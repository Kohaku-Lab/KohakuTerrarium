"""Integration test for ``kohakuterrarium.browser``: one creature browsing a real site.

The comprehensive usage example for ``browser/``: a real :class:`Agent` built
from an on-disk creature config that enables the ``browser`` plugin, driven by
a ``ScriptedLLM`` that speaks native tool calls, against a loopback fixture
site, through real Chromium and the session's real filtering proxy. The LLM is
the only seam; the plugin is toggled through ``agent_toggle_plugin``, the
helper behind the Studio and HTTP plugin switches.

Skips when Playwright or its Chromium is missing; any other launch failure
fails. Every await is bounded, so a hang fails with its own message.
"""

import asyncio
import base64
import json
import re
import threading
from collections import deque

import pytest

from kohakuterrarium.browser.errors import BrowserClosedError, BrowserUnavailableError
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.llm.base import NativeToolCall
from kohakuterrarium.terrarium.creature.ops import agent_toggle_plugin
from kohakuterrarium.testing.llm import ScriptedLLM
from tests.helpers.browser_site import FixtureSite, browsers_env, process_alive

pytestmark = [pytest.mark.xdist_group("browser"), pytest.mark.timeout(120)]

LIMIT_S = 30.0
EXIT_BOUND_S = 20.0
WRAP_UP = "Done."
REPORT = b"team,seats\nkohaku,5\n"
UPLOAD = b"kohaku upload payload\n"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
CONFIG = """\
name: browser_tester
system_prompt_file: system.md
controller:
  model: scripted
  tool_format: native
input:
  type: none
output:
  type: stdout
plugins:
  - name: browser
    options:
      allow_loopback: true
      allow_hosts: ["127.0.0.1:{port}", "169.254.169.254"]
      action_timeout_s: 8
      nav_timeout_s: 15
"""


class StepLLM(ScriptedLLM):
    """A ``ScriptedLLM`` speaking native tool calls: queued steps, then a wrap-up reply."""

    def __init__(self) -> None:
        super().__init__([WRAP_UP])
        self.queue: deque[NativeToolCall | str] = deque()
        self.last_tool_calls: list[NativeToolCall] = []

    async def chat(self, messages, **kwargs):
        self.call_log.append(self._normalize_messages(messages))
        self.call_count += 1
        step = self.queue.popleft() if self.queue else WRAP_UP
        if isinstance(step, NativeToolCall):
            self.last_tool_calls = [step]
            yield ""
        else:
            self.last_tool_calls = []
            yield step


async def within(awaitable, what: str, limit: float = LIMIT_S):
    """Await ``awaitable``, failing the test when it takes longer than ``limit``."""
    try:
        return await asyncio.wait_for(awaitable, limit)
    except asyncio.TimeoutError:
        pytest.fail(f"{what} did not finish within {limit:g} s")


class Creature:
    """Drives the agent one tool call per turn, as a model would, and reads what it saw."""

    def __init__(self, agent: Agent, llm: StepLLM) -> None:
        self.agent = agent
        self.llm = llm
        self.steps = 0
        self.parts: list[dict] = []

    def script(self, tool: str, **args) -> str:
        """Queue one native call of ``tool`` and its wrap-up; the prompt that starts it."""
        self.steps += 1
        call = NativeToolCall(f"call_{self.steps}", tool, json.dumps(args))
        self.llm.queue.extend([call, WRAP_UP])
        return f"step {self.steps}: {tool}"

    async def call(self, tool: str, **args) -> str:
        """Run one turn whose only tool call is ``tool``; the tool result the model saw."""
        prompt = self.script(tool, **args)
        seen = len(self.llm.call_log)
        result = await within(self.agent.run(prompt), prompt)
        assert result.ok, (prompt, result.error)
        follow_up = self.llm.call_log[seen + 1]
        message = next(m for m in reversed(follow_up) if m.get("role") == "tool")
        content = message["content"]
        self.parts = content if isinstance(content, list) else []
        if isinstance(content, str):
            return content
        return "\n".join(p.get("text", "") for p in content if p.get("type") == "text")


def ref_of(text: str, role: str, name: str) -> str:
    match = re.search(rf'{role} "{re.escape(name)}"[^\n]*?\[ref=(\w+)\]', text)
    assert match, f"no {role} {name!r} in:\n{text}"
    return match.group(1)


