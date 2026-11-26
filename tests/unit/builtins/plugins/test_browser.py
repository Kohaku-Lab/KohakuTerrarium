"""Unit tests for the ``browser`` builtin plugin, against the scripted driver.

Covers the plugin's lifecycle (cheap construction, lazy launch, close on stop
and unload in either order, runtime disable, re-enable), option validation,
the host-resources refusal, the sandbox wiring (deny, allowlist, live change,
dropped connections, confinement, per-call re-read) and the prompt gate.
"""

import asyncio
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import fields
from types import SimpleNamespace

import pytest

import kohakuterrarium as kt
from kohakuterrarium.browser import profiles as profiles_module
from kohakuterrarium.browser import proxy as proxy_module
from kohakuterrarium.browser.errors import BrowserClosedError
from kohakuterrarium.browser.launch import Candidate
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.builtins.plugin_catalog import is_default_on, lookup_plugin
from kohakuterrarium.builtins.plugins.browser.plugin import (
    DOWNLOADS_DIR,
    PROMPT,
    BrowserPlugin,
)
from kohakuterrarium.builtins.plugins.sandbox.plugin import SandboxPlugin
from kohakuterrarium.core.agent.tools.enablement import switch_tool, tool_switch_state
from kohakuterrarium.core.config.types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.errors import ConfigError
from kohakuterrarium.llm.base import NativeToolCall
from kohakuterrarium.modules.plugin.base import PluginBlockError
from kohakuterrarium.modules.plugin.manager.manager import PluginManager
from kohakuterrarium.modules.plugin.option_validation import PluginOptionError
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.terrarium.channels.channels import TERRARIUM_ENGINE_KEY
from kohakuterrarium.testing.llm import ScriptedLLM
from tests.helpers.browser_driver import ScriptedDownload
from tests.helpers.browser_plugin import (
    call,
    plugin_context,
    scripted_plugin,
    tool_context,
)
from tests.unit.test_tool_doc_shape import DISCOURAGING

TOOLS = (
    "browser_navigate",
    "browser_snapshot",
    "browser_act",
    "browser_screenshot",
    "browser_tabs",
    "browser_eval",
)
VALID_ARGS = {
    "browser_navigate": {"url": "http://shop.test/"},
    "browser_snapshot": {},
    "browser_act": {"actions": [{"kind": "click", "ref": "e2"}]},
    "browser_screenshot": {},
    "browser_tabs": {"action": "list"},
    "browser_eval": {"expression": "1"},
}


async def loaded(tmp_path, manager=None, **options):
    """A scripted plugin after ``on_load``, with its driver."""
    plugin, driver = scripted_plugin(**options)
    await plugin.on_load(plugin_context(tmp_path, manager))
    return plugin, driver


def restricted() -> dict:
    """An environment whose engine refuses host resources."""
    return {TERRARIUM_ENGINE_KEY: SimpleNamespace(host_resources=False)}


async def proxied(session, request: bytes) -> tuple[asyncio.StreamReader, object]:
    """Open a connection to the session's proxy and send ``request``."""
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", session._stack.proxy.port
    )
    writer.write(request)
    await writer.drain()
    return reader, writer


async def proxied_get(session, host: str) -> None:
    reader, writer = await proxied(
        session, f"GET http://{host}/ HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()
    )
    await asyncio.wait_for(reader.read(), 5)
    writer.close()


def external_blocks(session, host: str) -> list:
    return [
        r
        for r in session._stack.proxy.recent()
        if r.host == host and r.blocked and r.code == "external"
    ]


def wrapped(manager, tool, context):
    return manager.wrap_method(
        "pre_tool_execute",
        "post_tool_execute",
        tool.execute,
        input_kwarg="args",
        extra_kwargs={
            "tool_name": tool.tool_name,
            "tool": tool,
            "job_id": "job",
            "context": context,
        },
    )


def manager_context(manager, tmp_path) -> ToolContext:
    context = ToolContext(agent_name="browser_test", session=None, working_dir=tmp_path)
    context.runtime_services.update(manager.collect_runtime_services(context))
    return context


FAST_REJUDGE_S = 0.1
REJUDGE_BOUND_S = 2.0


@pytest.fixture
def fast_rejudge(monkeypatch):
    monkeypatch.setattr(proxy_module, "REJUDGE_INTERVAL_S", FAST_REJUDGE_S)


async def connect(session, port: int, host: str = "127.0.0.1"):
    """A ``CONNECT`` to ``host:port`` through the session's proxy: its head and streams."""
    target = f"{host}:{port}"
    reader, writer = await proxied(
        session, f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode()
    )
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    if not head.startswith(b"HTTP/1.1 200"):
        writer.write_eof()
        head += await asyncio.wait_for(reader.read(), 5)
        writer.close()
    return head, (reader, writer)


async def tunnel(session, port: int):
    """An open, working tunnel to the loopback echo server."""
    head, (reader, writer) = await connect(session, port)
    assert head.startswith(b"HTTP/1.1 200"), head
    writer.write(b"ping")
    await writer.drain()
    assert await asyncio.wait_for(reader.readexactly(4), 5) == b"ping"
    return reader, writer


async def closes_within(reader, seconds: float) -> None:
    """``reader`` reaches EOF within ``seconds``."""
    try:
        data = await asyncio.wait_for(reader.read(), seconds)
    except asyncio.TimeoutError:
        pytest.fail(f"the tunnel was still open after {seconds:g} s")
    assert data == b"", data


@asynccontextmanager
async def sandboxed(tmp_path, sandbox_options, disabled=False, **options):
    """A launched browser next to a real sandbox, and a loopback echo server."""

    async def echo(reader, writer):
        while data := await reader.read(1024):
            writer.write(data)
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    plugin, driver = scripted_plugin(allow_loopback=True, **options)
    sandbox = SandboxPlugin(options=sandbox_options)
    manager = PluginManager()
    manager.register(sandbox)
    manager.register(plugin)
    await manager.load_all(plugin_context(tmp_path, manager))
    if disabled:
        manager.disable("sandbox")
    snapshot = plugin.contribute_tools()[1]
    context = manager_context(manager, tmp_path)
    assert (await wrapped(manager, snapshot, context)({}, context)).success
    try:
        yield plugin, sandbox, manager, port
    finally:
        server.close()
        await manager.unload_all()
        assert driver.alive() == []


