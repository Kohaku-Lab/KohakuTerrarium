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
from dataclasses import fields
from types import SimpleNamespace

import pytest

import kohakuterrarium as kt
from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.builtins.plugin_catalog import is_default_on, lookup_plugin
from kohakuterrarium.builtins.plugins.browser.plugin import (
    DOWNLOADS_DIR,
    PROMPT,
    BrowserPlugin,
)
from kohakuterrarium.builtins.plugins.sandbox.plugin import SandboxPlugin
from kohakuterrarium.core.config.types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.errors import ConfigError
from kohakuterrarium.llm.base import NativeToolCall
from kohakuterrarium.modules.plugin.base import PluginBlockError
from kohakuterrarium.modules.plugin.manager.manager import PluginManager
from kohakuterrarium.modules.plugin.option_validation import PluginOptionError
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.terrarium.channels.channels import TERRARIUM_ENGINE_KEY
from kohakuterrarium.testing.llm import ScriptedLLM
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
        with pytest.raises(ConfigError, match="env:VAR_NAME"):
            BrowserPlugin(secrets={"pw": "literal-password"})


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

    async def test_restricted_engine_allows_a_headless_throwaway_profile(
        self, tmp_path
    ):
        plugin, driver = await loaded(tmp_path)
        context = tool_context(plugin, tmp_path, environment=restricted())
        assert (await call(plugin, "browser_snapshot", {}, context)).success
        assert driver.plans[0].headless is True
        await plugin.on_unload()

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

    async def test_deny_drops_open_connections_of_a_running_browser(self, tmp_path):
        async def echo(reader, writer):
            while data := await reader.read(1024):
                writer.write(data)
                await writer.drain()
            writer.close()

        server = await asyncio.start_server(echo, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        plugin, driver = scripted_plugin(allow_loopback=True)
        sandbox = SandboxPlugin()
        manager = PluginManager()
        manager.register(sandbox)
        manager.register(plugin)
        await manager.load_all(plugin_context(tmp_path, manager))
        snapshot = plugin.contribute_tools()[1]
        context = manager_context(manager, tmp_path)
        assert (await wrapped(manager, snapshot, context)({}, context)).success
        reader, writer = await proxied(
            plugin.session,
            f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode(),
        )
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        assert head.startswith(b"HTTP/1.1 200")
        writer.write(b"ping")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(4), 5) == b"ping"
        sandbox.set_options({"network": "deny"})
        context = manager_context(manager, tmp_path)
        with pytest.raises(PluginBlockError):
            await wrapped(manager, snapshot, context)({}, context)
        assert await asyncio.wait_for(reader.read(), 5) == b"", "tunnel still open"
        assert plugin.session.state == "ready", "only connections are dropped"
        writer.close()
        server.close()
        await manager.unload_all()
        assert driver.alive() == []

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