def page_line(text: str, index: int = 2) -> str:
    """Line ``index`` of a result: header, wrapper opening, then the page's own lines."""
    return text.split("\n")[index]


async def browser_pids(plugin) -> list[int]:
    """Every process of the plugin's running browser, the browser process first."""
    pids = await within(plugin.session.process_ids(), "process_ids")
    assert pids and process_alive(pids[0])
    return pids


async def survivors(pids: list[int], bound: float = EXIT_BOUND_S) -> list[int]:
    """The pids still running after waiting up to ``bound`` seconds for them to exit."""
    alive = list(pids)
    for _ in range(int(bound / 0.1)):
        alive = [pid for pid in alive if process_alive(pid)]
        if not alive:
            break
        await asyncio.sleep(0.1)
    return alive


def serve_slowly(arrived: threading.Event, release: threading.Event):
    """A route that reports its request arrived, then answers once released."""

    def route(handler) -> None:
        arrived.set()
        release.wait(LIMIT_S)
        handler.send_response(200)
        handler.send_header("Content-Length", "2")
        handler.end_headers()
        handler.wfile.write(b"ok")

    return route


@pytest.fixture
def sites():
    """The fixture site the creature tests, and a loopback listener it must never reach."""
    site, listener = FixtureSite(), FixtureSite()
    yield site, listener
    site.close()
    listener.close()


