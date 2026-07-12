"""R1-32 §6+§7: engine-aware TUI slash dispatch is bound to the ACTIVE tab.

§6 — the runner must read the ACTIVE creature's *live aggregated* command
registry (built-ins + plugin contributions) per dispatch, not a built-in-only
snapshot. ``/goal`` is plugin-contributed, so a built-in-only registry can never
find it; the live registry must surface it when ``GoalPlugin`` is enabled and
drop it when the plugin is disabled.

§7 — the trusted context (service target AND rendered notice target) must be
rebuilt from the current active tab before every engine-aware dispatch, so
``/drives``/``/goal`` typed on a sibling creature tab operate on and render to
that creature, not the launch focus.

Exercised through ``_dispatch_active_engine_command`` without mounting Textual.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.plugins.goal.plugin import GoalPlugin
from kohakuterrarium.builtins.user_commands import (
    get_builtin_user_command,
    list_builtin_user_commands,
)
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.engine_cli_commands import (
    _active_command_target,
    _dispatch_active_engine_command,
    _engine_command_context,
    _live_command_registry,
)
from kohakuterrarium.testing.terrarium import _FakeAgent


def _builtin_snapshot() -> dict:
    """The pre-fix built-in-only registry the old runner snapshotted."""
    return {n: get_builtin_user_command(n) for n in list_builtin_user_commands()}


class _RegistryAgent(_FakeAgent):
    """Fake agent exposing a fixed live user-command registry."""

    def __init__(self, name: str, registry: dict) -> None:
        super().__init__(name=name)
        self.session = None
        self._registry = registry

    def list_user_commands(self) -> dict:
        return dict(self._registry)


class _PluginAgent(_FakeAgent):
    """Fake agent whose live registry aggregates built-ins + plugin commands.

    Mirrors ``Agent._aggregate_user_commands`` closely enough that a plugin
    toggle shows through ``list_user_commands()`` exactly as it does on the real
    agent, so the /goal enable→disable transition is exercised end to end.
    """

    def __init__(self, name: str, plugins: PluginManager) -> None:
        super().__init__(name=name)
        self.session = None
        self.plugins = plugins

    def list_user_commands(self) -> dict:
        commands = _builtin_snapshot()
        for contribution in self.plugins.collect_user_commands():
            commands[contribution.name] = contribution.command
        return commands


class _RecordingCommand:
    """A ``needs_engine`` command recording the context each call received."""

    name = "probe"
    aliases: list[str] = []
    needs_engine = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, args, context):
        creature_id = context.extra.get("creature_id")
        self.calls.append(
            {
                "args": args,
                "creature_id": creature_id,
                "agent": context.agent,
                "service": context.extra.get("service"),
                "is_operator": context.extra.get("is_operator"),
            }
        )
        return SimpleNamespace(output=f"probe on {creature_id}", error=None)


class _TabTUI:
    """TUISession stub with a settable active tab + captured notices."""

    def __init__(self, active: str) -> None:
        self._active = active
        self.notices: list[dict] = []

    def get_active_tab(self) -> str:
        return self._active

    def add_system_notice(
        self, text: str, command: str = "", error: bool = False, target: str = ""
    ) -> None:
        self.notices.append(
            {"text": text, "command": command, "error": error, "target": target}
        )


async def _engine_with_agents(
    pairs: list[tuple[str, _FakeAgent]],
) -> tuple[Terrarium, str]:
    """Drive-enabled engine with every ``(creature_id, agent)`` in ONE graph."""
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    graph_id: str | None = None
    for cid, agent in pairs:
        creature = Creature(creature_id=cid, name=cid, agent=agent, is_privileged=True)
        added = await engine.add_creature(creature, graph=graph_id)
        if graph_id is None:
            graph_id = added.graph_id
    assert graph_id is not None
    return engine, graph_id


class TestActiveTabContext:
    """§7: the service + render target follow the active tab, not launch focus."""

    @pytest.mark.asyncio
    async def test_dispatch_targets_active_sibling_tab(self):
        probe = _RecordingCommand()
        registry = {"probe": probe}
        root_agent = _RegistryAgent("root", registry)
        bob_agent = _RegistryAgent("bob", registry)
        engine, graph_id = await _engine_with_agents(
            [("root", root_agent), ("bob", bob_agent)]
        )
        try:
            # Active tab is the SIBLING, but the launch focus is root.
            tui = _TabTUI("bob")
            handled = await _dispatch_active_engine_command(
                "/probe hi",
                tui,
                engine,
                root_agent,
                "root",
                graph_id,
                _builtin_snapshot(),
            )
            assert handled is True
            # Service call target follows the active tab.
            assert probe.calls[-1]["creature_id"] == "bob"
            assert probe.calls[-1]["agent"] is bob_agent
            assert probe.calls[-1]["is_operator"] is True
            # Rendered notice target follows the active tab.
            assert tui.notices[-1]["target"] == "bob"
            assert tui.notices[-1]["error"] is False

            # Fail-first anchor: the OLD launch-bound static context always
            # carried the focus creature_id regardless of the visible tab.
            static_ctx = _engine_command_context(root_agent, engine, "root", registry)
            assert static_ctx.extra["creature_id"] == "root"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_dispatch_targets_active_focus_tab(self):
        probe = _RecordingCommand()
        registry = {"probe": probe}
        root_agent = _RegistryAgent("root", registry)
        bob_agent = _RegistryAgent("bob", registry)
        engine, graph_id = await _engine_with_agents(
            [("root", root_agent), ("bob", bob_agent)]
        )
        try:
            tui = _TabTUI("root")
            handled = await _dispatch_active_engine_command(
                "/probe hi",
                tui,
                engine,
                root_agent,
                "root",
                graph_id,
                _builtin_snapshot(),
            )
            assert handled is True
            assert probe.calls[-1]["creature_id"] == "root"
            assert probe.calls[-1]["agent"] is root_agent
            assert tui.notices[-1]["target"] == "root"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_channel_and_unknown_tabs_fall_back_to_focus(self):
        probe = _RecordingCommand()
        registry = {"probe": probe}
        root_agent = _RegistryAgent("root", registry)
        engine, graph_id = await _engine_with_agents([("root", root_agent)])
        try:
            for active in ("#chat", "ghost"):
                probe.calls.clear()
                tui = _TabTUI(active)
                handled = await _dispatch_active_engine_command(
                    "/probe hi",
                    tui,
                    engine,
                    root_agent,
                    "root",
                    graph_id,
                    _builtin_snapshot(),
                )
                assert handled is True
                assert probe.calls[-1]["creature_id"] == "root"
                assert tui.notices[-1]["target"] == "root"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_active_command_target_resolves_sibling_agent(self):
        root_agent = _RegistryAgent("root", {})
        bob_agent = _RegistryAgent("bob", {})
        engine, graph_id = await _engine_with_agents(
            [("root", root_agent), ("bob", bob_agent)]
        )
        try:
            cid, agent = _active_command_target(
                _TabTUI("bob"), engine, root_agent, "root", graph_id
            )
            assert cid == "bob"
            assert agent is bob_agent
        finally:
            await engine.shutdown()


class TestLivePluginRegistry:
    """§6: plugin-contributed /goal is reachable only through the live registry."""

    @pytest.mark.asyncio
    async def test_goal_absent_from_builtin_snapshot(self):
        # Fail-first anchor: the pre-fix built-in-only registry the runner used
        # never contains the plugin-contributed /goal command.
        assert "goal" not in _builtin_snapshot()

    @pytest.mark.asyncio
    async def test_live_registry_surfaces_goal_when_plugin_enabled(self):
        plugins = PluginManager()
        plugins.register(GoalPlugin())
        agent = _PluginAgent("root", plugins)
        registry = _live_command_registry(agent, _builtin_snapshot())
        assert "goal" in registry
        assert registry["goal"].needs_engine is True

    @pytest.mark.asyncio
    async def test_goal_dispatches_then_disappears_on_disable(self):
        plugins = PluginManager()
        plugins.register(GoalPlugin())
        agent = _PluginAgent("root", plugins)
        engine, graph_id = await _engine_with_agents([("root", agent)])
        try:
            # Enabled: /goal reaches the command against the trusted service
            # context and renders a real (non-error) result on the focus tab.
            tui = _TabTUI("root")
            handled = await _dispatch_active_engine_command(
                "/goal list",
                tui,
                engine,
                agent,
                "root",
                graph_id,
                _builtin_snapshot(),
            )
            assert handled is True
            assert tui.notices[-1]["error"] is False
            assert tui.notices[-1]["target"] == "root"
            assert "unavailable" not in tui.notices[-1]["text"].lower()

            # Disabled: the plugin's /goal leaves the live registry, so the
            # dispatch falls through (handled False, nothing rendered).
            plugins.disable("goal")
            assert "goal" not in _live_command_registry(agent, _builtin_snapshot())
            tui2 = _TabTUI("root")
            handled2 = await _dispatch_active_engine_command(
                "/goal list",
                tui2,
                engine,
                agent,
                "root",
                graph_id,
                _builtin_snapshot(),
            )
            assert handled2 is False
            assert tui2.notices == []
        finally:
            await engine.shutdown()
