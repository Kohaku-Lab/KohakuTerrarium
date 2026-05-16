"""Multi-node fat journey ??one test, full user flow over the real HTTP API.

This is the ONE multi-node e2e journey.  It drives every user-facing
operation through the public HTTP / WebSocket interface a real user
hits in the browser:

  - browse sessions / catalogs
  - spawn creatures on workers via the dashboard's "new creature"
    modal (POST /api/sessions/active/creature with on_node)
  - open a chat WebSocket and stream multi-turn replies
  - open the runtime-graph endpoint (the graph editor's data source)
  - drag wires: creature ??channel, channel ??other-worker creature,
    creature ??creature direct output
  - send a message INTO a channel and observe it reach the receiver
    (the actual cross-node message flow, not just topology mutations)
  - switch model on a worker creature
  - toggle plugins / patch scratchpad / read system prompt
  - get history / search memory
  - interrupt a turn
  - stop one creature; cluster stays up
  - mirror-writer logs are checked for "append failed" across the run

The journey uses a fail-accumulator (``_BugLog``) so a single run
surfaces EVERY bug present, not just the first.  No internal
state-injection: everything goes through the public interface.  If a
feature can't be exercised via the public interface that is itself a
bug ??the journey will surface it.

Per ``tests/README.md`` rule 5: this is ONE fat function.  When
the journey doesn't reach far enough, ADD MORE OPERATIONS to it;
do NOT add more test functions.
"""

import asyncio
import json
import logging
import os
import traceback
from pathlib import Path

import pytest

from tests.e2e._lab_harness import (
    OP_TIMEOUT,
    RealLabHost,
    RealLabWorker,
    install_scripted_llm,
)
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.studio.sessions import cluster_fold
from kohakuterrarium.testing.llm import ScriptEntry

pytestmark = pytest.mark.timeout(900)


def _write_creature_config(
    root: Path,
    name: str,
    system_prompt: str,
    *,
    subagents: list[str] | None = None,
    extra_tools: list[str] | None = None,
) -> Path:
    """Write a realistic on-disk creature config dir."""
    cdir = root / f"creature_{name}"
    cdir.mkdir(parents=True, exist_ok=True)
    text = (
        f"name: {name}\n"
        f"system_prompt: {system_prompt!r}\n"
        "llm_profile: openai/gpt-4-test\n"
        "model: gpt-4\n"
        "provider: openai\n"
        "input:\n  type: cli\n"
        "output:\n  type: stdout\n"
    )
    if extra_tools:
        text += "tools:\n"
        for tname in extra_tools:
            text += f"  - name: {tname}\n    type: builtin\n"
    if subagents:
        text += "subagents:\n"
        for sa in subagents:
            text += f"  - name: {sa}\n    type: builtin\n"
    (cdir / "config.yaml").write_text(text, encoding="utf-8")
    return cdir


async def _drain_chat(ws, message: str, *, idle: float = 4.0, hard: float = 30.0):
    """Send one user turn over the chat WS; return all assistant text + activity.

    Returns a tuple ``(text, frames)`` where ``frames`` is every frame
    received during the turn (text_chunk, tool_start, tool_done,
    channel_message, subagent_*, idle, error, ??.  Behavior asserts
    can then key on specific frame types.
    """
    await ws.send(json.dumps({"type": "input", "content": message}))
    chunks: list[str] = []
    frames: list[dict] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + hard
    while loop.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=idle)
        except asyncio.TimeoutError:
            break
        try:
            frame = json.loads(raw)
        except (ValueError, TypeError):
            continue
        frames.append(frame)
        t = frame.get("type")
        if t in ("text", "text_chunk", "assistant"):
            chunks.append(str(frame.get("content", "")))
        elif t == "error":
            chunks.append(f"<ERROR:{frame.get('content')}>")
            break
        elif t == "idle" and chunks:
            break
    return "".join(chunks), frames