class TestBrowserIntegration:
    """``browser/`` driven the way a creature drives it: plugin, tools, session, proxy."""

    async def test_creature_browses_a_site_end_to_end(
        self, sites, tmp_path, monkeypatch
    ):
        """navigate with page text → snapshot → batch + delta → dialog → new tab
        with its text → upload → download → screenshot → eval → blocked egress →
        stale ref → interrupt → blocked redirect → runtime disable / enable → stop,
        with no Chromium left behind."""
        pytest.importorskip("playwright.async_api")
        for name, value in browsers_env().items():
            monkeypatch.setenv(name, value)
        site, listener = sites
        arrived, release = threading.Event(), threading.Event()
        site.routes["/api/slow"] = serve_slowly(arrived, release)
        for path in ("/api/profile", "/api/delete", "/api/upload", "/api/helpful"):
            site.file(path, b"ok", "text/plain")
        site.html(
            "/leave.html",
            '<!doctype html><title>Leave</title><a href="/out">Archive copy</a>',
        )
        site.redirect("/out", listener.url("/archive"))
        site.file(
            "/report.csv",
            REPORT,
            "text/csv",
            **{"Content-Disposition": 'attachment; filename="report.csv"'},
        )
        creature_dir, work = tmp_path / "creature", tmp_path / "work"
        creature_dir.mkdir()
        work.mkdir()
        (creature_dir / "system.md").write_text("You test web apps in a browser.\n")
        (creature_dir / "config.yaml").write_text(CONFIG.format(port=site.port))
        llm = StepLLM()
        agent = await Agent.build(creature_dir, llm=llm, pwd=str(work), io="headless")
        plugin = agent.plugins.get_plugin("browser")
        creature = Creature(agent, llm)
        pids: list[int] = []
        try:
            await within(agent.start(), "agent.start")
            # Navigate: the browser launches on the first call.
            app = site.url(f"/workflow.html?probe={listener.port}")
            page = await creature.call("browser_navigate", url=app)
            if plugin.session.state != "ready":
                try:
                    await within(plugin.session.navigate(app), "direct navigate")
                except BrowserUnavailableError as exc:
                    pytest.skip(f"no Chromium for Playwright: {exc}")
                pytest.fail(f"the first navigation failed:\n{page}")
            assert page.startswith(f"[t1] {app}\n<page_content")
            assert 'trust="untrusted"' in page.split("\n")[1]
            assert site.hits["/workflow.html"] == 1
            assert '- heading "Team settings" [level=1]' in page
            assert "Page text left out" not in page

            # Snapshot: refs for every control the workflow uses.
            snapshot = await creature.call("browser_snapshot")
            refs = {
                name: ref_of(snapshot, role, name)
                for role, name in (
                    ("textbox", "Display name"),
                    ("combobox", "Theme"),
                    ("checkbox", "Accept terms"),
                    ("button", "Save profile"),
                    ("button", "Read notice"),
                    ("button", "Dismiss notice"),
                    ("button", "Delete account"),
                    ("link", "Open help"),
                    ("button", "Attachment"),
                    ("link", "Download report"),
                    ("button", "Load slow data"),
                    ("button", "Run probes"),
                )
            }
            assert ref_of(page, "button", "Save profile") == refs["Save profile"]

            # One batch fills and submits the form; the result is a delta.
            saved = await creature.call(
                "browser_act",
                actions=[
                    {"kind": "type", "ref": refs["Display name"], "text": "Grace"},
                    {"kind": "select", "ref": refs["Theme"], "values": ["Dark"]},
                    {"kind": "check", "ref": refs["Accept terms"]},
                    {"kind": "click", "ref": refs["Save profile"]},
                    {"kind": "wait", "text": "Saved profile for Grace"},
                ],
            )
            assert site.bodies["/api/profile"] == [
                b'{"name":"Grace","theme":"Dark","terms":true}'
            ]
            assert "Actions: 5 of 5 done" in saved
            assert "status: Saved profile for Grace" in saved
            assert "Page changes" in saved and "Page now" not in saved

            # A confirm dialog: pending until answered, nothing sent before.
            deleting = await creature.call(
                "browser_act",
                actions=[{"kind": "click", "ref": refs["Delete account"]}],
            )
            assert "dialog pending: confirm" in deleting.split("\n")[0]
            assert 'Dialog pending: confirm "Delete the account?"' in deleting
            blocked_by_dialog = await creature.call("browser_snapshot")
            assert "is waiting for an answer" in blocked_by_dialog
            assert "/api/delete" not in site.hits
            await creature.call(
                "browser_act",
                actions=[
                    {"kind": "dialog", "accept": True},
                    {"kind": "wait", "text": "Account deleted"},
                ],
            )
            assert site.hits["/api/delete"] == 1

            # A link that opens a new tab, then work inside that tab.
            help_page = await creature.call(
                "browser_act", actions=[{"kind": "click", "ref": refs["Open help"]}]
            )
            assert f"[t2] {site.url('/help.html')}" in help_page
            assert "Tab t2 now:" in help_page
            assert "Was this page helpful?" in help_page
            tabs = await creature.call("browser_tabs", action="list")
            assert "* [t2] " + site.url("/help.html") in tabs
            await creature.call(
                "browser_act",
                actions=[
                    {
                        "kind": "click",
                        "ref": ref_of(help_page, "button", "Mark helpful"),
                    },
                    {"kind": "wait", "text": "Thanks for the feedback"},
                ],
            )
            assert site.hits["/api/helpful"] == 1
            closed = await creature.call("browser_tabs", action="close", tab="t2")
            assert "Tabs (1, active t1)" in closed

            # Upload a file from the working directory.
            (work / "notes.txt").write_bytes(UPLOAD)
            await creature.call(
                "browser_act",
                actions=[
                    {"kind": "upload", "ref": refs["Attachment"], "files": [0]},
                    {"kind": "wait", "text": "Uploaded notes.txt"},
                ],
                files=["notes.txt"],
            )
            assert site.bodies["/api/upload"] == [UPLOAD]

            # Download: saved under the working directory and reported.
            downloaded = await creature.call(
                "browser_act",
                actions=[{"kind": "click", "ref": refs["Download report"]}],
            )
            saved_report = work / "browser-downloads" / "report.csv"
            assert saved_report.read_bytes() == REPORT
            assert f"Downloaded: report.csv → {saved_report}" in downloaded

            # Screenshot: the model receives the image, and the file is saved.
            shot = await creature.call("browser_screenshot", path="shots/page.png")
            png = (work / "shots" / "page.png").read_bytes()
            assert png.startswith(PNG_MAGIC)
            assert "Screenshot: 1280x800 CSS px, image/png" in shot
            images = [p for p in creature.parts if p.get("type") == "image_url"]
            assert len(images) == 1
            url = images[0]["image_url"]["url"]
            assert base64.b64decode(url.split("base64,", 1)[1]) == png

            # Script reads page state the earlier batch stored.
            evaluated = await creature.call(
                "browser_eval", expression="() => localStorage.getItem('profile')"
            )
            assert json.loads(json.loads(page_line(evaluated))) == {
                "name": "Grace",
                "theme": "Dark",
                "terms": True,
            }

            # Egress: a metadata address and an unlisted loopback port are blocked.
            probes = await creature.call(
                "browser_act",
                actions=[
                    {"kind": "click", "ref": refs["Run probes"]},
                    {"kind": "wait", "text": "Probes finished"},
                ],
            )
            assert "Blocked 2 requests:" in probes
            assert (
                "169.254.169.254:80 (address 169.254.169.254 is link-local)" in probes
            )
            assert f"127.0.0.1:{listener.port} (" in probes
            stolen = await creature.call("browser_navigate", url=listener.url("/steal"))
            assert "127.0.0.1 is not in allow_hosts" in stolen
            assert listener.total_hits() == 0

            # A ref whose element is gone fails at once, not after the timeout.
            await creature.call(
                "browser_act",
                actions=[{"kind": "click", "ref": refs["Dismiss notice"]}],
            )
            stale = await creature.call(
                "browser_act",
                actions=[{"kind": "click", "ref": refs["Read notice"]}],
            )
            ref = refs["Read notice"]
            assert f"click {ref}: failed" in stale
            assert f"ref {ref} is no longer on the page" in stale
            assert "timed out" not in stale

            # Interrupt a batch while its request is in flight; the next call works.
            prompt = creature.script(
                "browser_act",
                actions=[
                    {"kind": "click", "ref": refs["Load slow data"]},
                    {"kind": "wait", "text": "Slow data loaded"},
                ],
            )
            turn = asyncio.create_task(agent.run(prompt))
            assert await within(asyncio.to_thread(arrived.wait, LIMIT_S), "slow call")
            agent.interrupt()
            interrupted = await within(turn, "the interrupted turn")
            assert (
                interrupted.status == "interrupted" and interrupted.interrupted_by_user
            )
            llm.queue.clear()
            release.set()
            after = await creature.call("browser_snapshot", query="status")
            assert after.startswith(f"[t1] {app}\n<page_content")

            # A click that redirects to a forbidden host reads as a blocked navigation.
            leave = await creature.call("browser_navigate", url=site.url("/leave.html"))
            left_out = await creature.call(
                "browser_act",
                actions=[
                    {"kind": "click", "ref": ref_of(leave, "link", "Archive copy")}
                ],
            )
            target = f"127.0.0.1:{listener.port}"
            assert (
                f"1. click {ref_of(leave, 'link', 'Archive copy')}: failed — it led to "
                f"{target}, which the browser network policy blocked"
            ) in left_out
            assert "the page did not load" in left_out
            assert "Navigation blocked: " in left_out and "Navigated:" not in left_out
            assert "Blocked 1 request" not in left_out
            assert listener.total_hits() == 0

            # Disable at runtime: the browser exits and the tools refuse.
            pids = await browser_pids(plugin)
            await agent_toggle_plugin(agent, "browser", False)
            left = await survivors(pids)
            assert left == [], f"alive after disable: {left} of {pids}"
            assert "browser_snapshot" not in {
                schema.name for schema in agent.controller._get_native_tool_schemas()
            }
            with pytest.raises(BrowserClosedError):
                await plugin.runtime_services(None)["browser"].prepare(None)
            refused = await creature.call("browser_snapshot")
            assert "Tool not registered: browser_snapshot" in refused

            # Enable again: a fresh browser serves the next call.
            await agent_toggle_plugin(agent, "browser", True)
            fresh = await creature.call("browser_navigate", url=app)
            assert fresh.startswith(f"[t1] {app}\n<page_content")
            assert site.hits["/workflow.html"] == 2
            old, pids = pids, await browser_pids(plugin)
            assert set(pids).isdisjoint(old)
        finally:
            release.set()
            await within(agent.stop(), "agent.stop")
        # Stop: no Chromium process of this test survives.
        left = await survivors(pids)
        assert left == [], f"alive after stop: {left} of {pids}"
        with pytest.raises(BrowserClosedError):
            await plugin.runtime_services(None)["browser"].prepare(None)
        assert plugin.session.state == "closed"
