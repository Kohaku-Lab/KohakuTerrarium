"""Unit tests for :mod:`kohakuterrarium.builtins.ktui.page_modules`.

The Modules section lists a real engine-hosted creature's modules; a tool row
shows its origin and on/off state, and its menu switches the tool.
"""

from types import SimpleNamespace

import kohakuterrarium as kt
from kohakuterrarium.builtins.ktui.page_modules import Modules, _detail
from kohakuterrarium.core.config.types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.modules.tool.origin import ToolOrigin
from kohakuterrarium.terrarium.creature.host import Creature
from kohakuterrarium.terrarium.engine.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM


class _Probe(BaseTool):
    def __init__(self, name):
        super().__init__()
        self._name = name

    @property
    def tool_name(self):
        return self._name

    @property
    def description(self):
        return f"{self._name} tool"

    @property
    def execution_mode(self):
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(output="ran")


class _Shell:
    """The parts of ``KtuiShell`` the Modules section touches."""

    def __init__(self, engine, choices):
        self.engine = engine
        self.choices = list(choices)
        self.notices: list[tuple[str, str]] = []
        self.menus: list[list[str]] = []
        self.app = SimpleNamespace(
            notify=lambda text, tone: self.notices.append((text, tone))
        )
        self.chrome = SimpleNamespace(
            pages=SimpleNamespace(shown="chat", refresh=lambda force=False: None)
        )

    def spawn(self, coro):
        coro.close()

    async def panel(self, picker):
        self.menus.append([item["value"] for item in picker.items])
        return self.choices.pop(0)


async def _setup(tmp_path, choices=()):
    cfg = AgentConfig(
        name="alice",
        system_prompt="Test.",
        include_hints_in_prompt=False,
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="none"),
    )
    agent = await kt.Agent.build(cfg, llm=ScriptedLLM(["ok"]), tools=[_Probe("info")])
    agent.add_tool(_Probe("lab_query"), origin=ToolOrigin("mcp", "sandbox-lab"))
    engine = Terrarium()
    await engine.add_creature(
        Creature(creature_id="alice", name="alice", agent=agent), start=False
    )
    shell = _Shell(engine, choices)
    entry = SimpleNamespace(creature=SimpleNamespace(creature_id="alice"))
    modules = Modules(shell)
    await modules.load("alice")
    return engine, agent, shell, entry, modules


def _tool_row(modules, entry, name):
    groups = {g["value"]: g for g in modules.items(entry)}
    return next(
        row for row in groups["set:none:tool"]["children"] if row["label"] == name
    )


class TestModulesSection:
    async def test_tool_rows_show_origin_and_state(self, tmp_path):
        engine, agent, _, entry, modules = await _setup(tmp_path)
        try:
            agent.set_tool_enabled("info", False)
            await modules.load("alice")
            lab = _tool_row(modules, entry, "lab_query")
            assert lab["detail"] == "mcp:sandbox-lab · lab_query tool"
            assert lab["icon"] == "✓"
            assert _tool_row(modules, entry, "info")["icon"] == "○"
        finally:
            await engine.shutdown()

    async def test_menu_switches_a_tool_off_and_on(self, tmp_path):
        engine, agent, shell, entry, modules = await _setup(
            tmp_path, choices=["toggle", "toggle"]
        )
        try:
            await modules.act(entry, "tool:lab_query")
            assert shell.menus[0] == ["toggle"]
            assert agent.is_tool_enabled("lab_query") is False
            assert shell.notices[-1] == ("lab_query: off", "success")
            assert _tool_row(modules, entry, "lab_query")["icon"] == "○"
            await modules.act(entry, "tool:lab_query")
            assert agent.is_tool_enabled("lab_query") is True
            assert shell.notices[-1] == ("lab_query: on", "success")
        finally:
            await engine.shutdown()

    async def test_switching_off_a_framework_tool_warns(self, tmp_path):
        engine, agent, shell, entry, modules = await _setup(
            tmp_path, choices=["toggle"]
        )
        try:
            await modules.act(entry, "tool:info")
            assert agent.is_tool_enabled("info") is False
            assert shell.notices[-2] == ("info: off", "success")
            text, tone = shell.notices[-1]
            assert tone == "warning" and text.startswith("Warning: 'info'")
        finally:
            await engine.shutdown()


def test_detail_joins_origin_and_description():
    assert _detail({"description": "plain"}) == "plain"
    assert _detail({"origin": {"label": "builtin"}, "description": ""}) == "builtin"
    assert _detail({}) is None