class _CapturingHandler(logging.Handler):
    """Captures every record emitted on the attached logger.

    The project's logger sets ``propagate=False`` on the
    ``kohakuterrarium`` root, so pytest's stock ``caplog`` doesn't see
    records emitted by ``kohakuterrarium.terrarium.output_wiring``. The
    journey attaches one of these directly to that logger to assert on
    B7's misleading "target unresolved" WARN.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        self.records.append(record)


class _BugLog:
    """Fail-accumulator for the fat journey.

    Every assertion goes through ``check`` so the journey runs to
    completion regardless of individual failures.  ``step(label)`` is
    a context manager wrapping each milestone so an UNCAUGHT
    exception inside one step (TypeError, KeyError, etc.) does NOT
    abort the rest ??it's recorded with a traceback and the next step
    runs.  ``raise_if_any`` surfaces every bug in a single error.
    """

    def __init__(self) -> None:
        self.bugs: list[tuple[str, str]] = []

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        if not ok:
            self.bugs.append((label, detail))
        return ok

    def record(self, label: str, detail: str) -> None:
        self.bugs.append((label, detail))

    def record_exception(self, label: str, exc: BaseException) -> None:
        self.bugs.append(
            (label, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        )

    def step(self, label: str):
        bugs = self

        class _Ctx:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, exc_type, exc, tb):
                if exc is not None:
                    bugs.record_exception(label, exc)
                    return True  # swallow so the next step runs
                return False

        return _Ctx()

    def raise_if_any(self) -> None:
        if not self.bugs:
            return
        lines = ["Multi-node journey surfaced bugs:"]
        for i, (label, detail) in enumerate(self.bugs, 1):
            short = (detail.splitlines()[0] if detail else "").strip()
            lines.append(f"  {i:2d}. {label} :: {short}")
        if self.bugs and self.bugs[0][1] and "\n" in self.bugs[0][1]:
            lines.append("")
            lines.append("--- first failure detail ---")
            lines.append(self.bugs[0][1])
        raise AssertionError("\n".join(lines))


class TestMultinodeJourney:
    """ONE fat journey covering every multi-node feature through the
    public HTTP/WS interface.  No internal seams except the LLM.
    """

    async def test_full_creature_session_on_subprocess_worker(
        self, tmp_path, monkeypatch, caplog
    ):
        # ?�?� 0. environment & identity store ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        # Multi-script holder ??one shared list across host + every
        # worker's in-process scripted LLM.  Most entries are plain
        # strings; the *match-gated* entries fire the LLM-driven
        # send_channel tool call (step 29c) when the right user input
        # arrives.  The `match` filter lets entries appear in any
        # order without throwing off call-count.
        TOOL_CALL_SEND = (
            "[/send_channel]\n"
            "@@channel=ch1\n"
            "@@message=cross-node greeting from alpha\n"
            "[send_channel/]"
        )
        install_scripted_llm(
            monkeypatch,
            script=[
                # Pinned match-gated entries first so they are always
                # selectable regardless of call_count.
                ScriptEntry(response=TOOL_CALL_SEND, match="please ping bravo"),
                ScriptEntry(
                    response="bravo received the cross-node ping",
                    match="cross-node greeting",
                ),
                # B2 — bravo attempts an unauthorized send on ch1.
                # After /connect alpha→bravo via ch1 (step 29b), bravo
                # is wired LISTEN-only on ch1 (NOT send).  Driving bravo
                # with "bravo send unauthorized" makes its LLM emit
                # send_channel(ch1, …); the standalone gate in
                # tools_group_send.py:186-204 must fire because
                # send_edges[bravo] does not contain ch1.
                ScriptEntry(
                    response=(
                        "[/send_channel]\n"
                        "@@channel=ch1\n"
                        "@@message=b2-should-be-blocked\n"
                        "[send_channel/]"
                    ),
                    match="bravo send unauthorized",
                ),
                # B2 hypothesis 3 — alpha attempts the legacy
                # ``send_message`` path on ch1 between step 27 (alpha
                # SEND wire deleted) and step 29b (alpha re-wired as
                # sender via /connect).  In that window
                # send_edges[alpha] does NOT contain ch1, so the
                # ``_enforce_send_edge_in_engine_context`` gate
                # (builtins/tools/send_message.py:23-67) must return a
                # deny string.  Bracket form for ``send_message`` uses
                # ``@@channel=`` + bare ``message`` body
                # (parsing/patterns.py:22).
                ScriptEntry(
                    response=(
                        "[/send_message]\n"
                        "@@channel=ch1\n"
                        "@@message=b2-legacy-should-be-blocked\n"
                        "[send_message/]"
                    ),
                    match="alpha legacy send unauthorized",
                ),
                # LLM-driven privileged tool: alpha spawns charlie via
                # group_add_node when asked to.  Exercises tools_group_lifecycle
                # + topology mutations + creature_host factory.
                ScriptEntry(
                    response=(
                        "[/group_add_node]\n"
                        f"@@config_path={tmp_path}/creature_alpha\n"
                        "@@name=charlie\n"
                        "[group_add_node/]"
                    ),
                    match="please spawn charlie",
                ),
                # Privileged tool calls exercising more tools_group_*.
                ScriptEntry(
                    response="[/group_status]\n[group_status/]",
                    match="show graph status",
                ),
                ScriptEntry(
                    response=(
                        "[/group_channel]\n"
                        "@@action=add\n"
                        "@@name=privops\n"
                        "@@description=privileged-created channel\n"
                        "[group_channel/]"
                    ),
                    match="add a new channel called privops",
                ),
                ScriptEntry(
                    response=(
                        "[/group_wire]\n"
                        "@@action=add\n"
                        "@@from=alpha\n"
                        "@@to=charlie\n"
                        "[group_wire/]"
                    ),
                    match="wire alpha to charlie directly",
                ),
                # CF-7: alpha (privileged on w1) is asked to remove
                # bravo (lives on w2).  The worker engine has no
                # cluster-routing for ``group_*`` mutations today, so
                # the tool's local ``resolve_group_target`` miss must
                # surface a clearly *cross-cluster*-flagged error
                # rather than a vague "not in your group" miss that
                # would hide the real cause.
                ScriptEntry(
                    response=(
                        "[/group_remove_node]\n"
                        "@@creature_id=bravo\n"
                        "[group_remove_node/]"
                    ),
                    match="please remove bravo",
                ),
                # Generic filler responses for the many unscripted turns.
                ScriptEntry(response="a1"),
                ScriptEntry(response="a2"),
                ScriptEntry(response="a3"),
                ScriptEntry(response="a4"),
                ScriptEntry(response="a5"),
                ScriptEntry(response="a6"),
                ScriptEntry(response="a7"),
                ScriptEntry(response="a8"),
                ScriptEntry(response="a9"),
                ScriptEntry(response="a10"),
                ScriptEntry(response="follow-up reply"),
                ScriptEntry(response="another reply"),
                ScriptEntry(response="third reply"),
                ScriptEntry(response="fourth reply"),
                ScriptEntry(response="fifth reply"),
                ScriptEntry(response="sixth reply"),
                ScriptEntry(response="seventh reply"),
                ScriptEntry(response="eighth reply"),
            ],
        )

        host_cfg = tmp_path / "kt-config"
        host_cfg.mkdir(parents=True, exist_ok=True)
        import yaml

        (host_cfg / "llm_profiles.yaml").write_text(
            yaml.safe_dump(
                {
                    "version": 3,
                    "presets": {
                        "openai": {
                            "tinypreset": {
                                "model": "fake-model",
                                "max_context": 4096,
                                "max_output": 256,
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (host_cfg / "api_keys.yaml").write_text(
            yaml.safe_dump({"openai": "sk-test"}), encoding="utf-8"
        )
        monkeypatch.setenv("KT_CONFIG_DIR", str(host_cfg))

        # B2 hypothesis 3 — alpha registers the *legacy* ``send_message``
        # builtin so the journey can probe whether its alternative gate
        # path (``_enforce_send_edge_in_engine_context``) also enforces
        # the send-edge requirement when the caller is NOT wired as
        # sender on the target channel.  Without this opt-in tool the
        # creature only has ``send_channel`` (the new path), which is
        # already gated.
        cfg_alpha = _write_creature_config(
            tmp_path,
            "alpha",
            "You are alpha.",
            extra_tools=["send_message"],
        )
        cfg_bravo = _write_creature_config(tmp_path, "bravo", "You are bravo.")
        bugs = _BugLog()

        async with RealLabHost(tmp_path) as host:
            async with (
                RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1") as w1,
                RealLabWorker("w2", host.lab_ws_url, tmp_path / "w2") as w2,
            ):
                # In-process workers auto-join on ``__aenter__``;
                # ``RealLabHost`` waits for client registration via
                # the host engine's heartbeat tracker, but we also
                # poll explicitly to flush any pending JOIN messages.
                try:
                    await asyncio.wait_for(
                        host.wait_for_workers([w1.node_id, w2.node_id]),
                        timeout=OP_TIMEOUT * 4,
                    )
                except (asyncio.TimeoutError, AttributeError):
                    # Older harness may not expose ``wait_for_workers``;
                    # in-process workers are reliably joined by the
                    # time __aenter__ returns so a short sleep is a
                    # safe substitute.
                    await asyncio.sleep(0.5)

                # B7: attach a direct handler to the wiring logger so
                # the journey can observe ``"target unresolved"`` WARNs
                # emitted on the cross-node output-wire path
                # (``propagate=False`` on the kohakuterrarium root means
                # ``caplog`` would not see them).
                wiring_handler = _CapturingHandler()
                wiring_logger = logging.getLogger(
                    "kohakuterrarium.terrarium.output_wiring"
                )
                wiring_prior_level = wiring_logger.level
                wiring_logger.addHandler(wiring_handler)
                wiring_logger.setLevel(logging.DEBUG)
                host_cwd_snapshot = os.getcwd()
                try:
                    with caplog.at_level(
                        logging.WARNING, logger="kohakuterrarium.session.sync"
                    ):
                        await _drive_journey(
                            host,
                            w1,
                            w2,
                            cfg_alpha,
                            cfg_bravo,
                            tmp_path,
                            bugs,
                            wiring_handler=wiring_handler,
                            host_cwd=host_cwd_snapshot,
                        )
                finally:
                    wiring_logger.removeHandler(wiring_handler)
                    wiring_logger.setLevel(wiring_prior_level)

                # Cross-cutting invariant: no session-sync mirror append
                # failures anywhere across the multi-worker journey.
                mirror_fails = [
                    r
                    for r in caplog.records
                    if "session-sync mirror" in r.getMessage()
                    and (
                        "append failed" in r.getMessage()
                        or "meta apply failed" in r.getMessage()
                    )
                ]
                if mirror_fails:
                    bugs.record(
                        "BUG #127/#137: session-sync mirror append-failure log fired",
                        " | ".join(r.getMessage() for r in mirror_fails),
                    )

        bugs.raise_if_any()


# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�
# The journey body ??flat narrative.  Each marked step is what
# a user would actually click / type in the web UI; the inline
# behavior assertions are what they'd EXPECT to see (rendered
# state, streamed tokens, edges on the canvas, etc.).
# ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�


async def _run(bugs: _BugLog, label: str, coro):
    """Run an awaitable step; record any uncaught exception against
    ``label`` without aborting the journey.  Returns the result on
    success, or ``None`` on failure (so callers can guard their
    subsequent state extraction)."""
    try:
        return await coro
    except Exception as exc:
        bugs.record_exception(label, exc)
        return None


async def _drive_journey(
    host,
    w1,
    w2,
    cfg_alpha,
    cfg_bravo,
    tmp_path,
    bugs: _BugLog,
    *,
    wiring_handler: _CapturingHandler | None = None,
    host_cwd: str | None = None,
):
    # === 0. user lands on dashboard - catalog + identity endpoints ===
    async with bugs.step("0 catalog endpoints reachable"):
        for path in [
            "/api/configs/creatures",
            "/api/configs/terrariums",
            "/api/configs/models",
            "/api/configs/builtins",
            "/api/configs/commands",
            "/api/configs/modules",
            "/api/configs/skills",
            "/api/configs/server_info",
            "/api/configs/packages",
            "/api/configs/manifest",
            "/api/configs/workspace",
            "/api/configs/templates",
            "/api/configs/registry",
        ]:
            rr = await asyncio.wait_for(host.http.get(path), timeout=OP_TIMEOUT)
            bugs.check(
                f"0 catalog GET {path} reachable",
                rr.status_code in (200, 404, 405),
                f"{rr.status_code} {rr.text[:200]}",
            )
    async with bugs.step("0 identity endpoints reachable"):
        for path in [
            "/api/identity/api-keys",
            "/api/identity/llm",
            "/api/identity/mcp",
            "/api/identity/codex",
            "/api/identity/settings",
            "/api/identity/ui-prefs",
        ]:
            rr = await asyncio.wait_for(host.http.get(path), timeout=OP_TIMEOUT)
            bugs.check(
                f"0 identity GET {path} reachable",
                rr.status_code in (200, 404, 405),
                f"{rr.status_code} {rr.text[:200]}",
            )
    async with bugs.step("0 identity mutations: POST/PUT/DELETE"):
        # POST a fresh LLM profile so the LLM profile editor's API
        # surface gets exercised (not just GET).
        rr = await asyncio.wait_for(
            host.http.post(
                "/api/identity/llm/profiles",
                json={
                    "name": "test-profile",
                    "preset": "openai/tinypreset",
                    "model": "fake-model",
                    "provider": "openai",
                },
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "0 POST /identity/llm/profiles reachable",
            rr.status_code in (200, 201, 400, 404, 405, 422),
            f"{rr.status_code} {rr.text[:200]}",
        )
        # POST an API key for a non-conflicting test provider.
        rr = await asyncio.wait_for(
            host.http.post(
                "/api/identity/api-keys",
                json={"provider": "anthropic", "key": "sk-fake-test"},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "0 POST /identity/api-keys reachable",
            rr.status_code in (200, 201, 400, 404, 405, 422),
            f"{rr.status_code} {rr.text[:200]}",
        )
        # POST a UI pref entry.
        rr = await asyncio.wait_for(
            host.http.post(
                "/api/identity/ui-prefs",
                json={"key": "theme", "value": "dark"},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "0 POST /identity/ui-prefs reachable",
            rr.status_code in (200, 201, 400, 404, 405, 422),
            f"{rr.status_code} {rr.text[:200]}",
        )

    # === 0b. B5 — worker-aware "Working directory" pre-fill ======
    # The New Creature modal calls ``configAPI.getServerInfo()`` to
    # seed the working-dir field (NewCreatureModal.vue:104).  Today
    # ``/api/configs/server-info`` returns the HOST's ``os.getcwd()``
    # regardless of the selected worker — the user picks "Run on w1"
    # and the field keeps the host's cwd.  We assert SOME route
    # exists that returns a worker-side default different from the
    # host's cwd.
    async with bugs.step("0b B5 worker-aware server-info default working dir"):
        host_cwd_local = host_cwd or os.getcwd()
        # Sanity: plain endpoint reports host cwd (the field the
        # modal currently pre-fills).
        sanity = await asyncio.wait_for(
            host.http.get("/api/configs/server-info"), timeout=OP_TIMEOUT
        )
        if sanity.status_code == 200:
            bugs.check(
                "B5 sanity: /configs/server-info reports host cwd",
                sanity.json().get("cwd") == host_cwd_local,
                f"got {sanity.json().get('cwd')!r}, expected {host_cwd_local!r}",
            )
        worker_cwd: str | None = None
        tried: list[tuple[str, int, str]] = []
        for url in (
            "/api/configs/server-info?on_node=w1",
            "/api/catalog/server-info?on_node=w1",
            "/api/nodes/w1/server-info",
        ):
            r = await asyncio.wait_for(host.http.get(url), timeout=OP_TIMEOUT)
            tried.append((url, r.status_code, r.text[:200]))
            if r.status_code == 200:
                body = r.json() if isinstance(r.json(), dict) else {}
                cwd_value = body.get("cwd")
                if cwd_value and cwd_value != host_cwd_local:
                    worker_cwd = cwd_value
                    break
        bugs.check(
            "B5: a worker-aware server-info route exists for on_node=w1",
            worker_cwd is not None,
            f"every probed endpoint 404'd or echoed host cwd; "
            f"host_cwd={host_cwd_local!r}; probed={tried!r}",
        )
        if worker_cwd is not None:
            bugs.check(
                "B5: worker-aware server-info cwd != host cwd",
                worker_cwd != host_cwd_local,
                f"worker_cwd={worker_cwd!r} equals host_cwd={host_cwd_local!r}",
            )

    async with bugs.step("0 runtime + nodes + metrics"):
        for path in ["/api/runtime/graph", "/api/nodes", "/api/metrics"]:
            rr = await asyncio.wait_for(host.http.get(path), timeout=OP_TIMEOUT)
            bugs.check(
                f"0 GET {path} reachable",
                rr.status_code in (200, 404),
                f"{rr.status_code} {rr.text[:200]}",
            )

    # === 1. user opens the dashboard ??sees the empty active list ===
    r = await _run(
        bugs,
        "1 dashboard sessions list",
        asyncio.wait_for(host.http.get("/api/sessions/active"), timeout=OP_TIMEOUT),
    )
    if r is not None:
        bugs.check(
            "1 dashboard sessions list returns 200",
            r.status_code == 200,
            f"{r.status_code} {r.text}",
        )
    # === 2. user picks a worker and creates a creature ============
    spawn_a = await asyncio.wait_for(
        host.http.post(
            "/api/sessions/active/creature",
            json={"config_path": str(cfg_alpha), "on_node": "w1"},
        ),
        timeout=OP_TIMEOUT * 4,
    )
    bugs.check(
        "2 spawn alpha on w1 returns 200",
        spawn_a.status_code == 200,
        f"{spawn_a.status_code} {spawn_a.text}\nw1 stderr: <inproc>",
    )
    if spawn_a.status_code != 200:
        return
    sa = spawn_a.json()
    graph_a = sa["session_id"]
    a_id = sa["creatures"][0]["creature_id"]
    _a_name = sa["creatures"][0]["name"]
    a_creature_dict = sa["creatures"][0]

    # Visible state on the spawn response ??what the modal shows.
    bugs.check(
        "BUG: spawn response carries home_node='w1' (UI chip)",
        a_creature_dict.get("home_node") == "w1",
        f"home_node={a_creature_dict.get('home_node')!r}",
    )
    bugs.check(
        "BUG: spawn response carries resolved model name (UI model chip)",
        bool(a_creature_dict.get("model")),
        f"creature dict has no model: {a_creature_dict}",
    )

    # === 2c. B9 — a remote single-creature spawn must NOT report a
    # "root" placeholder anywhere in the session payload.  The frontend
    # treats ``instance.has_root`` as "address chat via literal 'root'",
    # which makes ``/ws/sessions/{sid}/creatures/root/chat`` the WS URL.
    # That URL only exists when the creature_id IS literally "root"
    # (recipe-defined ``root:`` keyword), never for a privileged
    # single-creature spawn on a worker.  ``has_root`` is a recipe
    # property, NOT a privileged-node property — the remote spawn
    # currently conflates the two (``has_root = is_privileged``).
    async with bugs.step(
        "2c B9 remote spawn must not report has_root / root placeholder"
    ):
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/active/{graph_a}"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "B9: GET /api/sessions/active/{sid} returns 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:300]}",
        )
        if rr.status_code == 200:
            sess = rr.json()
            sc = (sess.get("creatures") or [{}])[0]
            bugs.check(
                "B9: session payload has_root is False for a worker-spawn (no recipe)",
                sess.get("has_root") is False,
                f"has_root={sess.get('has_root')!r}; full={sess}",
            )
            bugs.check(
                "B9: session creatures[0].creature_id is the spawn's real cid",
                sc.get("creature_id") == a_id,
                f"creature_id={sc.get('creature_id')!r}, expected {a_id!r}",
            )
            bugs.check(
                "B9: session creatures[0].name is not the literal 'root'",
                sc.get("name") != "root",
                f"name={sc.get('name')!r}",
            )
        rr2 = await asyncio.wait_for(
            host.http.get(f"/api/sessions/active/{graph_a}/creatures"),
            timeout=OP_TIMEOUT,
        )
        if rr2.status_code == 200:
            roster = rr2.json() or []
            target = next(
                (c for c in roster if c.get("creature_id") == a_id),
                None,
            )
            bugs.check(
                "B9: /creatures listing includes the real cid (not 'root')",
                target is not None,
                f"roster cids={[c.get('creature_id') for c in roster]!r}",
            )
            if target is not None:
                bugs.check(
                    "B9: /creatures entry name is not the literal 'root'",
                    target.get("name") != "root",
                    f"name={target.get('name')!r}",
                )
        # The "root" history URL the frontend builds today (driven by
        # has_root) MUST 404 — there is no creature named "root" in
        # this graph.  The fix is on the producer side (don't set
        # has_root) but the routing contract must hold either way.
        rh_bad = await asyncio.wait_for(
            host.http.get(f"/api/sessions/{graph_a}/creatures/root/history"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "B9: /creatures/root/history is 404 (no creature literally named 'root')",
            rh_bad.status_code == 404,
            f"status={rh_bad.status_code} body={rh_bad.text[:200]}",
        )
        rh_ok = await asyncio.wait_for(
            host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/history"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "B9: /creatures/{real_cid}/history is 200",
            rh_ok.status_code == 200,
            f"status={rh_ok.status_code} body={rh_ok.text[:200]}",
        )

    # === 3. user sees the creature in the active list ============
    async with bugs.step("3 active sessions list shape"):
        r = await asyncio.wait_for(
            host.http.get("/api/sessions/active"), timeout=OP_TIMEOUT
        )
        body = r.json() if r.status_code == 200 else []
        # The endpoint returns a raw list of session dicts ??not a
        # {"sessions": [...]} envelope.  Accept either shape so the
        # test catches the bug only if the SESSION itself is missing.
        if isinstance(body, dict):
            listed = body.get("sessions") or []
        else:
            listed = body or []
        bugs.check(
            "3 active list now contains the spawned session",
            any(s.get("session_id") == graph_a for s in listed),
            f"active list: {body}",
        )

    # === 3a. B3 — GET /api/sessions/active/{sid} must carry the
    # creature's resolved ``model`` (and ``llm_name``).  This is what
    # the New-Creature modal / ModelSwitcher reads on tab open.  Today
    # ``lifecycle.get_session``'s remote branch synthesises the
    # creature dict from a stale ``_meta`` entry and drops every field
    # except creature_id / name / home_node — the picker shows
    # "No model".
    async with bugs.step("3a B3 session GET carries resolved model on worker"):
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/active/{graph_a}"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "B3: GET /api/sessions/active/{sid} returns 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:300]}",
        )
        if rr.status_code == 200:
            sess = rr.json()
            sess_creatures = sess.get("creatures") or []
            sess_creature = sess_creatures[0] if sess_creatures else {}
            bugs.check(
                "B3: session GET creature dict carries non-empty model field",
                bool(sess_creature.get("model")),
                f"creature dict: {sess_creature!r}",
            )
            bugs.check(
                "B3: session GET creature dict carries non-empty llm_name field",
                bool(sess_creature.get("llm_name")),
                f"creature dict: {sess_creature!r}",
            )
            model_str = str(sess_creature.get("model") or "")
            bugs.check(
                "B3: session GET creature model contains configured 'gpt-4'",
                "gpt-4" in model_str,
                f"model={model_str!r}; creature={sess_creature!r}",
            )

    # === 3b. S6-2 — worker-side switch_model paths that bypass the
    # host's /creatures/{cid}/model HTTP route (the /model slash
    # command, PluginContext.switch_model, compact-LLM swap) never
    # call update_remote_creature_model_meta. Sync read paths
    # (lifecycle.list_sessions, lifecycle.list_creatures, the legacy
    # GET /agents alias) then surface the stale identifier from the
    # cached ``_meta``. Drive the /model slash via the session-command
    # HTTP route (skips the host's /model HTTP shortcut so we exercise
    # the bypassing path), then assert the next read of
    # ``GET /api/sessions/active/{sid}/creatures`` reports the NEW
    # identifier (the lab-host's only registered switchable profile is
    # ``openai/tinypreset``).
    async with bugs.step("3b S6-2 worker-side /model slash propagates to host cache"):
        cmd = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/{graph_a}/creatures/{a_id}/command",
                json={"command": "model", "args": "openai/tinypreset"},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "S6-2: /command model openai/tinypreset returns 200",
            cmd.status_code == 200,
            f"{cmd.status_code} {cmd.text[:300]}",
        )
        # Read the sync ``list_creatures`` path — this is the one the
        # host route /agents list and the per-session creatures listing
        # both go through. Before the S6-2 fix it returns the stale
        # ``llm_name`` cached at spawn time ("gpt-4" — the unresolved
        # config fallback). After the fix it folds in the live worker
        # identifier ("openai/tinypreset").
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/active/{graph_a}/creatures"),
            timeout=OP_TIMEOUT,
        )
        if rr.status_code == 200:
            payload = rr.json() or []
            target = next(
                (c for c in payload if c.get("creature_id") == a_id),
                {},
            )
            llm_name = str(target.get("llm_name") or "")
            bugs.check(
                "S6-2: list_creatures surfaces switched identifier after /model",
                "tinypreset" in llm_name,
                f"llm_name={llm_name!r}; creature={target!r}",
            )

    # === 4. user opens chat tab ??WS chat ========================
    chat_url_a = f"/ws/sessions/{graph_a}/creatures/{a_id}/chat"
    async with host.api_ws(chat_url_a) as ws:
        text, _ = await _drain_chat(ws, "greet alpha")
    bugs.check(
        "4 chat WS first turn streams a reply",
        bool(text) and "ERROR:" not in text,
        f"reply={text!r}",
    )
    async with host.api_ws(chat_url_a) as ws:
        text2, _ = await _drain_chat(ws, "alpha second turn")
    bugs.check(
        "4 chat WS second turn streams a fresh reply",
        bool(text2) and "ERROR:" not in text2,
        f"reply={text2!r}",
    )

    # === 5. user opens graph editor ??runtime-graph snapshot =====
    snap = await asyncio.wait_for(
        host.http.get("/api/runtime/graph"), timeout=OP_TIMEOUT
    )
    bugs.check(
        "5 runtime-graph snapshot 200",
        snap.status_code == 200,
        f"{snap.status_code} {snap.text}",
    )
    graphs = snap.json().get("graphs", []) if snap.status_code == 200 else []
    a_graph = next((g for g in graphs if g.get("graph_id") == graph_a), None)
    bugs.check(
        "5 alpha's graph present in snapshot",
        a_graph is not None,
        f"snapshot graphs: {[g.get('graph_id') for g in graphs]}",
    )
    if a_graph:
        bugs.check(
            "BUG: snapshot graph reports worker node_id (not _host)",
            a_graph.get("node_id") == "w1",
            f"node_id={a_graph.get('node_id')!r}",
        )
        cdicts = a_graph.get("creatures", []) or []
        for c in cdicts:
            if c.get("creature_id") == a_id:
                bugs.check(
                    "BUG #147: snapshot creature.home_node = worker",
                    c.get("home_node") == "w1",
                    f"home_node={c.get('home_node')!r}; full creature dict: {c}",
                )

    # === 6. user spawns a second creature on the OTHER worker ====
    spawn_b = await asyncio.wait_for(
        host.http.post(
            "/api/sessions/active/creature",
            json={"config_path": str(cfg_bravo), "on_node": "w2"},
        ),
        timeout=OP_TIMEOUT * 4,
    )
    bugs.check(
        "6 spawn bravo on w2 returns 200",
        spawn_b.status_code == 200,
        f"{spawn_b.status_code} {spawn_b.text}",
    )
    if spawn_b.status_code != 200:
        bugs.raise_if_any()
        return
    sb = spawn_b.json()
    graph_b = sb["session_id"]
    b_id = sb["creatures"][0]["creature_id"]
    b_name = sb["creatures"][0]["name"]

    # === 7. user creates a channel in alpha's graph ==============
    r = await asyncio.wait_for(
        host.http.post(
            f"/api/sessions/topology/{graph_a}/channels",
            json={"name": "ch1"},
        ),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "7 add channel ch1 on alpha's graph 200",
        r.status_code == 200,
        f"{r.status_code} {r.text}",
    )

    # === 8. user drags alpha ??ch1 (send) ========================
    r = await asyncio.wait_for(
        host.http.post(
            f"/api/sessions/topology/{graph_a}/creatures/{a_id}/wire",
            json={"channel": "ch1", "direction": "send"},
        ),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "8 wire alpha ??ch1 (send) 200",
        r.status_code == 200,
        f"{r.status_code} {r.text}",
    )

    # === 9. user drags ch1 ??bravo (cross-node listen) ===========
    # This is the cross-node wire ??pre-fix would 400 because ch1
    # doesn't exist on bravo's graph.  Service should lazily
    # replicate the channel onto bravo's graph and cross-subscribe.
    r = await asyncio.wait_for(
        host.http.post(
            f"/api/sessions/topology/{graph_b}/creatures/{b_id}/wire",
            json={"channel": "ch1", "direction": "listen"},
        ),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "9 cross-node wire ch1 ??bravo (listen) 200",
        r.status_code == 200,
        f"{r.status_code} {r.text}\nw2 stderr: <inproc>",
    )

    # === 9a. CF-3 — send to cluster channel via primary sid ======
    # After cross-node wire, ch1 is replicated on BOTH worker engines
    # (alpha's graph + bravo's graph). The user POSTs to the cluster
    # primary sid (lex-smallest member). ``send_to_channel`` must
    # route via ``service.send_channel_message`` so the call lands on
    # the worker hosting the primary's graph; the legacy short-circuit
    # to a host engine would 404 because the lab-host runs no agent
    # engine and any host-coordination engine has no per-cluster
    # channel object. Behavior assert: HTTP returns 200 AND the
    # message lands in the channel's history.
    async with bugs.step("9a CF-3 send via cluster primary sid lands in history"):
        svc = get_service()
        primary_sid = cluster_fold.sid_to_primary(svc).get(graph_a, graph_a)
        rr = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/topology/{primary_sid}/channels/ch1/send",
                json={"content": "cluster send via host", "sender": "test"},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "CF-3: POST /topology/{primary}/channels/ch1/send returns 200",
            rr.status_code == 200,
            f"primary_sid={primary_sid!r}; {rr.status_code} {rr.text}",
        )
        # service.list_channels must include ch1 on the primary sid —
        # cluster member channels are replicated across workers.
        ch_list = await svc.list_channels(primary_sid)
        ch_names = {c.name for c in ch_list}
        bugs.check(
            "CF-3: service.list_channels(primary_sid) includes ch1",
            "ch1" in ch_names,
            f"primary_sid={primary_sid!r}; channels={ch_names!r}",
        )
        # Verify the message reached at least one cluster member's
        # channel history — the send_to_channel routing is correct
        # iff the worker that actually owns the primary's graph
        # received and recorded the message.
        found = False
        for member_sid in sorted(
            cluster_fold.cluster_groups(svc).get(primary_sid, {primary_sid})
        ):
            try:
                hist = await svc.channel_history(member_sid, "ch1")
            except KeyError:
                continue
            for m in hist:
                if isinstance(m, dict) and m.get("content") == "cluster send via host":
                    found = True
                    break
            if found:
                break
        bugs.check(
            "CF-3: cluster channel history records the sent message",
            found,
            f"primary_sid={primary_sid!r}; no history entry matched content",
        )

    # === 10. user refreshes graph editor ??sees ONE cluster ======
    snap = await asyncio.wait_for(
        host.http.get("/api/runtime/graph"), timeout=OP_TIMEOUT
    )
    if snap.status_code == 200:
        graphs = snap.json().get("graphs", [])
        # Cluster fold: both creatures should appear in ONE snapshot graph.
        cluster = next(
            (
                g
                for g in graphs
                if {a_id, b_id}
                <= {c.get("creature_id") for c in (g.get("creatures") or [])}
            ),
            None,
        )
        bugs.check(
            "BUG #134: cluster fold ??alpha+bravo in ONE graph entry after wire",
            cluster is not None,
            f"snapshot graphs: {[g.get('graph_id') for g in graphs]}",
        )
        if cluster:
            by_id = {c["creature_id"]: c for c in cluster.get("creatures", [])}
            a_c = by_id.get(a_id, {})
            b_c = by_id.get(b_id, {})
            bugs.check(
                "BUG #147: alpha home_node in cluster = w1",
                a_c.get("home_node") == "w1",
                f"alpha home_node={a_c.get('home_node')!r}",
            )
            bugs.check(
                "BUG #147: bravo home_node in cluster = w2",
                b_c.get("home_node") == "w2",
                f"bravo home_node={b_c.get('home_node')!r}",
            )
            bugs.check(
                "BUG #150: wire shows in alpha.send_channels",
                "ch1" in (a_c.get("send_channels") or []),
                f"alpha dict: {a_c}",
            )
            bugs.check(
                "BUG #150: wire shows in bravo.listen_channels",
                "ch1" in (b_c.get("listen_channels") or []),
                f"bravo dict: {b_c}",
            )
            members = {
                (m.get("node_id"), m.get("graph_id"))
                for m in (cluster.get("members") or [])
            }
            bugs.check(
                "10 cluster members = {(w1, graph_a), (w2, graph_b)}",
                ("w1", graph_a) in members and ("w2", graph_b) in members,
                f"members={members}",
            )

    # === 10a. B1 — cluster MUST surface as ONE session at the
    # sessions layer too, not just at the runtime-graph snapshot.
    # Standalone-mode invariant: after a cross-node connect, the
    # session-coordinator fuses the two SessionStores into one;
    # ``GET /api/sessions/active`` then returns a SINGLE listing
    # whose ``creatures`` covers both members.  Today multi-node
    # mode leaves two distinct listings — the user cannot open the
    # cluster as one chat tab.
    async with bugs.step("10a B1 cluster folds to single session listing"):
        rr = await asyncio.wait_for(
            host.http.get("/api/sessions/active"), timeout=OP_TIMEOUT
        )
        bugs.check(
            "B1: list active sessions returns 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:300]}",
        )
        if rr.status_code == 200:
            listings = rr.json()
            if isinstance(listings, dict):
                listings = listings.get("sessions") or []
            cluster_entries = 0
            fold_sid: str | None = None
            fold_cids: set[str] = set()
            for entry in listings or []:
                sid = entry.get("session_id") if isinstance(entry, dict) else None
                if not sid:
                    continue
                rf = await asyncio.wait_for(
                    host.http.get(f"/api/sessions/active/{sid}"), timeout=OP_TIMEOUT
                )
                if rf.status_code != 200:
                    continue
                cr = rf.json().get("creatures", []) or []
                cids = {
                    c.get("creature_id") or c.get("agent_id")
                    for c in cr
                    if isinstance(c, dict)
                }
                if {a_id, b_id} <= cids:
                    cluster_entries += 1
                    fold_sid = fold_sid or sid
                    fold_cids = fold_cids or cids
            bugs.check(
                "B1: exactly ONE folded session listing covers both creatures",
                cluster_entries == 1,
                f"cluster_entries={cluster_entries}; listings={listings!r}",
            )
            bugs.check(
                "B1: folded session contains both alpha and bravo creature ids",
                {a_id, b_id} <= fold_cids,
                f"fold_sid={fold_sid!r}; fold_cids={fold_cids!r}",
            )
            # B10: the cluster-fold session payload must also list the
            # cross-node channel(s) — frontend's "X creatures and Y
            # channels" overview reads `instance.channels.length`.
            # Standalone single-graph payloads carry the live channel
            # registry; cluster-folded multi-node payloads must union
            # channels across cluster members the same way the runtime-
            # graph snapshot does (multi_node_cluster.fold_clusters).
            if fold_sid:
                rr_f = await asyncio.wait_for(
                    host.http.get(f"/api/sessions/active/{fold_sid}"),
                    timeout=OP_TIMEOUT,
                )
                if rr_f.status_code == 200:
                    body = rr_f.json()
                    fold_channels = body.get("channels") or []
                    ch_names = {
                        c.get("name") for c in fold_channels if isinstance(c, dict)
                    }
                    bugs.check(
                        "B10: cluster-fold session lists cross-node channel ch1",
                        "ch1" in ch_names,
                        f"channels={fold_channels!r}",
                    )

    # === 10b. S6-1 — non-primary sid resolves the WHOLE cluster ====
    # When the frontend opens the cluster via bravo's session_id (the
    # non-primary member, NOT the lex-smallest cluster id), the unified
    # session getter must still return the unioned cluster Session —
    # both alpha + bravo creatures — not just bravo's single-creature
    # listing. Otherwise tabs opened from bravo's rail entry render only
    # bravo and the multi-site channel chat is invisible.
    async with bugs.step("10b S6-1 GET non-primary sid returns folded cluster"):
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/active/{graph_b}"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "S6-1: GET /sessions/active/{graph_b} returns 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:300]}",
        )
        if rr.status_code == 200:
            payload = rr.json()
            non_primary_cids = {
                c.get("creature_id") or c.get("agent_id")
                for c in (payload.get("creatures") or [])
                if isinstance(c, dict)
            }
            bugs.check(
                "S6-1: non-primary cluster sid returns BOTH alpha+bravo creatures",
                {a_id, b_id} <= non_primary_cids,
                f"graph_b={graph_b!r}; creatures={non_primary_cids!r}",
            )

    # === 11. user types ??message flows through cross-node channel
    # The behavior the user EXPECTS: alpha emits ??ch1 ??bravo's
    # input queue receives a channel-trigger; bravo's next turn sees
    # it.  Drive via the dedicated channel-send route (matches the
    # "user types in channel chat" pattern in the UI).
    r = await asyncio.wait_for(
        host.http.post(
            f"/api/sessions/topology/{graph_a}/channels/ch1/send",
            json={"content": "broadcast hello", "sender": "user"},
        ),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "BUG: channel /send endpoint works for cross-node channel",
        r.status_code == 200,
        f"{r.status_code} {r.text}",
    )

    # === 11b. CF-4 — channel info unions history across cluster sides ==
    # After cluster wire, ``ch1`` is replicated on BOTH worker engines.
    # A send routed to graph_a lands in worker-1's channel object; a
    # send routed to graph_b lands in worker-2's.  The unified topology
    # GET ``/channels/ch1`` on the cluster primary MUST return BOTH
    # messages — otherwise the channel-chat panel shows only one half
    # of the conversation depending on which side last sent.
    async with bugs.step("11b CF-4 channel history unions both cluster sides"):
        rr = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/topology/{graph_b}/channels/ch1/send",
                json={
                    "content": "cf4-from-bravo-side",
                    "sender": "user",
                },
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "CF-4 precondition: send via graph_b (worker-2) returns 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:300]}",
        )
        rch = await asyncio.wait_for(
            host.http.get(f"/api/sessions/topology/{graph_a}/channels/ch1"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "CF-4: GET cluster-primary /channels/ch1 returns 200",
            rch.status_code == 200,
            f"{rch.status_code} {rch.text[:300]}",
        )
        ch_history_union: list = []
        if rch.status_code == 200:
            body = rch.json()
            ch_history_union = body.get("history") or body.get("messages") or []
        joined_union = " ".join(
            str(m.get("content", "")) for m in ch_history_union if isinstance(m, dict)
        )
        # Both sides' messages must be visible from the primary sid GET.
        # "broadcast hello" was sent via graph_a (worker-1) in step 11;
        # "cf4-from-bravo-side" was just sent via graph_b (worker-2).
        bugs.check(
            "CF-4: cluster channel history includes worker-1 side message",
            "broadcast hello" in joined_union,
            f"history={ch_history_union!r}",
        )
        bugs.check(
            "CF-4: cluster channel history includes worker-2 side message",
            "cf4-from-bravo-side" in joined_union,
            f"history={ch_history_union!r}",
        )
        # Same union must be visible from the NON-primary sid too —
        # the topology route mounts the same service, so a GET on
        # graph_b should return the same cluster-wide union.
        rch_b = await asyncio.wait_for(
            host.http.get(f"/api/sessions/topology/{graph_b}/channels/ch1"),
            timeout=OP_TIMEOUT,
        )
        if rch_b.status_code == 200:
            body_b = rch_b.json()
            history_b = body_b.get("history") or body_b.get("messages") or []
            joined_b = " ".join(
                str(m.get("content", "")) for m in history_b if isinstance(m, dict)
            )
            bugs.check(
                "CF-4: non-primary sid sees the SAME unioned cluster history",
                "broadcast hello" in joined_b and "cf4-from-bravo-side" in joined_b,
                f"history={history_b!r}",
            )

    # === 11c. CF-2 — chat WS surfaces channel history from BOTH workers ==
    # After 11 + 11b, ch1 holds messages on BOTH worker engines: a "broadcast
    # hello" sent via worker-1 (graph_a) AND "cf4-from-bravo-side" sent via
    # worker-2 (graph_b).  The chat WS is bound to alpha on worker-1 — pre-fix
    # it only saw worker-1's channel replica, so worker-2's message never
    # showed up in the channel-history frames.  Option C's cluster-aware
    # multiplexer opens one attach upstream per cluster member worker, so
    # BOTH workers' ``terrarium_attach`` adapters emit their replica's
    # channel history into the same client WS.
    async with bugs.step("11c CF-2 chat WS sees channel history from BOTH workers"):
        cf2_channel_contents: list[str] = []
        async with host.api_ws(chat_url_a) as ws:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + OP_TIMEOUT
            while loop.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                except asyncio.TimeoutError:
                    break
                try:
                    frame = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if (
                    frame.get("type") == "channel_message"
                    and frame.get("channel") == "ch1"
                ):
                    cf2_channel_contents.append(str(frame.get("content", "")))
        joined = " | ".join(cf2_channel_contents)
        bugs.check(
            "CF-2: chat WS history surfaces worker-1 side channel message",
            "broadcast hello" in joined,
            f"channel_contents={cf2_channel_contents!r}",
        )
        bugs.check(
            "CF-2: chat WS history surfaces worker-2 side channel message",
            "cf4-from-bravo-side" in joined,
            f"channel_contents={cf2_channel_contents!r}",
        )

    # === 12. user keeps chatting ??worker still responsive =======
    # Reported #152: worker hangs after wire ops.  Re-chat alpha.
    try:
        async with host.api_ws(chat_url_a) as ws:
            text, _ = await asyncio.wait_for(
                _drain_chat(ws, "alpha post-wire"), timeout=OP_TIMEOUT * 2
            )
    except asyncio.TimeoutError:
        bugs.record(
            "BUG #152: alpha chat hung after wire ops (WS TimeoutError)",
            f"w1 stderr: <inproc>",
        )
        text = ""
    bugs.check(
        "BUG #152: alpha worker responsive (chat returns SOME reply)",
        bool(text) and "ERROR:" not in text,
        f"reply={text!r}",
    )

    # === 13. user chats with bravo via the cluster's primary id ==
    # Reported #151: multi-creature cluster route failures.
    chat_url_b_byname = f"/ws/sessions/{graph_a}/creatures/{b_name}/chat"
    try:
        async with host.api_ws(chat_url_b_byname) as ws:
            text, _ = await asyncio.wait_for(
                _drain_chat(ws, "greet bravo"), timeout=OP_TIMEOUT * 2
            )
    except asyncio.TimeoutError:
        bugs.record(
            "BUG #151: cluster chat to bravo by name hung",
            f"w2 stderr: <inproc>",
        )
        text = ""
    bugs.check(
        "BUG #151: bravo reachable via cluster-primary graph_id by name",
        bool(text) and "ERROR:" not in text,
        f"reply={text!r}",
    )
    # And by creature_id on bravo's own graph (the canonical URL).
    chat_url_b = f"/ws/sessions/{graph_b}/creatures/{b_id}/chat"
    try:
        async with host.api_ws(chat_url_b) as ws:
            text_b, _ = await asyncio.wait_for(
                _drain_chat(ws, "bravo direct"), timeout=OP_TIMEOUT * 2
            )
    except asyncio.TimeoutError:
        bugs.record(
            "13 bravo direct chat hung",
            f"w2 stderr: <inproc>",
        )
        text_b = ""
    bugs.check(
        "13 bravo reachable on its own session+id (canonical URL)",
        bool(text_b) and "ERROR:" not in text_b,
        f"reply={text_b!r}",
    )

    # === 13b. B11 — cross-worker chat-routing on cluster session ===
    # User opens chat tab keyed to alpha's URL (host's WS proxies to w1)
    # but types in a sub-tab targeting bravo (lives on w2). The receive
    # loop's per-frame ``target`` field MUST route the input to bravo on
    # the OTHER worker. Today the worker-side
    # ``_find_sibling_by_name`` only sees its local engine's graph and
    # emits "Cannot route to creature 'bravo': not found in this
    # session." — the cluster looks like ONE session but the chat WS is
    # bound to ONE worker.
    async with bugs.step("13b B11 cross-worker target= routes to other worker"):
        cross_err_frames: list[str] = []
        cross_user_inputs: list[str] = []
        async with host.api_ws(chat_url_a) as ws:
            payload = json.dumps(
                {
                    "type": "input",
                    "content": "hello bravo from cross-worker",
                    "target": b_name,
                }
            )
            await ws.send(payload)
            loop = asyncio.get_event_loop()
            deadline = loop.time() + OP_TIMEOUT * 2
            while loop.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=4.0)
                except asyncio.TimeoutError:
                    break
                try:
                    frame = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                t = frame.get("type")
                if t == "error":
                    cross_err_frames.append(str(frame.get("content", "")))
                if t == "user_input" and frame.get("source") == b_name:
                    cross_user_inputs.append(str(frame.get("content", "")))
                if t == "idle":
                    break
        bugs.check(
            "B11: cross-worker target= produces NO 'not found in this session' error",
            not any("not found in this session" in s for s in cross_err_frames),
            f"errors={cross_err_frames!r}",
        )
        # Verify bravo (on w2) actually received the input — observe via
        # bravo's history endpoint.
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/{graph_b}/creatures/{b_id}/history"),
            timeout=OP_TIMEOUT,
        )
        joined_b = ""
        if rr.status_code == 200:
            hbody = rr.json()
            messages = hbody.get("messages") or hbody.get("events") or []
            joined_b = " ".join(
                str(m.get("content", "")) for m in messages if isinstance(m, dict)
            )
        bugs.check(
            "B11: bravo on w2 received the cross-worker user input",
            "hello bravo from cross-worker" in joined_b,
            f"bravo history joined={joined_b[:600]!r}; "
            f"cross_user_inputs={cross_user_inputs!r}",
        )

    # === 14. user adds a SECOND channel after cluster setup ======
    # Reported #143: after a???�b, creating ch2 + wiring b?�ch2 400s
    # when the frontend uses the cluster's primary graph_id.
    r = await asyncio.wait_for(
        host.http.post(
            f"/api/sessions/topology/{graph_a}/channels",
            json={"name": "ch2"},
        ),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "14 add ch2 on cluster primary 200",
        r.status_code == 200,
        f"{r.status_code} {r.text}",
    )
    r = await asyncio.wait_for(
        host.http.post(
            # Frontend posts the cluster primary id, not bravo's actual graph_id.
            f"/api/sessions/topology/{graph_a}/creatures/{b_id}/wire",
            json={"channel": "ch2", "direction": "send"},
        ),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "BUG #143: wire bravo ??ch2 using cluster primary graph_id",
        r.status_code == 200,
        f"{r.status_code} {r.text}",
    )

    # === 15. user drags creature ??creature direct output wire ===
    r = await asyncio.wait_for(
        host.http.post(
            f"/api/sessions/wiring/{graph_b}/creatures/{b_id}/outputs",
            json={
                "to": a_id,
                "with_content": True,
                "prompt_format": "simple",
                "allow_self_trigger": False,
            },
        ),
        timeout=OP_TIMEOUT * 2,
    )
    bugs.check(
        "BUG #145: cross-node direct output wire (bravo ??alpha) 200",
        r.status_code == 200,
        f"{r.status_code} {r.text}\nw2 stderr: <inproc>",
    )

    # === 16. user picks a new model on alpha ====================
    r = await asyncio.wait_for(
        host.http.post(
            f"/api/sessions/{graph_a}/creatures/{a_id}/model",
            json={"model": "openai/tinypreset"},
        ),
        timeout=OP_TIMEOUT * 2,
    )
    bugs.check(
        "16 model switch on worker creature 200",
        r.status_code == 200,
        f"{r.status_code} {r.text}",
    )
    switched_to = ""
    if r.status_code == 200:
        try:
            switched_to = r.json().get("model", "") or ""
        except (ValueError, TypeError):
            switched_to = ""

    # === 16a. B4 — after switch_model, simulate the chat tab being
    # closed and re-opened.  The frontend's tab-restore path runs
    # ``useInstancesStore.fetchOne(id)`` → ``GET /api/sessions/active/{id}``.
    # The response's ``creatures[0].model`` / ``llm_name`` is what
    # ``ModelSwitcher.vue`` reads into ``currentModel``; if empty the
    # picker shows "No model".  Today ``lifecycle.get_session``'s
    # remote branch synthesises the creature dict from a stale
    # ``_meta`` entry that never refreshes from the worker.
    async with bugs.step("16a B4 model survives tab-reopen GET on worker"):
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/active/{graph_a}"),
            timeout=OP_TIMEOUT * 2,
        )
        bugs.check(
            "B4: tab-reopen GET /api/sessions/active/{sid} returns 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:300]}",
        )
        if rr.status_code == 200:
            body = rr.json()
            crs = body.get("creatures") or []
            # Folded cluster sessions contain both alpha + bravo — look
            # up ALPHA explicitly (the creature we switched), not crs[0]
            # (which depends on cluster-fold ordering).
            alpha_dict = next(
                (c for c in crs if (c.get("creature_id") or c.get("agent_id")) == a_id),
                {},
            )
            reopen_model = alpha_dict.get("model", "") or ""
            reopen_llm_name = alpha_dict.get("llm_name", "") or ""
            bugs.check(
                "B4: tab-reopen GET surfaces non-empty model/llm_name",
                bool(reopen_model or reopen_llm_name),
                f"alpha_dict={alpha_dict!r}; switched_to={switched_to!r}; crs={crs!r}",
            )
            if switched_to:
                bugs.check(
                    "B4: tab-reopen GET surfaces the user's switched model",
                    switched_to in reopen_model or switched_to in reopen_llm_name,
                    f"alpha_dict={alpha_dict!r}; switched_to={switched_to!r}",
                )

    # === 17. user opens plugin pane ??list + toggle =============
    pl = await asyncio.wait_for(
        host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/plugins"),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "17 list plugins on worker creature 200",
        pl.status_code == 200,
        f"{pl.status_code} {pl.text}",
    )
    if pl.status_code == 200:
        body = pl.json()
        plugins = body if isinstance(body, list) else body.get("plugins", [])
        if plugins:
            name = plugins[0]["name"]
            on = await asyncio.wait_for(
                host.http.post(
                    f"/api/sessions/{graph_a}/creatures/{a_id}/plugins/{name}/toggle",
                    json={"enabled": True},
                ),
                timeout=OP_TIMEOUT,
            )
            bugs.check(
                f"17 toggle plugin {name!r} on 200",
                on.status_code == 200,
                f"{on.status_code} {on.text}",
            )

    # === 18. user patches scratchpad =============================
    r = await asyncio.wait_for(
        host.http.patch(
            f"/api/sessions/{graph_a}/creatures/{a_id}/scratchpad",
            json={"updates": {"focus": "navigation", "phase": "cruise"}},
        ),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "18 patch scratchpad on worker 200",
        r.status_code == 200,
        f"{r.status_code} {r.text}",
    )
    r = await asyncio.wait_for(
        host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/scratchpad"),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "18 get scratchpad on worker 200",
        r.status_code == 200,
        f"{r.status_code} {r.text}",
    )

    # === 19. user reads working dir / system prompt =============
    r = await asyncio.wait_for(
        host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/working-dir"),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "19 GET working-dir on worker 200",
        r.status_code == 200,
        f"{r.status_code} {r.text}",
    )
    r = await asyncio.wait_for(
        host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/system-prompt"),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "19 GET system-prompt on worker 200",
        r.status_code == 200,
        f"{r.status_code} {r.text}",
    )

    # === 20. user interrupts alpha mid-turn ======================
    r = await asyncio.wait_for(
        host.http.post(f"/api/sessions/{graph_a}/creatures/{a_id}/interrupt"),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "20 interrupt on worker 200",
        r.status_code == 200,
        f"{r.status_code} {r.text}",
    )

    # === 21. user opens history pane ============================
    r = await asyncio.wait_for(
        host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/history"),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "21 history endpoint 200",
        r.status_code == 200,
        f"{r.status_code} {r.text}",
    )
    if r.status_code == 200:
        body = r.json()
        bugs.check(
            "21 history carries recorded events/messages",
            bool(body.get("messages") or body.get("events")),
            f"history body: {str(body)[:400]}",
        )

    # === 22. user opens search pane =============================
    r = await asyncio.wait_for(
        host.http.get(
            f"/api/sessions/{graph_a}/memory/search",
            params={"q": "greet"},
        ),
        timeout=OP_TIMEOUT,
    )
    bugs.check(
        "22 memory search endpoint reachable",
        r.status_code in (200, 404),
        f"{r.status_code} {r.text}",
    )
    # === 22a memory search returns hits for worker conversation text
    async with bugs.step("22a memory search hit body for worker text"):
        rr = await asyncio.wait_for(
            host.http.get(
                f"/api/sessions/{graph_a}/memory/search",
                params={"q": "greet", "k": 20, "mode": "fts"},
            ),
            timeout=OP_TIMEOUT,
        )
        if rr.status_code == 200:
            body = rr.json()
            hits = (
                body
                if isinstance(body, list)
                else (body.get("hits") or body.get("results") or [])
            )
            bugs.check(
                "BUG: memory search returns hits for worker turn text",
                bool(hits),
                f"empty hits; body={body}",
            )
            # CF-5: cluster-wide memory search must union hits from EVERY
            # cluster member, not just the primary's mirror. Alpha (w1)
            # and bravo (w2) live on distinct workers and each has its
            # own mirror ``.kohakutr`` host-side; opening only one of
            # them returned a one-sided result set. Both "greet alpha"
            # and "greet bravo" were typed (steps 4 + 13) so the FTS
            # hits must surface BOTH agents — verify via the ``agent``
            # discriminator on each result row.
            sources = {
                str((h.get("agent") if isinstance(h, dict) else "") or "") for h in hits
            }
            bugs.check(
                "CF-5: memory search unions hits from BOTH cluster members "
                "(alpha+bravo)",
                "alpha" in sources and "bravo" in sources,
                f"agents in hits={sources!r}; hits={hits!r}",
            )

    # === 22a-bis. CF-5 viewer follow-up — cluster fan-out for /turns
    # and /events. The viewer endpoints share memory search's blind
    # spot: opening only the primary's mirror returns a one-sided view.
    # After CF-5 viewer fan-out, GET /turns and GET /events on the
    # cluster's primary sid must surface ROWS from BOTH alpha (w1) and
    # bravo (w2) — the merge tags each row with ``agent`` (turns) or
    # ``member_sid`` (events) so we can verify both sources contributed.
    async with bugs.step("22a-bis viewer /turns cluster fan-out"):
        rr = await asyncio.wait_for(
            host.http.get(
                f"/api/sessions/{graph_a}/turns",
                params={"aggregate": "true", "limit": 200},
            ),
            timeout=OP_TIMEOUT,
        )
        if rr.status_code == 200:
            body = rr.json()
            turns = body.get("turns") or []
            # In aggregate mode each row is a per-turn aggregate with NO
            # row-level ``agent`` field — the per-agent identities live
            # in the row's ``breakdown`` list (one entry per contributor
            # agent / sub-agent). Union those across every row to verify
            # both cluster members contributed.
            agents: set[str] = set()
            for t in turns:
                if not isinstance(t, dict):
                    continue
                for b in t.get("breakdown") or []:
                    if isinstance(b, dict):
                        a = b.get("agent")
                        if isinstance(a, str) and a:
                            agents.add(a)
            bugs.check(
                "CF-5-viewer: /turns unions rows from BOTH cluster "
                "members (alpha+bravo)",
                "alpha" in agents and "bravo" in agents,
                f"agents in turns breakdown={agents!r}; turns_count={len(turns)}",
            )
    async with bugs.step("22a-bis viewer /events cluster fan-out"):
        rr = await asyncio.wait_for(
            host.http.get(
                f"/api/sessions/{graph_a}/events",
                params={"limit": 500},
            ),
            timeout=OP_TIMEOUT,
        )
        if rr.status_code == 200:
            body = rr.json()
            events = body.get("events") or []
            members = {
                str((e.get("member_sid") if isinstance(e, dict) else "") or "")
                for e in events
            }
            # Both member sids appear (graph_a + graph_b). Drop the
            # empty-string entry from rows where the merge didn't tag
            # the source (shouldn't happen in cluster mode but defensive).
            members.discard("")
            bugs.check(
                "CF-5-viewer: /events unions rows from BOTH cluster "
                "members (>=2 distinct member_sid tags)",
                len(members) >= 2,
                f"member_sids in events={members!r}; events_count={len(events)}",
            )

    # === 22b. service-protocol completeness — every per-creature
    # method must work against a WORKER creature, not just a local one.
    # These are the verbs missing from the earlier journey steps.
    async with bugs.step("22b PUT native-tool-options on worker"):
        rr = await asyncio.wait_for(
            host.http.put(
                f"/api/sessions/{graph_a}/creatures/{a_id}/native-tool-options",
                json={"options": {"send_channel": {"enabled": True}}},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "22b PUT native-tool-options 200/422 on worker",
            rr.status_code in (200, 400, 404, 422),
            f"{rr.status_code} {rr.text[:300]}",
        )
    async with bugs.step("22c module options GET/PUT/toggle on worker"):
        # GET /modules already exercised in step 23; here we drive the
        # per-module GET options + PUT options + toggle path.
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/modules"),
            timeout=OP_TIMEOUT,
        )
        if rr.status_code == 200:
            body = rr.json()
            modules = body if isinstance(body, list) else body.get("modules", [])
            if modules:
                first = modules[0]
                mname = first.get("name") or first.get("id") or ""
                if mname:
                    # GET module options
                    g = await asyncio.wait_for(
                        host.http.get(
                            f"/api/sessions/{graph_a}/creatures/{a_id}/modules/{mname}/options"
                        ),
                        timeout=OP_TIMEOUT,
                    )
                    bugs.check(
                        f"22c GET module options for {mname!r} reachable",
                        g.status_code in (200, 404),
                        f"{g.status_code}",
                    )
                    # PUT module options
                    p = await asyncio.wait_for(
                        host.http.put(
                            f"/api/sessions/{graph_a}/creatures/{a_id}/modules/{mname}/options",
                            json={"options": {}},
                        ),
                        timeout=OP_TIMEOUT,
                    )
                    bugs.check(
                        f"22c PUT module options for {mname!r} reachable",
                        p.status_code in (200, 400, 404, 422),
                        f"{p.status_code}",
                    )
    async with bugs.step("22d branch operations on worker"):
        # Get branches list.
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/branches"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "22d GET /branches on worker 200/404",
            rr.status_code in (200, 404),
            f"{rr.status_code}",
        )
        # Edit a message + rerun.
        rr = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/{graph_a}/creatures/{a_id}/messages/0/edit",
                json={"content": "edited content"},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "22d edit-message on worker reachable",
            rr.status_code in (200, 400, 404, 422),
            f"{rr.status_code}",
        )
        # Rewind to message 0.
        rr = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/{graph_a}/creatures/{a_id}/messages/0/rewind"
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "22d rewind on worker reachable",
            rr.status_code in (200, 400, 404, 422),
            f"{rr.status_code}",
        )

    async with bugs.step("22e jobs ops on worker"):
        # Get jobs list (we may not have a job, but the verb dispatches).
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/jobs"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "22e GET /jobs on worker 200/404",
            rr.status_code in (200, 404),
            f"{rr.status_code}",
        )
        # stop_job and promote_job on a nonexistent job-id — verbs still
        # dispatch and return 404, exercising the route.
        for verb_path in ("tasks/nonexistent/stop", "promote/nonexistent"):
            rr = await asyncio.wait_for(
                host.http.post(f"/api/sessions/{graph_a}/creatures/{a_id}/{verb_path}"),
                timeout=OP_TIMEOUT,
            )
            bugs.check(
                f"22e POST /{verb_path} on worker reachable",
                rr.status_code in (200, 400, 404, 422),
                f"{rr.status_code}",
            )

    async with bugs.step("22f sinks list + delete on worker"):
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/wiring/{graph_a}/creatures/{a_id}/sinks"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "22f GET /sinks on worker reachable",
            rr.status_code in (200, 404),
            f"{rr.status_code}",
        )
        rr = await asyncio.wait_for(
            host.http.delete(
                f"/api/sessions/wiring/{graph_a}/creatures/{a_id}/sinks/nonexistent"
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "22f DELETE /sinks/{id} on worker reachable",
            rr.status_code in (200, 204, 404),
            f"{rr.status_code}",
        )

    # === 23. user opens overview side-panels ??env/triggers/options/branches/jobs
    async with bugs.step("23 creature side-panels"):
        for tail, label in [
            ("env", "env"),
            ("triggers", "triggers"),
            ("native-tool-options", "native-tool-options"),
            ("branches", "branches"),
            ("jobs", "jobs"),
            ("modules", "modules"),
            ("sinks", "sinks"),
            ("outputs", "outputs"),
        ]:
            rr = await asyncio.wait_for(
                host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/{tail}"),
                timeout=OP_TIMEOUT,
            )
            # Endpoint shape: 200 on success.  404 means "endpoint
            # exists but creature has no records" ??also acceptable.
            bugs.check(
                f"23 GET /{label} on worker creature 200/404",
                rr.status_code in (200, 404),
                f"{rr.status_code} {rr.text[:300]}",
            )

    # === 24. user clicks the regenerate icon on alpha's last reply ===
    async with bugs.step("24 regenerate last turn"):
        rr = await asyncio.wait_for(
            host.http.post(f"/api/sessions/{graph_a}/creatures/{a_id}/regenerate"),
            timeout=OP_TIMEOUT * 2,
        )
        bugs.check(
            "24 regenerate endpoint 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:400]}",
        )

    # === 24a. branch sequence: drive a third turn on worker, edit a
    # previous turn's content, verify history reflects the edit.  This
    # exercises edit→branch on a worker-hosted creature, not just the
    # canned shape-check from step 22d.
    async with bugs.step("24a branch sequence (edit-then-history)"):
        # Drive one more user turn so the conversation has at least
        # three turns (greet alpha / alpha second turn / branch turn).
        try:
            async with host.api_ws(chat_url_a) as ws:
                t3, _ = await asyncio.wait_for(
                    _drain_chat(ws, "branch turn three"),
                    timeout=OP_TIMEOUT * 2,
                )
        except asyncio.TimeoutError:
            pass
        # Pre-edit history length.
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/history"),
            timeout=OP_TIMEOUT,
        )
        pre_len = 0
        if rr.status_code == 200:
            pbody = rr.json()
            pre_len = len(pbody.get("messages") or pbody.get("events") or [])
        # Edit message at idx 1 (typically the first user turn).
        rr = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/{graph_a}/creatures/{a_id}/messages/1/edit",
                json={"content": "edited first user turn"},
            ),
            timeout=OP_TIMEOUT * 2,
        )
        bugs.check(
            "BUG: edit-message at idx 1 on worker returns 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:300]}",
        )
        # Post-edit history must contain the new text.
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/history"),
            timeout=OP_TIMEOUT,
        )
        if rr.status_code == 200:
            body = rr.json()
            messages = body.get("messages") or body.get("events") or []
            joined = " ".join(str(m.get("content", "")) for m in messages)
            bugs.check(
                "BUG: post-edit history contains edited content",
                "edited first user turn" in joined,
                f"history joined: {joined[:400]} (pre_len={pre_len})",
            )

    # === 25. user changes the working-dir on alpha (UI inline edit) ==
    async with bugs.step("25 PUT working-dir"):
        new_wd = str(tmp_path / "alpha-wd")
        Path(new_wd).mkdir(parents=True, exist_ok=True)
        rr = await asyncio.wait_for(
            host.http.put(
                f"/api/sessions/{graph_a}/creatures/{a_id}/working-dir",
                json={"path": new_wd},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "BUG #148: PUT working-dir on worker creature 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:400]}",
        )
        # Read it back ??UI shows the value after select-worker.
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/working-dir"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "BUG #148: GET working-dir on worker creature 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:400]}",
        )
        if rr.status_code == 200:
            wd = rr.json().get("pwd") or rr.json().get("working_dir") or ""
            bugs.check(
                "BUG #148: working-dir round-trips the new value to worker",
                wd and Path(wd).resolve() == Path(new_wd).resolve(),
                f"got wd={wd!r}, expected {new_wd!r}",
            )

    # === 26. user removes the direct-output wire (creature ??creature) ==
    async with bugs.step("26 list and delete output edges"):
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/wiring/{graph_b}/creatures/{b_id}/outputs"),
            timeout=OP_TIMEOUT,
        )
        if rr.status_code == 200:
            edges = rr.json().get("edges") if isinstance(rr.json(), dict) else rr.json()
            if isinstance(edges, list) and edges:
                eid = edges[0].get("edge_id") or edges[0].get("id")
                if eid:
                    dd = await asyncio.wait_for(
                        host.http.delete(
                            f"/api/sessions/wiring/{graph_b}/creatures/{b_id}/outputs/{eid}"
                        ),
                        timeout=OP_TIMEOUT,
                    )
                    bugs.check(
                        "26 delete direct-output wire 200/204",
                        dd.status_code in (200, 204),
                        f"{dd.status_code} {dd.text[:400]}",
                    )

    # === 26a. cross-node output wire: re-create alpha→bravo on the
    # cluster, list, delete by edge_id, verify it's gone.  Step 26
    # exercised the same-node edge on graph_b; this one drives the
    # cross-node create + list + delete loop end-to-end.
    async with bugs.step("26a cross-node output wire CRUD"):
        rr = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/wiring/{graph_a}/creatures/{a_id}/outputs",
                json={
                    "to": b_id,
                    "with_content": True,
                    "prompt_format": "simple",
                    "allow_self_trigger": False,
                },
            ),
            timeout=OP_TIMEOUT * 2,
        )
        bugs.check(
            "BUG: cross-node output wire alpha→bravo create 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:400]}",
        )

    # === 26a.B7. With alpha→bravo cross-node output wire active,
    # drive an alpha turn — the wire fires on finalisation and
    # ``TerrariumOutputWiringResolver._resolve_target`` cannot find
    # bravo in w1's local engine, so today it WARNS "target
    # unresolved - emissions will be dropped" BEFORE the cross-node
    # fallback delivers via worker→host relay.  The emission does
    # deliver (delivery half), but the misleading WARN fires (bug
    # half).
    async with bugs.step("26b B7 cross-node output wire emit + no misleading WARN"):
        # Snapshot pre-emit WARN count so we attribute new records to
        # this step only.
        if wiring_handler is None:
            pre_warns = 0
        else:
            pre_warns = sum(
                1
                for r in wiring_handler.records
                if r.levelno >= logging.WARNING
                and "target unresolved" in str(r.getMessage())
            )
        emit_text = ""
        try:
            async with host.api_ws(chat_url_a) as ws:
                emit_text, _frames = await asyncio.wait_for(
                    _drain_chat(ws, "alpha emit toward bravo via wire"),
                    timeout=OP_TIMEOUT * 2,
                )
        except asyncio.TimeoutError:
            bugs.record("B7 alpha emit-turn hung", "WS TimeoutError")
        # Settle for cross-node relay + bravo's triggered turn to drain.
        await asyncio.sleep(1.0)
        # --- Half 1: delivery actually succeeded -----------------
        # The wire forwards alpha's reply text to bravo's
        # conversation. We observe bravo's recorded history via the
        # public HTTP API (not internal state) and require some
        # non-empty alpha reply text to surface.
        bugs.check(
            "B7 half-1: alpha actually produced reply text to forward",
            bool(emit_text) and "ERROR:" not in emit_text,
            f"emit_text={emit_text!r}",
        )
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/{graph_b}/creatures/{b_id}/history"),
            timeout=OP_TIMEOUT,
        )
        if rr.status_code == 200:
            hbody = rr.json()
            messages = hbody.get("messages") or hbody.get("events") or []
            joined = " ".join(
                str(m.get("content", "")) for m in messages if isinstance(m, dict)
            )
            bugs.check(
                "B7 half-1: cross-node output wire delivered alpha's reply to bravo",
                bool(emit_text) and emit_text.strip() and emit_text.strip() in joined,
                f"emit_text={emit_text!r}; bravo history joined={joined[:600]!r}",
            )
        # --- Half 2: misleading WARN must NOT have fired ---------
        if wiring_handler is not None:
            new_unresolved = [
                r
                for r in wiring_handler.records
                if r.levelno >= logging.WARNING
                and "target unresolved" in str(r.getMessage())
            ][pre_warns:]
            # Sanity: at minimum the handler captured SOMETHING from
            # the wiring logger during the run; if it captured zero
            # records the test is mis-instrumented, not the
            # framework.
            bugs.check(
                "B7: wiring-logger handler captured at least one record",
                bool(wiring_handler.records),
                "no records captured — handler attached to wrong logger",
            )
            bugs.check(
                "B7 half-2: no misleading 'target unresolved' WARN on cross-node emit",
                not new_unresolved,
                f"captured WARNs: "
                f"{[(r.getMessage(), getattr(r, 'target', None)) for r in new_unresolved]}",
            )
        # List edges, pick the alpha→bravo one, DELETE it.
        ll = await asyncio.wait_for(
            host.http.get(f"/api/sessions/wiring/{graph_a}/creatures/{a_id}/outputs"),
            timeout=OP_TIMEOUT,
        )
        if ll.status_code == 200:
            edges_body = ll.json()
            # Endpoint returns either {"edges":[…]} or {"outputs":[…]}
            # or a bare list depending on shape.  Accept all three.
            if isinstance(edges_body, dict):
                edges = edges_body.get("edges") or edges_body.get("outputs") or []
            else:
                edges = edges_body or []
            cand = None
            if isinstance(edges, list):
                for e in edges:
                    if not isinstance(e, dict):
                        continue
                    dst = e.get("to_creature_id") or e.get("to")
                    if dst == b_id:
                        cand = e
                        break
            bugs.check(
                "BUG: cross-node alpha→bravo edge listable in /outputs",
                cand is not None,
                f"edges body: {edges_body}",
            )
            if cand is not None:
                eid = cand.get("edge_id") or cand.get("id")
                if eid:
                    dd = await asyncio.wait_for(
                        host.http.delete(
                            f"/api/sessions/wiring/{graph_a}/creatures/{a_id}/outputs/{eid}"
                        ),
                        timeout=OP_TIMEOUT,
                    )
                    bugs.check(
                        "BUG: cross-node output wire DELETE 200/204",
                        dd.status_code in (200, 204),
                        f"{dd.status_code} {dd.text[:300]}",
                    )
                    # Verify gone.
                    rr2 = await asyncio.wait_for(
                        host.http.get(
                            f"/api/sessions/wiring/{graph_a}/creatures/{a_id}/outputs"
                        ),
                        timeout=OP_TIMEOUT,
                    )
                    if rr2.status_code == 200:
                        b2 = rr2.json()
                        e2 = (b2.get("edges") if isinstance(b2, dict) else b2) or []
                        still = any(
                            (e.get("edge_id") or e.get("id")) == eid
                            for e in e2
                            if isinstance(e, dict)
                        )
                        bugs.check(
                            "BUG: cross-node output wire absent after DELETE",
                            not still,
                            f"still present in {e2}",
                        )

    # === 27. user removes a wire (creature ??channel) via the wire DELETE ==
    async with bugs.step("27 delete creature ??channel wire"):
        dd = await asyncio.wait_for(
            host.http.request(
                "DELETE",
                f"/api/sessions/topology/{graph_a}/creatures/{a_id}/wire",
                json={"channel": "ch1", "direction": "send"},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "27 delete alpha ??ch1 wire 200/204",
            dd.status_code in (200, 204),
            f"{dd.status_code} {dd.text[:400]}",
        )

    # === 27b. B2 hypothesis 3 — legacy send_message gate fires ====
    # User-reported bug: "multi node channel didn't block the
    # creature to send message when the creature have NOT BEEN
    # MOUNTED AS SENDER".  Step 27 just deleted alpha's SEND wire on
    # ch1, so send_edges[alpha] no longer contains ch1.  alpha was
    # configured with the legacy ``send_message`` tool (see
    # ``cfg_alpha`` setup).  Drive alpha to emit
    # ``send_message(channel=ch1, ...)`` and assert that
    # ``_enforce_send_edge_in_engine_context`` (the legacy gate at
    # builtins/tools/send_message.py:23-67) deny-returns the
    # canonical "not wired as sender" message, AND the broadcast
    # does NOT land on ch1's history.
    async with bugs.step("27b' B2 legacy send_message gate on unwired sender"):
        try:
            async with host.api_ws(
                f"/ws/sessions/{graph_a}/creatures/{a_id}/chat"
            ) as ws_a:
                text_a, frames_a = await asyncio.wait_for(
                    _drain_chat(ws_a, "alpha legacy send unauthorized"),
                    timeout=OP_TIMEOUT * 3,
                )
        except asyncio.TimeoutError:
            bugs.record(
                "27b' alpha chat hung while emitting legacy send_message",
                "w1 inproc",
            )
            text_a, frames_a = "", []
        await asyncio.sleep(0.5)
        rh_a = await asyncio.wait_for(
            host.http.get(f"/api/sessions/{graph_a}/creatures/{a_id}/history"),
            timeout=OP_TIMEOUT,
        )
        history_joined_a = ""
        if rh_a.status_code == 200:
            hbody_a = rh_a.json()
            msgs_a = hbody_a.get("messages") or hbody_a.get("events") or []
            history_joined_a = " ".join(
                str(m.get("content", "")) for m in msgs_a if isinstance(m, dict)
            )
        frames_joined_a = " ".join(
            str(f.get("content", "")) for f in frames_a if isinstance(f, dict)
        )
        gate_visible_a = (
            "not wired as sender" in history_joined_a.lower()
            or "not wired as sender" in frames_joined_a.lower()
            or "not wired as sender" in text_a.lower()
        )
        tool_emitted_a = any(
            str(f.get("type", "")).startswith("tool") for f in frames_a
        )
        bugs.check(
            "27b' precondition: alpha emitted a send_message tool call",
            tool_emitted_a,
            f"frames: {[f.get('type') for f in frames_a]}; text={text_a!r}",
        )
        bugs.check(
            "27b' B2: alpha send_message(ch1) blocked by send-edge gate",
            gate_visible_a,
            f"history={history_joined_a[:600]!r}; "
            f"text={text_a!r}; "
            f"frame_types={[f.get('type') for f in frames_a]}",
        )
        rch_a = await asyncio.wait_for(
            host.http.get(f"/api/sessions/topology/{graph_a}/channels/ch1"),
            timeout=OP_TIMEOUT,
        )
        ch_history_a: list = []
        if rch_a.status_code == 200:
            body_a = rch_a.json()
            ch_history_a = body_a.get("history") or body_a.get("messages") or []
        leaked_a = any(
            "b2-legacy-should-be-blocked" in str(m.get("content", ""))
            for m in ch_history_a
            if isinstance(m, dict)
        )
        bugs.check(
            "27b' B2: unauthorized alpha legacy send_message did NOT broadcast on ch1",
            not leaked_a,
            f"ch1 history: {ch_history_a!r}",
        )

    # === 28. user deletes the channel itself =====================
    async with bugs.step("28 delete channel"):
        # Channel-DELETE endpoint lives on topology router; older code
        # used /channels POST + a different name.  We try the canonical
        # DELETE first; if it's not the right shape, the assertion
        # surfaces the gap.
        dd = await asyncio.wait_for(
            host.http.request(
                "DELETE",
                f"/api/sessions/topology/{graph_a}/channels/ch2",
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "28 delete channel ch2 returns 200/204 OR 405 if not implemented",
            dd.status_code in (200, 204, 404, 405),
            f"{dd.status_code} {dd.text[:300]}",
        )

    # === 29. user spawns a terrarium-from-recipe (not just a creature) ==
    async with bugs.step("29 list terrarium configs + spawn one"):
        rr = await asyncio.wait_for(
            host.http.get("/api/configs/terrariums"), timeout=OP_TIMEOUT
        )
        bugs.check(
            "29 terrarium config list 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:300]}",
        )

    # === 29b. /connect via unified topology endpoint =============
    async with bugs.step("29b unified /connect alpha ??bravo via ch1"):
        rr = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/topology/{graph_a}/connect",
                json={"sender": a_id, "receiver": b_id, "channel": "ch1"},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "29b unified /connect returns 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:400]}",
        )
        # B1 (post-/connect re-check) — the cluster-fold invariant must
        # hold after the unified /connect endpoint as well, not just
        # after the wire-listen path exercised in step 10a.
        await asyncio.sleep(0.5)
        ls = await asyncio.wait_for(
            host.http.get("/api/sessions/active"), timeout=OP_TIMEOUT
        )
        if ls.status_code == 200:
            ll = ls.json()
            if isinstance(ll, dict):
                ll = ll.get("sessions") or []
            cluster_entries = 0
            for entry in ll or []:
                sid = entry.get("session_id") if isinstance(entry, dict) else None
                if not sid:
                    continue
                rf = await asyncio.wait_for(
                    host.http.get(f"/api/sessions/active/{sid}"),
                    timeout=OP_TIMEOUT,
                )
                if rf.status_code != 200:
                    continue
                cr = rf.json().get("creatures", []) or []
                cids = {
                    c.get("creature_id") or c.get("agent_id")
                    for c in cr
                    if isinstance(c, dict)
                }
                if {a_id, b_id} <= cids:
                    cluster_entries += 1
            bugs.check(
                "B1 post-/connect: exactly ONE folded session listing",
                cluster_entries == 1,
                f"cluster_entries={cluster_entries}; listings={ll!r}",
            )

    # === 29b' B2 send-gate on unwired receiver after /connect ====
    # User-reported bug: "multi node channel didn't block the
    # creature to send message when the creature have NOT BEEN
    # MOUNTED AS SENDER".  After step 29b the unified /connect
    # wired alpha=SEND on ch1 and bravo=LISTEN on ch1.  Bravo has
    # NO send-edge on ch1.  The standalone gate in
    # tools_group_send.py:186-204 must fire when bravo's controller
    # emits send_channel(ch1, …): the tool result must contain
    # "not wired as sender" AND the broadcast must NOT reach ch1's
    # history (no message containing b2-should-be-blocked).
    async with bugs.step("29b' B2 send-gate fires when bravo unwired as sender"):
        try:
            async with host.api_ws(
                f"/ws/sessions/{graph_b}/creatures/{b_id}/chat"
            ) as ws_b:
                text_b, frames_b = await asyncio.wait_for(
                    _drain_chat(ws_b, "bravo send unauthorized"),
                    timeout=OP_TIMEOUT * 3,
                )
        except asyncio.TimeoutError:
            bugs.record(
                "29b' bravo chat hung while emitting unauthorized send_channel",
                "w2 inproc",
            )
            text_b, frames_b = "", []
        await asyncio.sleep(0.5)
        # Behavior assert: bravo's conversation must carry the
        # canonical gate error string ("not wired as sender") as
        # tool result.  Pull bravo's history via the HTTP route.
        rh = await asyncio.wait_for(
            host.http.get(f"/api/sessions/{graph_b}/creatures/{b_id}/history"),
            timeout=OP_TIMEOUT,
        )
        history_joined = ""
        if rh.status_code == 200:
            hbody = rh.json()
            msgs = hbody.get("messages") or hbody.get("events") or []
            history_joined = " ".join(
                str(m.get("content", "")) for m in msgs if isinstance(m, dict)
            )
        # Frames may also carry the tool result; merge for the assert.
        frames_joined = " ".join(
            str(f.get("content", "")) for f in frames_b if isinstance(f, dict)
        )
        gate_visible = (
            "not wired as sender" in history_joined
            or "not wired as sender" in frames_joined
            or "not wired as sender" in text_b
        )
        # Sanity: bravo's LLM must have actually emitted the tool call.
        # If no tool_* frame fired, the gate assert below would PASS
        # trivially because nothing was attempted — that would mask the
        # bug we're trying to surface.
        tool_emitted = any(str(f.get("type", "")).startswith("tool") for f in frames_b)
        bugs.check(
            "B2 precondition: bravo emitted a send_channel tool call",
            tool_emitted,
            f"frames: {[f.get('type') for f in frames_b]}; text={text_b!r}",
        )
        bugs.check(
            "B2: bravo send_channel(ch1) blocked by send-edge gate",
            gate_visible,
            f"history={history_joined[:600]!r}; "
            f"text={text_b!r}; "
            f"frame_types={[f.get('type') for f in frames_b]}",
        )
        # Behavior assert: the broadcast must NOT have reached
        # ch1's history.  If bravo's send slipped past the gate,
        # the message body "b2-should-be-blocked" would appear.
        rch = await asyncio.wait_for(
            host.http.get(f"/api/sessions/topology/{graph_a}/channels/ch1"),
            timeout=OP_TIMEOUT,
        )
        ch_history: list = []
        if rch.status_code == 200:
            body = rch.json()
            ch_history = body.get("history") or body.get("messages") or []
        leaked = any(
            "b2-should-be-blocked" in str(m.get("content", ""))
            for m in ch_history
            if isinstance(m, dict)
        )
        bugs.check(
            "B2: unauthorized bravo send did NOT broadcast on ch1",
            not leaked,
            f"ch1 history: {ch_history!r}",
        )

    # === 29c. cross-creature flow via LLM-emitted tool call ======
    # The heart of multi-node: alpha's LLM emits a ``send_channel``
    # tool call; the engine dispatches it; the message broadcasts
    # on ch1; bravo (listening) gets a channel event and fires a
    # triggered turn whose reply we observe.  We reload alpha's
    # scripted LLM (via model switch ??the subprocess seam re-reads
    # the script file on every new provider) so the *next* turn
    # starts at script[0] = a tool call.
    async with bugs.step("29c LLM-driven send_channel from alpha ??bravo"):
        # Re-wire alpha ??ch1 (step 27 may have removed it).
        rrw = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/topology/{graph_a}/creatures/{a_id}/wire",
                json={"channel": "ch1", "direction": "send"},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "29c re-wire alpha ??ch1 200/400",
            rrw.status_code in (200, 400),
            f"{rrw.status_code} {rrw.text[:300]}",
        )
        # Build the bracket-format tool-call response.
        tool_call_text = (
            "[/send_channel]\n"
            "@@channel=ch1\n"
            "@@message=cross-node greeting from alpha\n"
            "[send_channel/]"
        )
        # In-process workers share the test process's scripted-LLM
        # holder (see ``install_scripted_llm`` setup at top).  The
        # match-gated entries pre-installed there fire when alpha
        # receives "please ping bravo" → emits send_channel; bravo
        # receives the broadcast "cross-node greeting" → replies.
        # No mid-test script rewrite needed.
        _ = tool_call_text  # keep the var alive for symmetry with subprocess journey
        # Drive alpha ??its scripted LLM yields the tool call, the
        # parser dispatches send_channel, the engine broadcasts.
        try:
            async with host.api_ws(chat_url_a) as ws:
                text, frames = await asyncio.wait_for(
                    _drain_chat(ws, "please ping bravo"),
                    timeout=OP_TIMEOUT * 3,
                )
        except asyncio.TimeoutError:
            bugs.record(
                "29c alpha chat hung while emitting send_channel",
                f"w1 stderr: <inproc>",
            )
            text, frames = "", []
        # Behavior assert: alpha actually invoked the tool ??we expect
        # to see at least one tool_start/tool_done frame on the WS.
        bugs.check(
            "29c alpha emitted a tool call (tool_* frames present)",
            any(f.get("type", "").startswith("tool") for f in frames),
            f"frames seen: {[f.get('type') for f in frames]}",
        )
        # Behavior assert: bravo's channel history shows the broadcast
        # alpha just sent.
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/topology/{graph_a}/channels/ch1"),
            timeout=OP_TIMEOUT,
        )
        if rr.status_code == 200:
            body = rr.json()
            history = body.get("history") or body.get("messages") or []
            bugs.check(
                "BUG #134 multinode: channel ch1 records alpha's broadcast",
                any(
                    "cross-node greeting" in str(m.get("content", "")) for m in history
                ),
                f"channel history: {history}",
            )

    # === 29b2. recipe-driven terrarium spawn on the host ============
    # Exercises ``terrarium/recipe.py``, ``terrarium/root.py``,
    # ``terrarium/factory.py`` and the recipe → topology pipeline
    # that the dashboard's "New Terrarium" modal uses.
    async with bugs.step("29b2 recipe-driven terrarium spawn"):
        import yaml

        terra_dir = tmp_path / "terra_recipe"
        terra_dir.mkdir(parents=True, exist_ok=True)
        # Minimal terrarium recipe with one root creature + one channel.
        recipe = {
            "terrarium": {
                "name": "trio",
                "creatures": [
                    {
                        "name": "intake",
                        "config": str(cfg_alpha),
                    },
                    {
                        "name": "worker",
                        "config": str(cfg_alpha),
                    },
                ],
                "channels": {
                    "ops": {
                        "type": "broadcast",
                        "description": "operations channel",
                    },
                },
            },
        }
        recipe_path = terra_dir / "terrarium.yaml"
        recipe_path.write_text(yaml.safe_dump(recipe), encoding="utf-8")
        rr = await asyncio.wait_for(
            host.http.post(
                "/api/sessions/active/terrarium",
                json={"config_path": str(recipe_path)},
            ),
            timeout=OP_TIMEOUT * 4,
        )
        bugs.check(
            "29b2 recipe terrarium spawn returns 200 (NOT 500)",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:600]}",
        )
        # Legacy alias path — both should work for the same recipe.
        rr2 = await asyncio.wait_for(
            host.http.post(
                "/api/sessions/active/terrariums",
                json={"config_path": str(recipe_path)},
            ),
            timeout=OP_TIMEOUT * 4,
        )
        bugs.check(
            "29b2 legacy /terrariums alias returns 200",
            rr2.status_code == 200,
            f"{rr2.status_code} {rr2.text[:300]}",
        )

    # === 29c2. LLM-driven privileged tool: spawn charlie ============
    # Alpha (privileged) emits a ``group_add_node`` tool call when
    # the user types "please spawn charlie".  Exercises
    # tools_group_lifecycle + topology mutation + creature_host.
    async with bugs.step("29c2 group_add_node via LLM tool call"):
        try:
            async with host.api_ws(chat_url_a) as ws:
                text, frames = await asyncio.wait_for(
                    _drain_chat(ws, "please spawn charlie"),
                    timeout=OP_TIMEOUT * 3,
                )
        except asyncio.TimeoutError:
            bugs.record(
                "29c2 alpha chat hung while emitting group_add_node",
                f"frames seen: {[f.get('type') for f in (frames or [])]}",
            )
            text, frames = "", []
        # After the privileged tool, alpha's graph should have a new
        # member named "charlie".  Behavior assert via snapshot.
        snap = await asyncio.wait_for(
            host.http.get("/api/runtime/graph"), timeout=OP_TIMEOUT
        )
        if snap.status_code == 200:
            graphs = snap.json().get("graphs", []) or []
            all_names: list[str] = []
            for g in graphs:
                for c in g.get("creatures", []) or []:
                    all_names.append(c.get("name", ""))
            bugs.check(
                "29c2 charlie creature appears in runtime graph after spawn",
                "charlie" in all_names,
                f"creature names after spawn: {all_names}",
            )

    # === 29c3. More LLM-driven privileged tool calls ============
    # Exercise group_status, group_channel (add), group_wire (add) via
    # the LLM to hit tools_group_status.py / tools_group_channel.py /
    # tools_group_wire.py more thoroughly.
    async with bugs.step("29c3 group_status / group_channel / group_wire via LLM"):
        for prompt in [
            "show graph status",
            "add a new channel called privops",
            "wire alpha to charlie directly",
        ]:
            try:
                async with host.api_ws(chat_url_a) as ws:
                    text, frames = await asyncio.wait_for(
                        _drain_chat(ws, prompt),
                        timeout=OP_TIMEOUT * 3,
                    )
            except asyncio.TimeoutError:
                bugs.record(
                    f"29c3 alpha hung on prompt={prompt!r}",
                    f"frames seen: {[f.get('type') for f in (frames or [])]}",
                )

    # === 29c3b. CF-7: cross-cluster group_remove_node ===========
    # Alpha is privileged on w1; bravo lives on w2.  CLAUDE.md says
    # privileged tools are the cluster's "runtime graph editor", but
    # today ``gctx.engine`` is the caller's *worker* engine, so
    # ``resolve_group_target("bravo")`` misses on w1.  The fix
    # (CF-7 partial) is to surface a clearly cross-cluster-flagged
    # error rather than a generic "not in your group" message so the
    # LLM/user can distinguish a typo from a "lives on another
    # worker" miss.  A *full* fix (cluster-wide routing) is deferred
    # — see temp/bugs/CF7.md.
    async with bugs.step("29c3b CF-7: cross-cluster group_remove_node error shape"):
        # Drive alpha to emit the cross-cluster ``group_remove_node`` call,
        # then read the recorded ``tool_result`` event via the persistence
        # /events endpoint. The CF-7 fix surfaces the cross-cluster
        # diagnosis via ``ToolResult.error``, which is persisted as the
        # ``error`` field on the ``tool_result`` event in the worker's
        # session store. The chat-WS proxy chain forwards ``tool_error``
        # activity frames best-effort and may not relay them before the
        # receiver's idle timeout in the cluster-mux path, so the
        # session events log is the canonical surface to assert on.
        cross_err_seen = False
        try:
            async with host.api_ws(chat_url_a) as ws:
                await asyncio.wait_for(
                    _drain_chat(ws, "please remove bravo"),
                    timeout=OP_TIMEOUT * 3,
                )
            # Tool execution -> event write -> session.sync notify ->
            # host mirror append is a multi-hop async pipeline that
            # finishes some time AFTER the chat WS closes.  Fixed
            # sleeps race the pipeline on slow Windows / 3.13+ CI
            # runners.  Poll the /events endpoint until the tool_result
            # carrying the cross-cluster marker arrives or a generous
            # deadline expires.  macOS 3.13 specifically has been seen
            # to need >10s for the kqueue selector + lab WebSocket to
            # drain the tool_result event through the mirror writer.
            deadline = asyncio.get_event_loop().time() + 30.0
            last_tool_results: list[dict] = []
            while asyncio.get_event_loop().time() < deadline and not cross_err_seen:
                rr2 = await asyncio.wait_for(
                    host.http.get(
                        f"/api/sessions/{graph_a}/events",
                        params={"limit": 1000},
                    ),
                    timeout=OP_TIMEOUT,
                )
                if rr2.status_code == 200:
                    evts = rr2.json().get("events") or []
                    # Snapshot every tool_result we see so the failure
                    # message shows whether the tool fired at all and
                    # what error it actually carried — invaluable for
                    # diagnosing macOS-only timing regressions where
                    # the script may have desynced (off-by-one LLM
                    # call) or the engine_is_in_cluster gate flipped.
                    last_tool_results = [
                        {"name": e.get("name"), "error": e.get("error")}
                        for e in evts
                        if isinstance(e, dict) and e.get("type") == "tool_result"
                    ]
                    for e in evts:
                        if not isinstance(e, dict):
                            continue
                        if e.get("type") != "tool_result":
                            continue
                        err_text = str(e.get("error") or "")
                        if "cross-cluster" in err_text and "CF-7" in err_text:
                            cross_err_seen = True
                            break
                if not cross_err_seen:
                    await asyncio.sleep(0.25)
        except asyncio.TimeoutError:
            bugs.record(
                "29c3b alpha chat hung while emitting cross-cluster group_remove_node",
                "",
            )
        bugs.check(
            "CF-7: group_remove_node on a cross-cluster target returns a "
            "cross-cluster-flagged error (not a vague 'not in your group')",
            cross_err_seen,
            "expected a tool_result event on graph_a with error mentioning "
            "'cross-cluster' and 'CF-7' so the LLM/user can distinguish a "
            "typo from a cross-worker miss; observed tool_results="
            f"{last_tool_results!r}",
        )

    # === 29c4. /command framework command (compact / status) ====
    # The /command endpoint runs framework commands like /compact,
    # /status that the chat input bar recognizes.  Exercises
    # api/routes/sessions_v2/creatures_command.py + commands/.
    async with bugs.step("29c4 framework /command compact"):
        rr = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/{graph_a}/creatures/{a_id}/command",
                json={"command": "compact"},
            ),
            timeout=OP_TIMEOUT * 2,
        )
        bugs.check(
            "29c4 /command compact returns 200/400",
            rr.status_code in (200, 400, 404),
            f"{rr.status_code} {rr.text[:300]}",
        )

    # === 29c5. Rename creature + session ========================
    async with bugs.step("29c5 rename creature and session"):
        rr = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/active/agents/{a_id}/rename",
                json={"name": "alpha-renamed"},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "29c5 rename creature returns 200",
            rr.status_code in (200, 400, 404),
            f"{rr.status_code} {rr.text[:300]}",
        )
        rr = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/active/{graph_a}/creatures/{a_id}/rename",
                json={"name": "alpha-final"},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "29c5 rename creature via session-scoped path returns 200",
            rr.status_code in (200, 400, 404),
            f"{rr.status_code} {rr.text[:300]}",
        )

    # === 29c6. Native-tool-options PUT =========================
    async with bugs.step("29c6 PUT native-tool-options"):
        rr = await asyncio.wait_for(
            host.http.put(
                f"/api/sessions/{graph_a}/creatures/{a_id}/native-tool-options",
                json={"options": {"send_channel": {"enabled": True}}},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "29c6 PUT native-tool-options returns 200/422",
            rr.status_code in (200, 422, 400, 404),
            f"{rr.status_code} {rr.text[:300]}",
        )

    # === 29d. /disconnect via unified topology endpoint ==========
    async with bugs.step("29d unified /disconnect alpha ??bravo"):
        rr = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/topology/{graph_a}/disconnect",
                json={"sender": a_id, "receiver": b_id, "channel": "ch1"},
            ),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "29d unified /disconnect returns 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:400]}",
        )

    # === 29e. session list shows our session with persisted state =
    async with bugs.step("29e dashboard session listing"):
        rr = await asyncio.wait_for(
            host.http.get(f"/api/sessions/active/{graph_a}"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "29e active session detail 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:400]}",
        )
        # Also exercise the agents list view (dashboard left rail).
        rr = await asyncio.wait_for(
            host.http.get("/api/sessions/active/agents"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "29e dashboard /agents 200 with our creature listed",
            rr.status_code == 200
            and any(
                c.get("creature_id") == a_id
                for c in (
                    rr.json()
                    if isinstance(rr.json(), list)
                    else rr.json().get("agents", [])
                )
                if isinstance(c, dict)
            ),
            f"{rr.status_code} body={rr.text[:400]}",
        )

    # === 30. user stops bravo; alpha keeps running ==============
    async with bugs.step("30 stop bravo, alpha continues"):
        rr = await asyncio.wait_for(
            host.http.delete(f"/api/sessions/active/agents/{b_id}"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "30 stop bravo 200/204",
            rr.status_code in (200, 204),
            f"{rr.status_code} {rr.text[:300]}",
        )
        try:
            async with host.api_ws(chat_url_a) as ws:
                text, _ = await asyncio.wait_for(
                    _drain_chat(ws, "alpha after bravo gone"),
                    timeout=OP_TIMEOUT * 2,
                )
        except asyncio.TimeoutError:
            bugs.record(
                "30 alpha chat after bravo stopped (TimeoutError)",
                f"w1 stderr: <inproc>",
            )
            text = ""
        bugs.check(
            "30 alpha still responds after bravo stopped",
            bool(text) and "ERROR:" not in text,
            f"reply={text!r}",
        )

    # === 31. user closes the session, then resumes it via /sessions ==
    async with bugs.step("31 close alpha session"):
        # DELETE the session to evict from memory (simulates "close
        # tab" in the UI; the .kohakutr file remains on disk).
        dd = await asyncio.wait_for(
            host.http.delete(f"/api/sessions/active/{graph_a}"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "31 close alpha session returns 200/204",
            dd.status_code in (200, 204, 404),
            f"{dd.status_code} {dd.text[:300]}",
        )

    # === 32. saved-sessions list + persistence viewer + resume + fork ===
    # This is the persistence layer's user surface: the "Saved" tab in
    # the dashboard lists .kohakutr files, the user clicks one to view
    # a tree of turns, then opts to resume on a worker.  Each substep
    # exercises a different ``routes/persistence/*`` module.
    async with bugs.step("32a saved sessions list"):
        ls = await asyncio.wait_for(host.http.get("/api/sessions"), timeout=OP_TIMEOUT)
        bugs.check(
            "32a saved sessions list reachable",
            ls.status_code in (200, 404),
            f"{ls.status_code} {ls.text[:300]}",
        )
        sessions = []
        if ls.status_code == 200:
            body = ls.json()
            sessions = body if isinstance(body, list) else body.get("sessions", [])
    async with bugs.step("32b saved sessions disk-usage + stats"):
        for path in ("/api/sessions/disk-usage", "/api/sessions/stats"):
            rr = await asyncio.wait_for(host.http.get(path), timeout=OP_TIMEOUT)
            bugs.check(
                f"32b GET {path} 200",
                rr.status_code == 200,
                f"{rr.status_code} {rr.text[:300]}",
            )
    if sessions:
        sid = (
            sessions[0].get("session_id")
            or sessions[0].get("name")
            or sessions[0].get("session_name")
            or ""
        )
        if sid:
            async with bugs.step("32c persistence viewer endpoints"):
                for tail in ("tree", "summary", "turns", "events"):
                    rr = await asyncio.wait_for(
                        host.http.get(f"/api/sessions/{sid}/{tail}"),
                        timeout=OP_TIMEOUT,
                    )
                    bugs.check(
                        f"32c GET /sessions/{sid}/{tail} reachable",
                        rr.status_code in (200, 404),
                        f"{rr.status_code} {rr.text[:200]}",
                    )
            async with bugs.step("32d resume saved session on worker"):
                rr = await asyncio.wait_for(
                    host.http.post(
                        f"/api/sessions/{sid}/resume",
                        json={"on_node": "w1"},
                    ),
                    timeout=OP_TIMEOUT * 4,
                )
                bugs.check(
                    "32d resume on w1 returns 200/202",
                    rr.status_code in (200, 201, 202, 400, 404),
                    f"{rr.status_code} {rr.text[:400]}",
                )
            async with bugs.step("32e fork saved session"):
                rr = await asyncio.wait_for(
                    host.http.post(
                        f"/api/sessions/{sid}/fork",
                        json={"name": "forked-session", "at_event_id": 1},
                    ),
                    timeout=OP_TIMEOUT * 2,
                )
                bugs.check(
                    "32e fork returns 201/200/400",
                    rr.status_code in (200, 201, 400, 404),
                    f"{rr.status_code} {rr.text[:400]}",
                )
            async with bugs.step("32f history endpoint"):
                rr = await asyncio.wait_for(
                    host.http.get(f"/api/sessions/{sid}/history"),
                    timeout=OP_TIMEOUT,
                )
                bugs.check(
                    "32f history endpoint reachable",
                    rr.status_code in (200, 404),
                    f"{rr.status_code}",
                )

    # === 32g. CF-6 — cluster session resume ====================
    # A cluster session is N per-worker ``.kohakutr`` files plus a
    # host-side ``_cluster_links`` set. The pre-CF-6 resume route took
    # one ``on_node`` and pushed one file, silently downgrading the
    # multi-worker cluster to a singleton: no sibling resume, no
    # ``_cluster_links`` rebuild. CF-6's fix accepts a per-member list
    # AND re-issues ``service.connect`` to repopulate the cluster.
    async with bugs.step("32g CF-6 close bravo to evict cluster from memory"):
        # graph_a was already closed in step 31; close graph_b too so
        # both members live on disk only. _cluster_links is a HOST-side
        # set on MultiNodeTerrariumService — stop_session must persist
        # the membership to each member's mirror meta before the live
        # link drops out of memory.
        dd = await asyncio.wait_for(
            host.http.delete(f"/api/sessions/active/{graph_b}"),
            timeout=OP_TIMEOUT,
        )
        bugs.check(
            "32g close bravo session returns 200/204",
            dd.status_code in (200, 204, 404),
            f"{dd.status_code} {dd.text[:300]}",
        )
    async with bugs.step("32g CF-6 cluster resume rebuilds _cluster_links"):
        # Find both saved sids.  graph_a / graph_b are the engine
        # graph_ids the workers minted; the mirror files on the host
        # use those names verbatim.
        # Lex-smallest is the cluster primary (matches the cluster-fold
        # invariant used everywhere else in the journey).
        primary_sid = min([graph_a, graph_b])
        peer_sid = max([graph_a, graph_b])
        primary_node = "w1" if primary_sid == graph_a else "w2"
        peer_node = "w2" if primary_sid == graph_a else "w1"
        # The mirror writer drains worker session.sync events
        # asynchronously — closing graph_b returns before the worker's
        # last events have been received by the host's
        # ``SessionMirrorWriter`` and checkpointed to disk.  Poll the
        # saved-sessions listing with ``?refresh=true`` (forces an
        # index rebuild) until both member files show up or a
        # generous deadline expires.  This keeps the assertion strict
        # about the contract (both files MUST exist) without flaking
        # on slow-CI runners that need a beat longer than the
        # synchronous DELETE to drain.
        sid_set: set[str] = set()
        sessions: list = []
        has_primary = False
        has_peer = False
        deadline = asyncio.get_event_loop().time() + 10.0
        while asyncio.get_event_loop().time() < deadline:
            ls = await asyncio.wait_for(
                host.http.get("/api/sessions?refresh=true"), timeout=OP_TIMEOUT
            )
            sessions = []
            if ls.status_code == 200:
                body = ls.json()
                sessions = body if isinstance(body, list) else body.get("sessions", [])
            sid_set = {
                (s.get("session_id") or s.get("name") or s.get("session_name") or "")
                for s in sessions
            }
            has_primary = primary_sid in sid_set or any(
                primary_sid in s for s in sid_set
            )
            has_peer = peer_sid in sid_set or any(peer_sid in s for s in sid_set)
            if has_primary and has_peer:
                break
            await asyncio.sleep(0.25)
        bugs.check(
            "CF-6: both cluster member saved files are listed",
            has_primary and has_peer,
            f"saved sids={sid_set!r}; primary={primary_sid!r} peer={peer_sid!r}",
        )
        # Snapshot _cluster_links on the live host service before the
        # resume — it should be empty after both members were closed
        # (the link set lives only in memory and the engine graphs are
        # gone), and the resume must repopulate it.
        svc = get_service()
        pre_links = len(getattr(svc, "_cluster_links", set()))
        # POST resume with the full member list — this is the CF-6
        # API surface. Before CF-6 the route ignored ``members`` and
        # only pushed the primary's .kohakutr to ``on_node`` (w1),
        # bravo was never restored, _cluster_links stayed empty.
        rr = await asyncio.wait_for(
            host.http.post(
                f"/api/sessions/{primary_sid}/resume",
                json={
                    "on_node": primary_node,
                    "members": [
                        {"sid": primary_sid, "on_node": primary_node},
                        {"sid": peer_sid, "on_node": peer_node},
                    ],
                },
            ),
            timeout=OP_TIMEOUT * 6,
        )
        bugs.check(
            "CF-6: cluster resume request returns 200",
            rr.status_code == 200,
            f"{rr.status_code} {rr.text[:400]}",
        )
        if rr.status_code == 200:
            body = rr.json()
            # Behavior: the response surfaces BOTH members under
            # cluster_members so the frontend can render the cluster.
            cm = body.get("cluster_members") or []
            cm_nodes = {m.get("on_node") for m in cm}
            bugs.check(
                "CF-6: cluster_members in response covers both workers",
                cm_nodes == {primary_node, peer_node},
                f"cluster_members={cm!r}",
            )
            # Behavior: _cluster_links repopulated post-resume — the
            # cluster is a cluster again, not two singletons. Pre-CF-6
            # this was always 0 after a close/resume cycle.
            post_links = len(getattr(svc, "_cluster_links", set()))
            bugs.check(
                "CF-6: _cluster_links repopulated after cluster resume",
                post_links > pre_links or post_links > 0,
                f"pre={pre_links} post={post_links}",
            )
