"""R1-32: the Textual ``/drives`` intercept must only swallow the bare command,
and typed subcommands must run against a trusted local-console context.

``/drives`` (no arguments) opens the modal panel; ``/drives list|show|…`` falls
through the modal dispatcher and is then executed by the runner against the
trusted engine-command context (``_engine_command_context`` /
``_dispatch_engine_command``) so it reaches the live Drive service instead of the
engine-agnostic agent pipeline. Exercised without mounting the Textual app.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.user_commands import (
    get_builtin_user_command,
    list_builtin_user_commands,
)
from kohakuterrarium.builtins.user_commands import drives as drives_mod
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.studio.sessions import drives as drives_facade
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.engine_cli import _handle_tui_slash
from kohakuterrarium.terrarium.engine_cli_commands import (
    _dispatch_engine_command,
    _engine_command_context,
)
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent


class _FakeTUI:
    """Minimal TUISession stub tracking modal opens + captured notices."""

    def __init__(self) -> None:
        self.drive_panel_opened = 0
        self.model_picker_opened = 0
        self.modules_opened = 0
        self.notices: list[dict] = []

    async def show_drive_panel(self) -> None:
        self.drive_panel_opened += 1

    async def show_model_picker_modal(self, agent) -> None:
        self.model_picker_opened += 1

    async def show_modules_modal(self, agent) -> None:
        self.modules_opened += 1

    def add_system_notice(
        self, text: str, command: str = "", error: bool = False, target: str = ""
    ) -> None:
        self.notices.append(
            {"text": text, "command": command, "error": error, "target": target}
        )


@pytest.mark.asyncio
async def test_bare_drives_opens_panel() -> None:
    tui = _FakeTUI()
    handled = await _handle_tui_slash("/drives", tui, SimpleNamespace())
    assert handled is True
    assert tui.drive_panel_opened == 1


@pytest.mark.asyncio
async def test_bare_drive_singular_opens_panel() -> None:
    tui = _FakeTUI()
    handled = await _handle_tui_slash("/drive", tui, SimpleNamespace())
    assert handled is True
    assert tui.drive_panel_opened == 1


@pytest.mark.asyncio
async def test_drives_list_falls_through_to_registry() -> None:
    tui = _FakeTUI()
    handled = await _handle_tui_slash("/drives list", tui, SimpleNamespace())
    # Subcommand is NOT intercepted — the runner injects it into the agent's
    # slash dispatch instead of opening the modal.
    assert handled is False
    assert tui.drive_panel_opened == 0


@pytest.mark.asyncio
async def test_drives_show_falls_through_to_registry() -> None:
    tui = _FakeTUI()
    handled = await _handle_tui_slash("/drives show d1", tui, SimpleNamespace())
    assert handled is False
    assert tui.drive_panel_opened == 0


async def _drive_engine_with_root():
    """A Drive-enabled engine with one privileged ``root`` creature."""
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    agent = _FakeAgent(name="root")
    agent.session = None  # _engine_command_context reads focus.session
    root = Creature(creature_id="root", name="root", agent=agent, is_privileged=True)
    await engine.add_creature(root)
    return engine, agent


class TestTrustedEngineCommandContext:
    """R1-32: engine_cli builds the trusted local-console context and dispatches
    typed ``/drives`` subcommands against the live Drive service."""

    def _commands(self):
        return {n: get_builtin_user_command(n) for n in list_builtin_user_commands()}

    @pytest.mark.asyncio
    async def test_context_is_explicit_trusted_local_console(self):
        engine, agent = await _drive_engine_with_root()
        try:
            ctx = _engine_command_context(agent, engine, "root", self._commands())
            assert isinstance(ctx.extra["service"], LocalTerrariumService)
            assert ctx.extra["creature_id"] == "root"
            assert ctx.extra["principal"] == "user:local"
            # Operator authority is set EXPLICITLY (not a default-true).
            assert ctx.extra["is_operator"] is True
            assert drives_mod._resolve(ctx).is_operator is True
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_typed_drives_list_returns_real_data(self):
        engine, agent = await _drive_engine_with_root()
        try:
            service = LocalTerrariumService(engine)
            info = await service.list_creatures()
            gid = info[0].graph_id
            await drives_facade.create_record(
                service,
                graph_id=gid,
                actor=ActorRef("user", "local"),
                body={
                    "kind": "generic",
                    "title": "watch-deploy",
                    "scope_type": "graph",
                },
                is_privileged=True,
                operator=True,
            )
            commands = self._commands()
            ctx = _engine_command_context(agent, engine, "root", commands)
            tui = _FakeTUI()
            handled = await _dispatch_engine_command(
                "/drives list", tui, commands, {}, ctx
            )
            assert handled is True
            assert len(tui.notices) == 1
            notice = tui.notices[0]
            # Real Drive data — NOT the "unavailable" honesty error the poor
            # agent-only context produced before the fix.
            assert notice["error"] is False
            assert "watch-deploy" in notice["text"]
            assert notice["target"] == "root"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_context_without_explicit_flag_stays_unprivileged(self):
        # A non-console adapter that omits the flag must resolve unprivileged —
        # the safe default is never a silent operator grant (R1-21/R1-32).
        engine, agent = await _drive_engine_with_root()
        try:
            service = LocalTerrariumService(engine)
            flagless = UserCommandContext(
                extra={
                    "service": service,
                    "creature_id": "root",
                    "principal": "user:local",
                }
            )
            assert drives_mod._resolve(flagless).is_operator is False
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_non_engine_command_falls_through(self):
        # A command that does not declare needs_engine is NOT intercepted here —
        # it keeps routing to the creature's own pipeline.
        engine, agent = await _drive_engine_with_root()
        try:
            commands = self._commands()
            ctx = _engine_command_context(agent, engine, "root", commands)
            tui = _FakeTUI()
            handled = await _dispatch_engine_command("/clear", tui, commands, {}, ctx)
            assert handled is False
            assert tui.notices == []
        finally:
            await engine.shutdown()
