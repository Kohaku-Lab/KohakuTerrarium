"""Unit tests for extension instance injection (E7) —
:mod:`core.agent_extensions` + :mod:`modules.tool.function`.

Pins the headline contracts:

- ``@kt.tool`` turns a plain function into a working agent tool.
- ``Agent.build(tools=[...])`` puts injected tools in the INITIAL
  system prompt; ``agent.add_tool`` refreshes the live prompt.
- ``add_plugin`` after ``start()`` fires ``on_load`` (it never did).
- The plugin constructor contract is ONE spelling: ``cls(**options)``.
"""

import kohakuterrarium as kt
from kohakuterrarium.bootstrap.plugins import _load_one
from kohakuterrarium.core.config_types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.tool.function import FunctionTool
from kohakuterrarium.testing.llm import ScriptedLLM


def _cfg(tmp_path, name="ext_agent"):
    return AgentConfig(
        name=name,
        system_prompt="You are a test agent.",
        include_hints_in_prompt=False,
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="none"),
    )


@kt.tool
def add_numbers(a: int, b: int) -> str:
    """Add two integers."""
    return str(a + b)


@kt.tool
async def async_shout(text: str) -> str:
    """Uppercase the text."""
    return text.upper()


class TestFunctionTool:
    def test_decorator_builds_tool(self):
        assert isinstance(add_numbers, FunctionTool)
        assert add_numbers.tool_name == "add_numbers"
        assert add_numbers.description == "Add two integers."
        schema = add_numbers.get_parameters_schema()
        assert schema["properties"]["a"]["type"] == "integer"
        assert set(schema["required"]) == {"a", "b"}

    async def test_sync_function_executes(self):
        result = await add_numbers.execute({"a": 2, "b": 40})
        assert result.output == "42"

    async def test_async_function_executes(self):
        result = await async_shout.execute({"text": "hi"})
        assert result.output == "HI"

    async def test_missing_required_arg_is_error(self):
        result = await add_numbers.execute({"a": 1})
        assert result.error and "b" in result.error

    async def test_exception_becomes_tool_error(self):
        @kt.tool
        def boom() -> str:
            raise RuntimeError("nope")

        result = await boom.execute({})
        assert result.error and "nope" in result.error

    def test_factory_call_with_overrides(self):
        def fn(x: str) -> str:
            return x

        t = kt.tool(fn, name="renamed", description="custom desc")
        assert t.tool_name == "renamed"
        assert t.description == "custom desc"


class TestConstructorInjection:
    async def test_tools_land_in_initial_prompt_and_work(self, tmp_path):
        agent = await kt.Agent.build(
            _cfg(tmp_path),
            llm=ScriptedLLM(["[/add_numbers]a=1 b=2[add_numbers/]", "Done: 3"]),
            tools=[add_numbers],
        )
        # In the registry AND the initial aggregated prompt.
        assert "add_numbers" in agent.registry.list_tools()
        sys_prompt = agent._controller_config.system_prompt
        assert "add_numbers" in sys_prompt

    async def test_plugins_kwarg_registers_enabled(self, tmp_path):
        class Probe(BasePlugin):
            name = "probe"

        agent = await kt.Agent.build(
            _cfg(tmp_path), llm=ScriptedLLM(["x"]), plugins=[Probe()]
        )
        names = {p["name"] for p in agent.plugins.list_plugins()}
        assert "probe" in names
        assert agent.plugins.is_enabled("probe")


class TestRuntimeAdds:
    async def test_add_tool_refreshes_live_prompt(self, tmp_path):
        agent = await kt.Agent.build(_cfg(tmp_path), llm=ScriptedLLM(["x"]))
        await agent.start()
        try:
            assert "add_numbers" not in agent._controller_config.system_prompt
            agent.add_tool(add_numbers)
            # Registry + executor + the LIVE conversation system message.
            assert "add_numbers" in agent.registry.list_tools()
            sys_msg = agent.controller.conversation.get_system_message()
            assert "add_numbers" in sys_msg.content
        finally:
            await agent.stop()

    async def test_add_plugin_after_start_gets_on_load(self, tmp_path):
        loads: list[str] = []

        class LateProbe(BasePlugin):
            name = "late_probe"

            async def on_load(self, context=None):
                loads.append("loaded")

        agent = await kt.Agent.build(_cfg(tmp_path), llm=ScriptedLLM(["x"]))
        await agent.start()
        try:
            await agent.add_plugin(LateProbe())
            # E7 fix: post-start registration used to never fire on_load.
            assert loads == ["loaded"]
        finally:
            await agent.stop()


class TestPluginConstructorContract:
    def test_no_loader_path_uses_kwargs_contract(self, tmp_path, monkeypatch):
        # Both load paths must call ``cls(**options)`` — the no-loader
        # path used ``cls(options=options)``, silently dropping every
        # plugin written for the documented contract.
        import sys
        import textwrap

        mod_dir = tmp_path / "mods"
        mod_dir.mkdir()
        (mod_dir / "kwplug.py").write_text(
            textwrap.dedent("""
                from kohakuterrarium.modules.plugin.base import BasePlugin

                class KwPlugin(BasePlugin):
                    name = "kwplug"

                    def __init__(self, threshold=0):
                        self.threshold = threshold
                """),
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(mod_dir))
        plugin = _load_one(
            {
                "name": "kwplug",
                "module": "kwplug",
                "class": "KwPlugin",
                "options": {"threshold": 7},
            },
            loader=None,
            strict=True,
        )
        sys.modules.pop("kwplug", None)
        assert plugin is not None
        assert plugin.threshold == 7


class TestAsyncContextManager:
    async def test_async_with_starts_and_stops(self, tmp_path):
        # The Agent.build docstring promises ``async with agent`` — pin it.
        agent = await kt.Agent.build(_cfg(tmp_path), llm=ScriptedLLM(["x"]))
        assert agent.is_running is False
        async with agent as a:
            assert a is agent
            assert agent.is_running is True
        assert agent.is_running is False

    async def test_async_with_composes_with_explicit_start(self, tmp_path):
        agent = await kt.Agent.build(_cfg(tmp_path), llm=ScriptedLLM(["x"]))
        await agent.start()
        # Entering after an explicit start must not double-start.
        async with agent:
            assert agent.is_running is True
        assert agent.is_running is False


class TestMCPAlwaysOn:
    async def test_configless_agent_gets_mcp_manager(self, tmp_path):
        from kohakuterrarium.core.agent_mcp import init_mcp

        agent = await kt.Agent.build(_cfg(tmp_path), llm=ScriptedLLM(["x"]))
        await init_mcp(agent)
        # E7: the manager exists even with no mcp_servers config — the
        # ``mcp_connect`` runtime tool can actually work now.
        assert agent._mcp_manager is not None