class TestCatalogAndConstruction:
    def test_catalog_entry_is_registered_and_off_by_default(self):
        assert lookup_plugin("browser") == {
            "module": "kohakuterrarium.builtins.plugins.browser.plugin",
            "class": "BrowserPlugin",
        }
        assert not is_default_on("browser")

    def test_import_and_construction_load_no_playwright_and_start_nothing(self):
        script = (
            "import sys\n"
            "from kohakuterrarium.builtins.plugins.browser.plugin import BrowserPlugin\n"
            "plugin = BrowserPlugin()\n"
            "print(sorted(m for m in sys.modules if m.split('.')[0] == 'playwright'))\n"
            "print(plugin.session is None, plugin.runtime_services(None))\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        assert done.stdout.splitlines() == ["[]", "True {}"]

    def test_tools_are_contributed_once(self):
        plugin = BrowserPlugin()
        tools = plugin.contribute_tools()
        assert [t.tool_name for t in tools] == list(TOOLS)
        assert plugin.contribute_tools() == tools

    def test_schema_covers_every_browser_option(self):
        schema = BrowserPlugin.option_schema()
        assert set(schema) == {f.name for f in fields(BrowserOptions)}
        options = BrowserPlugin().get_options()
        assert options["viewport"] == "1280x800"
        assert options["headless"] is True
        assert options["allow_hosts"] == [] and options["secrets"] == {}
        assert set(options) == set(schema)

    def test_constructor_keywords_and_options_mapping_are_validated(self):
        assert BrowserPlugin(allow_loopback=True).get_options()["allow_loopback"]
        assert BrowserPlugin(options={"channel": ""}).get_options()["channel"] is None
        with pytest.raises(ConfigError, match="headless"):
            BrowserPlugin(options={"headless": "yes"})
        with pytest.raises(ConfigError, match="unknown browser option"):
            BrowserPlugin(bogus=1)
        with pytest.raises(ConfigError, match="origins"):
            BrowserPlugin(secrets={"pw": "literal-password"})
        with pytest.raises(ConfigError, match="origins"):
            BrowserPlugin(secrets={"pw": {"env": "PW", "origins": []}})

    def test_secrets_round_trip_as_plain_values(self):
        given = {"pw": {"env": "SHOP_PW", "origins": ["https://Shop.example/"]}}
        plugin = BrowserPlugin(secrets=given)
        shown = plugin.get_options()["secrets"]
        assert shown == {"pw": {"env": "SHOP_PW", "origins": ["https://shop.example"]}}
        assert json.loads(json.dumps(shown)) == shown
        again = plugin.set_options({"secrets": json.loads(json.dumps(shown))})
        assert again["secrets"] == shown
        assert plugin.option_schema()["secrets"]["type"] == "dict"


class TestOptionsAtRuntime:
    async def test_invalid_change_is_refused_and_nothing_changes(self, tmp_path):
        plugin, _ = await loaded(tmp_path)
        before, session = plugin.get_options(), plugin.session
        with pytest.raises(ConfigError, match="viewport"):
            plugin.set_options({"viewport": "huge"})
        with pytest.raises(PluginOptionError):
            plugin.set_options({"nav_timeout_s": "slow"})
        with pytest.raises(PluginOptionError):
            plugin.set_options({"no_such_option": 1})
        assert plugin.get_options() == before and plugin.session is session
        await plugin.on_unload()

    async def test_valid_change_closes_the_running_browser_and_applies(self, tmp_path):
        plugin, driver = await loaded(tmp_path)
        context = tool_context(plugin, tmp_path)
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        old = plugin.session
        merged = plugin.set_options({"allow_loopback": True, "viewport": "800x600"})
        assert merged["allow_loopback"] is True and merged["viewport"] == "800x600"
        await asyncio.sleep(0.05)
        assert old.state == "closed" and driver.alive() == []
        assert plugin.session is not old and plugin.session.state == "new"
        assert plugin.session.options.allow_loopback is True
        context = tool_context(plugin, tmp_path)
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        assert driver.plans[-1].viewport == (800, 600)
        await plugin.on_unload()
        assert driver.alive() == []


class TestLifecycle:
    async def test_session_is_created_on_load_and_launched_on_first_call(
        self, tmp_path
    ):
        plugin, driver = scripted_plugin()
        assert plugin.session is None and plugin.runtime_services(None) == {}
        await plugin.on_load(plugin_context(tmp_path))
        assert plugin.session.state == "new" and driver.plans == []
        assert set(plugin.runtime_services(None)) == {"browser"}
        result = await call(
            plugin, "browser_snapshot", {}, tool_context(plugin, tmp_path)
        )
        assert result.success and len(driver.plans) == 1
        assert plugin.session.state == "ready"
        assert plugin.session._downloads.directory == tmp_path / DOWNLOADS_DIR
        await plugin.on_unload()

    @pytest.mark.parametrize("order", ["stop_then_unload", "unload_then_stop"])
    async def test_stop_and_unload_close_in_either_order_and_never_relaunch(
        self, tmp_path, order
    ):
        plugin, driver = await loaded(tmp_path)
        context = tool_context(plugin, tmp_path)
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        hooks = [plugin.on_agent_stop, plugin.on_unload]
        if order == "unload_then_stop":
            hooks.reverse()
        for hook in hooks:
            await hook()
            assert driver.alive() == [] and plugin.session.state == "closed"
        for name in TOOLS:
            result = await call(plugin, name, VALID_ARGS[name], context)
            assert not result.success
            assert "closed" in result.error, (name, result.error)
        assert len(driver.plans) == 1, "a closed session must never relaunch"
        assert plugin.get_prompt_content(plugin_context(tmp_path)) is None

    async def test_unload_without_load_is_harmless(self):
        plugin, driver = scripted_plugin()
        await plugin.on_agent_stop()
        await plugin.on_unload()
        assert plugin.session is None and driver.plans == []

    async def test_load_after_stop_gives_a_working_session(self, tmp_path):
        plugin, driver = await loaded(tmp_path)
        await call(plugin, "browser_snapshot", {}, tool_context(plugin, tmp_path))
        await plugin.on_agent_stop()
        await plugin.on_load(plugin_context(tmp_path))
        result = await call(
            plugin, "browser_snapshot", {}, tool_context(plugin, tmp_path)
        )
        assert result.success and len(driver.plans) == 2
        await plugin.on_unload()
        assert driver.alive() == []

    async def test_runtime_disable_closes_the_browser_and_enable_brings_it_back(
        self, tmp_path
    ):
        plugin, driver = scripted_plugin()
        manager = PluginManager()
        manager.register(plugin)
        await manager.load_all(plugin_context(tmp_path, manager))
        context = tool_context(plugin, tmp_path)
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        old = plugin.session
        assert manager.disable("browser")
        await asyncio.sleep(0.05)
        assert driver.alive() == [], "a disabled plugin must not keep a browser"
        assert old.state == "closed"
        assert manager.collect_runtime_services(context) == {}
        result = await call(plugin, "browser_snapshot", {}, context)
        assert "closed" in result.error and len(driver.plans) == 1
        assert manager.enable("browser")
        await manager.load_pending()
        context = tool_context(plugin, tmp_path)
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        assert len(driver.plans) == 2 and len(driver.alive()) == 1
        await manager.unload_all()
        assert driver.alive() == []

    async def test_disabled_plugin_hands_no_session_to_a_kept_access(self, tmp_path):
        plugin, driver = scripted_plugin()
        manager = PluginManager()
        manager.register(plugin)
        await manager.load_all(plugin_context(tmp_path, manager))
        context = tool_context(plugin, tmp_path)
        kept = context.runtime_services["browser"]
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        assert manager.disable("browser")
        plugin.set_options({"viewport": "800x600"})
        assert plugin.session is None, "a disabled plugin built a session"
        with pytest.raises(BrowserClosedError):
            await kept.prepare(context)
        assert plugin.session is None and len(driver.plans) == 1
        await asyncio.sleep(0.05)
        assert driver.alive() == []
        assert manager.enable("browser")
        await manager.load_pending()
        session = await kept.prepare(tool_context(plugin, tmp_path))
        assert session is plugin.session and session.options.viewport == (800, 600)
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        assert len(driver.plans) == 2 and driver.plans[1].viewport == (800, 600)
        await manager.unload_all()
        assert driver.alive() == []

    async def test_enable_without_load_pending_still_gets_a_session(self, tmp_path):
        plugin, driver = scripted_plugin()
        manager = PluginManager()
        manager.register(plugin)
        await manager.load_all(plugin_context(tmp_path, manager))
        await call(plugin, "browser_snapshot", {}, tool_context(plugin, tmp_path))
        manager.disable("browser")
        await asyncio.sleep(0.05)
        manager.enable("browser")
        result = await call(
            plugin, "browser_snapshot", {}, tool_context(plugin, tmp_path)
        )
        assert result.success and len(driver.plans) == 2
        await manager.unload_all()
        assert driver.alive() == []

    async def test_a_disable_that_did_not_happen_keeps_the_browser(self, tmp_path):
        plugin, driver = scripted_plugin()
        manager = PluginManager()
        manager.register(plugin)
        context = plugin_context(tmp_path, manager)

        def refuse_refresh():
            raise RuntimeError("inventory refresh failed")

        context.host_agent.refresh_plugin_tools = refuse_refresh
        await manager.load_all(context)
        await call(plugin, "browser_snapshot", {}, tool_context(plugin, tmp_path))
        assert plugin.refuses_disable() is False
        with pytest.raises(RuntimeError, match="inventory refresh failed"):
            manager.disable("browser")
        assert manager.is_enabled("browser")
        await asyncio.sleep(0.05)
        assert len(driver.alive()) == 1 and plugin.session.state == "ready"
        context.host_agent.refresh_plugin_tools = lambda: None
        await manager.unload_all()
        assert driver.alive() == []


def live_at_launch(driver) -> list[int]:
    """How many browsers were alive as each later launch began."""
    seen: list[int] = []
    real = driver.launch

    async def launch(plan, sink):
        seen.append(len(driver.alive()))
        return await real(plan, sink)

    driver.launch = launch
    return seen


def hold_close(browser) -> None:
    """Make every close stage of ``browser`` hang until its stage timeout."""
    browser.hang_stages = {"context", "browser", "playwright"}


class TestNoOverlap:
    async def test_an_option_change_waits_for_the_old_browser_to_close(self, tmp_path):
        profile = str(tmp_path / "profile")
        plugin, driver = await loaded(tmp_path, profile_dir=profile)
        assert (
            await call(plugin, "browser_snapshot", {}, tool_context(plugin, tmp_path))
        ).success
        old = driver.browser
        hold_close(old)
        seen = live_at_launch(driver)
        plugin.set_options({"viewport": "800x600"})
        result = await call(
            plugin, "browser_snapshot", {}, tool_context(plugin, tmp_path)
        )
        assert result.success and len(driver.plans) == 2
        assert seen == [0], "a second browser started on the same profile"
        assert not old.alive and driver.plans[1].profile_dir == profile
        await plugin.on_unload()
        assert driver.alive() == []

    @pytest.mark.parametrize("change", ["options", "reenable"])
    async def test_a_profile_is_refused_while_the_old_browser_outlives_its_kill(
        self, tmp_path, monkeypatch, change
    ):
        monkeypatch.setattr(profiles_module, "RELEASE_WAIT_S", 0.2)
        plugin, driver = scripted_plugin(profile_dir=str(tmp_path / "profile"))
        manager = PluginManager()
        manager.register(plugin)
        await manager.load_all(plugin_context(tmp_path, manager))
        context = tool_context(plugin, tmp_path)
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        old = driver.browser
        hold_close(old)
        old.survives_kill = True
        if change == "options":
            plugin.set_options({"viewport": "800x600"})
        else:
            manager.disable("browser")
            manager.enable("browser")
            await manager.load_pending()
        context = tool_context(plugin, tmp_path)
        result = await call(plugin, "browser_snapshot", {}, context)
        assert not result.success and "could not be confirmed" in result.error
        assert old.killed and len(driver.plans) == 1
        old.alive = False
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        assert len(driver.plans) == 2 and driver.alive() == [driver.browser]
        await manager.unload_all()
        assert driver.alive() == []

    async def test_a_throwaway_profile_starts_beside_an_old_browser_that_outlived_its_kill(
        self, tmp_path
    ):
        plugin, driver = await loaded(tmp_path)
        context = tool_context(plugin, tmp_path)
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        old = driver.browser
        hold_close(old)
        old.survives_kill = True
        plugin.set_options({"viewport": "800x600"})
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        assert old.killed and old.alive and len(driver.plans) == 2
        old.alive = False
        await plugin.on_unload()
        assert driver.alive() == []

    async def test_disable_then_enable_never_runs_on_the_dying_browser(self, tmp_path):
        plugin, driver = scripted_plugin()
        manager = PluginManager()
        manager.register(plugin)
        await manager.load_all(plugin_context(tmp_path, manager))
        assert (
            await call(plugin, "browser_snapshot", {}, tool_context(plugin, tmp_path))
        ).success
        old_session, old = plugin.session, driver.browser
        hold_close(old)
        seen = live_at_launch(driver)
        manager.disable("browser")
        manager.enable("browser")
        context = tool_context(plugin, tmp_path)
        first = await call(plugin, "browser_snapshot", {}, context)
        second = await call(plugin, "browser_snapshot", {}, context)
        assert first.success and second.success, (first.error, second.error)
        assert plugin.session is not old_session and old_session.state == "closed"
        assert len(driver.plans) == 2 and seen == [0]
        assert len(driver.alive()) == 1
        await manager.unload_all()
        assert driver.alive() == []

    async def test_a_close_without_a_loop_is_kept_for_later(self, tmp_path, caplog):
        plugin, driver = await loaded(tmp_path)
        assert (
            await call(plugin, "browser_snapshot", {}, tool_context(plugin, tmp_path))
        ).success
        old_session = plugin.session

        def no_loop():
            raise RuntimeError("no running event loop")

        real = asyncio.get_running_loop
        try:
            asyncio.get_running_loop = no_loop
            plugin.set_options({"viewport": "800x600"})
        finally:
            asyncio.get_running_loop = real
        assert "close of the old browser session was deferred" in caplog.text
        assert old_session.state != "closed" and len(driver.alive()) == 1
        seen = live_at_launch(driver)
        assert (
            await call(plugin, "browser_snapshot", {}, tool_context(plugin, tmp_path))
        ).success
        assert old_session.state == "closed" and seen == [0]
        await plugin.on_unload()
        assert driver.alive() == []

    async def test_unload_closes_a_session_whose_close_was_deferred(self, tmp_path):
        plugin, driver = await loaded(tmp_path)
        assert (
            await call(plugin, "browser_snapshot", {}, tool_context(plugin, tmp_path))
        ).success
        real = asyncio.get_running_loop
        try:
            asyncio.get_running_loop = lambda: (_ for _ in ()).throw(
                RuntimeError("no running event loop")
            )
            plugin.on_disable()
        finally:
            asyncio.get_running_loop = real
        assert len(driver.alive()) == 1
        await plugin.on_unload()
        assert driver.alive() == []


class TestHostResources:
    async def test_restricted_engine_refuses_host_options_without_launching(
        self, tmp_path
    ):
        plugin, driver = await loaded(
            tmp_path,
            headless=False,
            profile_dir=str(tmp_path / "profile"),
            channel="chrome",
            allow_loopback=True,
            allow_private=True,
        )
        context = tool_context(plugin, tmp_path, environment=restricted())
        result = await call(plugin, "browser_snapshot", {}, context)
        assert not result.success
        for name in ("headless", "profile_dir", "channel", "allow_loopback"):
            assert name in result.error, result.error
        assert "allow_private" in result.error and "executable_path" not in result.error
        assert driver.plans == [], "refused, not silently downgraded"
        await plugin.on_unload()

    async def test_restricted_engine_refuses_secrets_without_launching(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("KT_T_SERVER_TOKEN", "server-side-token")
        secrets = {"pw": {"env": "KT_T_SERVER_TOKEN", "origins": ["http://shop.test"]}}
        plugin, driver = await loaded(tmp_path, secrets=secrets)
        context = tool_context(plugin, tmp_path, environment=restricted())
        for name in ("browser_snapshot", "browser_act"):
            args = {"actions": [{"kind": "type", "ref": "e3", "secret": "pw"}]}
            result = await call(
                plugin, name, args if name == "browser_act" else {}, context
            )
            assert not result.success and "refused options: secrets" in result.error
            assert "server-side-token" not in result.error
        assert driver.plans == []
        await plugin.on_unload()

    async def test_restricted_engine_allows_a_headless_throwaway_profile(
        self, tmp_path
    ):
        plugin, driver = await loaded(tmp_path)
        context = tool_context(plugin, tmp_path, environment=restricted())
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        assert driver.plans[0].headless is True
        assert driver.plans[0].candidates == (Candidate("bundled chromium"),)
        await plugin.on_unload()

    async def test_an_unrestricted_engine_may_fall_back_to_system_browsers(
        self, tmp_path
    ):
        plugin, driver = await loaded(tmp_path)
        assert (
            await call(plugin, "browser_snapshot", {}, tool_context(plugin, tmp_path))
        ).success
        assert [c.label for c in driver.plans[0].candidates] == [
            "bundled chromium",
            "system chrome",
            "system msedge",
        ]
        await plugin.on_unload()

    async def test_a_restriction_relaunches_a_browser_planned_with_system_ones(
        self, tmp_path
    ):
        plugin, driver = await loaded(tmp_path)
        free = tool_context(plugin, tmp_path)
        assert (await call(plugin, "browser_snapshot", {}, free)).success
        first = driver.browser
        fenced = tool_context(plugin, tmp_path, environment=restricted())
        assert (await call(plugin, "browser_snapshot", {}, fenced)).success
        assert not first.alive and len(driver.plans) == 2
        assert driver.plans[1].candidates == (Candidate("bundled chromium"),)
        assert (await call(plugin, "browser_snapshot", {}, fenced)).success
        assert len(driver.plans) == 2, "a bundled-only browser is kept"
        await plugin.on_unload()
        assert driver.alive() == []

    async def test_an_unrestricted_engine_allows_host_options(self, tmp_path):
        plugin, driver = await loaded(tmp_path, allow_loopback=True, headless=False)
        allowed = {TERRARIUM_ENGINE_KEY: SimpleNamespace(host_resources=True)}
        context = tool_context(plugin, tmp_path, environment=allowed)
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        assert driver.plans[0].headless is False
        await plugin.on_unload()


class TestSandboxWiring:
    async def test_network_deny_blocks_every_browser_tool(self, tmp_path):
        plugin, driver = scripted_plugin()
        manager = PluginManager()
        manager.register(SandboxPlugin(options={"network": "deny"}))
        manager.register(plugin)
        await manager.load_all(plugin_context(tmp_path, manager))
        for tool in plugin.contribute_tools():
            context = manager_context(manager, tmp_path)
            with pytest.raises(PluginBlockError, match="network"):
                await wrapped(manager, tool, context)(
                    dict(VALID_ARGS[tool.tool_name]), context
                )
        assert driver.plans == []
        await manager.unload_all()

    async def test_allowlist_reaches_the_proxy_and_follows_live_changes(self, tmp_path):
        plugin, driver = scripted_plugin()
        sandbox = SandboxPlugin(options={"network_allowlist": ["shop.test"]})
        manager = PluginManager()
        manager.register(sandbox)
        manager.register(plugin)
        await manager.load_all(plugin_context(tmp_path, manager))
        tool = plugin.contribute_tools()[1]
        context = manager_context(manager, tmp_path)
        assert (await wrapped(manager, tool, context)({}, context)).success
        session = plugin.session
        await proxied_get(session, "evil.test")
        assert external_blocks(session, "evil.test"), "sandbox allowlist not applied"
        await proxied_get(session, "shop.test")
        assert not external_blocks(session, "shop.test")
        sandbox.set_options({"network_allowlist": ["shop.test", "evil.test"]})
        before = len(external_blocks(session, "evil.test"))
        await proxied_get(session, "evil.test")
        assert len(external_blocks(session, "evil.test")) == before, "not live"
        await manager.unload_all()
        assert driver.alive() == []

    async def test_service_is_read_again_on_every_call(self, tmp_path):
        plugin, driver = scripted_plugin()
        sandbox = SandboxPlugin(options={"network_allowlist": ["shop.test"]})
        manager = PluginManager()
        manager.register(sandbox)
        manager.register(plugin)
        await manager.load_all(plugin_context(tmp_path, manager))
        tool = plugin.contribute_tools()[1]
        context = manager_context(manager, tmp_path)
        await wrapped(manager, tool, context)({}, context)
        session = plugin.session
        await proxied_get(session, "evil.test")
        assert len(external_blocks(session, "evil.test")) == 1
        manager.disable("sandbox")
        context = manager_context(manager, tmp_path)
        assert "network_policy" not in context.runtime_services
        assert (await wrapped(manager, tool, context)({}, context)).success
        await proxied_get(session, "evil.test")
        assert len(external_blocks(session, "evil.test")) == 1, "held a stale service"
        manager.enable("sandbox")
        context = manager_context(manager, tmp_path)
        await wrapped(manager, tool, context)({}, context)
        await proxied_get(session, "evil.test")
        assert len(external_blocks(session, "evil.test")) == 2
        await manager.unload_all()
        assert driver.alive() == []

    async def test_deny_drops_open_connections_without_a_tool_call(
        self, tmp_path, fast_rejudge
    ):
        async with sandboxed(tmp_path, {}) as (plugin, sandbox, manager, port):
            reader, writer = await tunnel(plugin.session, port)
            sandbox.set_options({"network": "deny"})
            await closes_within(reader, REJUDGE_BOUND_S)
            assert plugin.session.state == "ready", "only connections are dropped"
            head, _ = await connect(plugin.session, port)
            assert head.startswith(b"HTTP/1.1 403") and b"external" in head
            writer.close()

    async def test_required_confinement_cuts_a_running_browser_off_the_network(
        self, tmp_path, fast_rejudge
    ):
        async with sandboxed(tmp_path, {}) as (plugin, sandbox, manager, port):
            reader, writer = await tunnel(plugin.session, port)
            sandbox.set_options({"os_enforce": "require"})
            head, _ = await connect(plugin.session, port)
            assert head.startswith(b"HTTP/1.1 403") and b"os_enforce=require" in head
            await closes_within(reader, REJUDGE_BOUND_S)
            assert (
                plugin.session.state == "ready"
            ), "the process closes at the next call"
            snapshot = plugin.contribute_tools()[1]
            context = manager_context(manager, tmp_path)
            result = await wrapped(manager, snapshot, context)({}, context)
            assert not result.success and "os_enforce" in result.error
            assert plugin.session.state != "ready"
            writer.close()

    async def test_a_narrowed_allowlist_drops_tunnels_it_no_longer_allows(
        self, tmp_path, fast_rejudge
    ):
        allow = {"network_allowlist": ["127.0.0.1", "shop.test"]}
        async with sandboxed(tmp_path, allow) as (plugin, sandbox, manager, port):
            reader, writer = await tunnel(plugin.session, port)
            sandbox.set_options({"network_allowlist": ["shop.test"]})
            await closes_within(reader, REJUDGE_BOUND_S)
            writer.close()

    async def test_an_allowed_tunnel_survives_rejudges(self, tmp_path, fast_rejudge):
        allow = {"network_allowlist": ["127.0.0.1", "shop.test"]}
        async with sandboxed(tmp_path, allow) as (plugin, sandbox, manager, port):
            reader, writer = await tunnel(plugin.session, port)
            sandbox.set_options({"network_allowlist": ["127.0.0.1"]})
            await asyncio.sleep(FAST_REJUDGE_S * 5)
            writer.write(b"pong")
            await writer.drain()
            assert await asyncio.wait_for(reader.readexactly(4), 5) == b"pong"
            writer.close()

    async def test_enabling_the_sandbox_applies_at_once(self, tmp_path, fast_rejudge):
        allow = {"network_allowlist": ["shop.test"]}
        async with sandboxed(tmp_path, allow, disabled=True) as (
            plugin,
            sandbox,
            manager,
            port,
        ):
            reader, writer = await tunnel(plugin.session, port)
            assert manager.enable("sandbox")
            head, _ = await connect(plugin.session, port)
            assert head.startswith(b"HTTP/1.1 403"), "the next connection was let in"
            assert b"not allowlisted" in head
            await closes_within(reader, REJUDGE_BOUND_S)
            writer.close()

    async def test_a_disabled_sandbox_leaves_the_browsers_own_policy(
        self, tmp_path, fast_rejudge
    ):
        async with sandboxed(tmp_path, {}, block_hosts=["blocked.test"]) as (
            plugin,
            sandbox,
            manager,
            port,
        ):
            sandbox.set_options({"network": "deny"})
            head, _ = await connect(plugin.session, port)
            assert head.startswith(b"HTTP/1.1 403")
            assert manager.disable("sandbox")
            reader, writer = await tunnel(plugin.session, port)
            head, _ = await connect(plugin.session, port, host="blocked.test")
            assert head.startswith(b"HTTP/1.1 403") and b"blocked_host" in head
            await asyncio.sleep(FAST_REJUDGE_S * 3)
            writer.write(b"pong")
            await writer.drain()
            assert await asyncio.wait_for(reader.readexactly(4), 5) == b"pong"
            writer.close()

    async def test_a_browser_call_rejudges_at_once(self, tmp_path, monkeypatch):
        monkeypatch.setattr(proxy_module, "REJUDGE_INTERVAL_S", 60.0)
        allow = {"network_allowlist": ["127.0.0.1", "shop.test"]}
        async with sandboxed(tmp_path, allow) as (plugin, sandbox, manager, port):
            reader, writer = await tunnel(plugin.session, port)
            sandbox.set_options({"network_allowlist": ["shop.test"]})
            snapshot = plugin.contribute_tools()[1]
            context = manager_context(manager, tmp_path)
            assert (await wrapped(manager, snapshot, context)({}, context)).success
            await closes_within(reader, 1.0)
            writer.close()

    async def test_other_tools_pass_the_pre_hook_untouched(self, tmp_path):
        async with sandboxed(tmp_path, {}) as (plugin, sandbox, manager, port):
            session = plugin.session
            seen = []

            async def record():
                seen.append("touched")

            session.rejudge_connections = record
            session.drop_connections = record
            context = manager_context(manager, tmp_path)
            args = {"path": "x.txt"}
            for name in ("read", "write", "bash"):
                assert (
                    await plugin.pre_tool_execute(
                        args, tool_name=name, tool=None, context=context
                    )
                    is None
                )
            assert seen == [] and args == {"path": "x.txt"}

    async def test_required_os_confinement_refuses_launch(self, tmp_path):
        plugin, driver = await loaded(tmp_path)
        confined = SandboxPlugin(options={"os_enforce": "require"})
        services = confined.runtime_services(None)
        assert services["network_policy"].requires_os_confinement
        context = tool_context(plugin, tmp_path, services=services)
        result = await call(
            plugin, "browser_navigate", VALID_ARGS["browser_navigate"], context
        )
        assert not result.success and "os_enforce" in result.error
        assert driver.plans == []
        plain = tool_context(plugin, tmp_path)
        assert (await call(plugin, "browser_snapshot", {}, plain)).success
        result = await call(plugin, "browser_snapshot", {}, context)
        assert "os_enforce" in result.error
        assert driver.alive() == [] and plugin.session.state == "new"
        await plugin.on_unload()

    async def test_empty_path_and_files_reach_no_sandbox_path_check(self, tmp_path):
        plugin, driver = scripted_plugin()
        manager = PluginManager()
        manager.register(SandboxPlugin(options={"fs_write": "deny"}))
        manager.register(plugin)
        await manager.load_all(plugin_context(tmp_path, manager))
        tools = {tool.tool_name: tool for tool in plugin.contribute_tools()}
        context = manager_context(manager, tmp_path)
        nav = tools["browser_navigate"]
        await wrapped(manager, nav, context)({"url": "http://shop.test/"}, context)
        shot = tools["browser_screenshot"]
        filled = {"ref": "", "full_page": False, "path": "", "tab": ""}
        for path in ("", "  "):
            result = await wrapped(manager, shot, context)(
                {**filled, "path": path}, context
            )
            assert result.error is None, result.error
            assert result.metadata["session_metadata"]["browser"]["ok"] is True
        with pytest.raises(PluginBlockError, match="shot.png"):
            await wrapped(manager, shot, context)(
                {**filled, "path": "shot.png"}, context
            )
        assert not (tmp_path / "shot.png").exists()
        act = tools["browser_act"]
        for files in ("", [], "[]"):
            changed = await plugin.pre_tool_execute(
                {"actions": [], "files": files, "tab": ""},
                tool_name="browser_act",
                tool=act,
                context=context,
            )
            assert changed == {"actions": [], "tab": ""}
        await manager.unload_all()

    async def test_files_given_as_text_are_checked_by_the_sandbox_as_paths(
        self, tmp_path
    ):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (tmp_path / "outside.txt").write_text("x")
        plugin, driver = scripted_plugin()
        manager = PluginManager()
        manager.register(SandboxPlugin(options={"fs_read": "workspace"}))
        manager.register(plugin)
        await manager.load_all(plugin_context(workspace, manager))
        act = plugin.contribute_tools()[2]
        context = manager_context(manager, workspace)
        args = {
            "actions": [{"kind": "upload", "ref": "e3", "files": [0]}],
            "files": json.dumps(["../outside.txt"]),
        }
        with pytest.raises(PluginBlockError, match="outside working directory"):
            await wrapped(manager, act, context)(args, context)
        changed = await plugin.pre_tool_execute(
            dict(args), tool_name="browser_act", tool=act, context=context
        )
        assert changed["files"] == ["../outside.txt"]
        assert (
            await plugin.pre_tool_execute(
                {"files": ["a"]}, tool_name="browser_act", tool=act, context=context
            )
            is None
        )
        assert driver.plans == []
        await manager.unload_all()


class TestDownloads:
    async def run_download(self, work, sandbox_options=None, workspace=None):
        """A click that starts one download, through the manager; the act result."""
        plugin, driver = scripted_plugin()
        manager = PluginManager()
        if sandbox_options is not None:
            manager.register(SandboxPlugin(options=sandbox_options))
        manager.register(plugin)
        load = plugin_context(work, manager)
        if workspace is not None:
            load.host_agent.workspace = workspace
        await manager.load_all(load)
        tools = {tool.tool_name: tool for tool in plugin.contribute_tools()}
        context = manager_context(manager, work)
        await wrapped(manager, tools["browser_snapshot"], context)({}, context)
        page = driver.browser.page
        item = ScriptedDownload("report.csv", b"1")
        page.handlers["click"] = lambda **_: driver.browser.sink.download_started(
            page, item
        )
        result = await wrapped(manager, tools["browser_act"], context)(
            {"actions": [{"kind": "click", "ref": "e2"}]}, context
        )
        await manager.unload_all()
        assert driver.alive() == []
        return result, item

    async def test_write_deny_refuses_the_download_and_creates_nothing(self, tmp_path):
        result, item = await self.run_download(tmp_path, {"fs_write": "deny"})
        assert not (tmp_path / DOWNLOADS_DIR).exists()
        assert item.cancelled and item.saved_to is None
        body = result.output.split("</page_content>")[0]
        assert (
            "Download failed: report.csv (refused by the file-write policy: "
            "fs_write=deny)"
        ) in body

    async def test_workspace_write_allows_the_download(self, tmp_path):
        result, item = await self.run_download(tmp_path, {"fs_write": "workspace"})
        saved = tmp_path / DOWNLOADS_DIR / "report.csv"
        assert saved.read_bytes() == b"1" and not item.cancelled
        assert f"Downloaded: report.csv → {saved}" in result.output

    async def test_a_denied_path_refuses_the_download(self, tmp_path):
        denied = str(tmp_path / DOWNLOADS_DIR)
        result, item = await self.run_download(tmp_path, {"fs_deny": [denied]})
        assert not (tmp_path / DOWNLOADS_DIR).exists() and item.cancelled
        assert "refused by the file-write policy: path is denied" in result.output

    async def test_no_sandbox_saves_the_download(self, tmp_path):
        result, _ = await self.run_download(tmp_path)
        assert (tmp_path / DOWNLOADS_DIR / "report.csv").read_bytes() == b"1"

    async def test_downloads_follow_the_agents_working_directory(self, tmp_path):
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        second.mkdir()
        current = [first]
        plugin, driver = scripted_plugin()
        load = plugin_context(first)
        load.host_agent.workspace = SimpleNamespace(get=lambda: str(current[0]))
        await plugin.on_load(load)
        context = tool_context(plugin, first)
        assert (await call(plugin, "browser_snapshot", {}, context)).success

        async def download(name: str) -> None:
            page = driver.browser.page
            page.handlers["click"] = lambda **_: driver.browser.sink.download_started(
                page, ScriptedDownload(name, b"x")
            )
            await call(
                plugin,
                "browser_act",
                {"actions": [{"kind": "click", "ref": "e2"}]},
                tool_context(plugin, current[0]),
            )

        await download("a.csv")
        current[0] = second
        await download("b.csv")
        await plugin.session.close_browser()
        await call(plugin, "browser_snapshot", {}, tool_context(plugin, second))
        await download("c.csv")
        assert (first / DOWNLOADS_DIR / "a.csv").exists()
        assert sorted(p.name for p in (second / DOWNLOADS_DIR).iterdir()) == [
            "b.csv",
            "c.csv",
        ]
        await plugin.on_unload()


def _cfg(tmp_path, tool_format: str = "native") -> AgentConfig:
    return AgentConfig(
        name="browser_agent",
        system_prompt="You are a test agent.",
        include_hints_in_prompt=False,
        tool_format=tool_format,
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="none"),
    )


class NativeScript(ScriptedLLM):
    """Answers each request with the next scripted native tool call, then text."""

    def __init__(self, calls: list[tuple[str, dict]]):
        super().__init__(["done"])
        self.pending = list(calls)
        self._calls: list[NativeToolCall] = []

    @property
    def last_tool_calls(self) -> list[NativeToolCall]:
        return self._calls

    async def chat(self, messages, **kwargs):
        self._calls = []
        if self.pending:
            name, args = self.pending.pop(0)
            self.call_log.append(self._normalize_messages(messages))
            self.call_count += 1
            self._calls = [
                NativeToolCall(f"call-{self.call_count}", name, json.dumps(args))
            ]
            yield ""
            return
        async for chunk in super().chat(messages, **kwargs):
            yield chunk


class TestThroughTheAgent:
    async def test_prompt_block_and_tools_follow_the_plugin_both_ways(self, tmp_path):
        bare = await kt.Agent.build(_cfg(tmp_path), llm=ScriptedLLM(["x"]))
        assert not bare.plugins.is_enabled("browser"), "catalog entry stays off"
        assert "## Browser pages" not in bare.get_system_prompt()
        assert "page_content" not in bare.get_system_prompt()
        assert not [n for n in bare.registry.list_tools() if n.startswith("browser_")]
        plugin, driver = scripted_plugin()
        agent = await kt.Agent.build(
            _cfg(tmp_path), llm=ScriptedLLM(["x"]), plugins=[plugin]
        )
        await agent.start()
        try:
            assert PROMPT in agent.get_system_prompt()
            assert set(TOOLS) <= set(agent.registry.list_tools())
            agent.plugins.disable("browser")
            assert PROMPT not in agent.get_system_prompt()
            assert not [
                n for n in agent.registry.list_tools() if n.startswith("browser_")
            ]
            agent.plugins.enable("browser")
            await agent.plugins.load_pending()
            assert PROMPT in agent.get_system_prompt()
        finally:
            await agent.stop()
        assert driver.alive() == []

    async def test_tools_carry_the_plugin_origin_and_switch_off_with_the_block(
        self, tmp_path
    ):
        plugin, driver = scripted_plugin()
        agent = await kt.Agent.build(
            _cfg(tmp_path), llm=ScriptedLLM(["x"]), plugins=[plugin]
        )
        await agent.start()
        try:
            for name in TOOLS:
                state = tool_switch_state(agent, name)
                assert state == {
                    "origin": {
                        "kind": "plugin",
                        "name": "browser",
                        "label": "plugin:browser",
                    },
                    "enabled": True,
                }
            for name in TOOLS[1:]:
                switch_tool(agent, name, False)
            assert PROMPT in agent.get_system_prompt(), "one browser tool is still on"
            switch_tool(agent, TOOLS[0], False)
            assert PROMPT not in agent.get_system_prompt(), "every browser tool is off"
            agent.plugins.disable("browser")
            agent.plugins.enable("browser")
            await agent.plugins.load_pending()
            assert not any(agent.is_tool_enabled(name) for name in TOOLS)
            assert PROMPT not in agent.get_system_prompt(), "a re-enable keeps them off"
            switch_tool(agent, "browser_snapshot", True)
            assert PROMPT in agent.get_system_prompt()
            assert tool_switch_state(agent, "browser_snapshot")["enabled"] is True
        finally:
            await agent.stop()
        assert driver.alive() == []

    @pytest.mark.parametrize("shape", ["list", "json"])
    async def test_actions_as_native_list_or_json_string(self, tmp_path, shape):
        actions = [{"kind": "type", "ref": "e3", "text": "Kohaku"}]
        value = actions if shape == "list" else json.dumps(actions)
        llm = NativeScript(
            [("browser_snapshot", {}), ("browser_act", {"actions": value})]
        )
        plugin, driver = scripted_plugin()
        agent = await kt.Agent.build(_cfg(tmp_path), llm=llm, plugins=[plugin])
        await agent.start()
        try:
            await agent.run("fill the name")
        finally:
            await agent.stop()
        fills = [c for n, c in driver.browser.page.calls if n == "fill"]
        assert fills and fills[0]["text"] == "Kohaku" and fills[0]["ref"] == "e3"
        seen = json.dumps(llm.call_log[-1])
        assert "1. type e3: done" in seen
        assert driver.alive() == []

    async def test_actions_as_the_text_format_call_body(self, tmp_path):
        body = json.dumps([{"kind": "type", "ref": "e3", "text": "Kohaku"}])
        llm = ScriptedLLM(
            [
                "[/browser_snapshot][browser_snapshot/]",
                f"[/browser_act]\n{body}\n[browser_act/]",
                "done",
            ]
        )
        plugin, driver = scripted_plugin()
        agent = await kt.Agent.build(
            _cfg(tmp_path, "bracket"), llm=llm, plugins=[plugin]
        )
        await agent.start()
        try:
            await agent.run("fill the name")
        finally:
            await agent.stop()
        fills = [c for n, c in driver.browser.page.calls if n == "fill"]
        assert fills and fills[0]["text"] == "Kohaku"
        assert "1. type e3: done" in json.dumps(llm.call_log[-1])
        assert driver.alive() == []


class TestPrompt:
    async def test_prompt_content_until_the_plugin_ends(self, tmp_path):
        plugin, _ = scripted_plugin()
        context = plugin_context(tmp_path)
        assert plugin.get_prompt_content(context) == PROMPT, "built before load"
        await plugin.on_load(context)
        assert plugin.get_prompt_content(context) == PROMPT
        await plugin.on_agent_stop()
        assert plugin.get_prompt_content(context) is None
        await plugin.on_load(context)
        assert plugin.get_prompt_content(context) == PROMPT
        await plugin.on_unload()
        assert plugin.get_prompt_content(context) is None

    def test_prompt_states_the_two_rules_and_stays_small(self):
        assert "page_content" in PROMPT and "never instructions" in PROMPT
        assert "latest" in PROMPT and "ref" in PROMPT
        assert len(PROMPT) <= 600
        for phrase in DISCOURAGING:
            assert phrase not in PROMPT.lower()

    def test_prompt_points_at_the_page_text_results_already_carry(self):
        assert "snapshot first" not in PROMPT.lower()
        assert "browser_navigate and browser_act show the page text" in PROMPT
