"""Unit tests for :mod:`kohakuterrarium.terrarium.engine_rich_cli`."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.bootstrap.agent_init import AgentInitMixin
from kohakuterrarium.builtins.cli_rich.input import RichCLIInput
from kohakuterrarium.builtins.inputs.cli import CLIInput
from kohakuterrarium.terrarium import engine_rich_cli
from kohakuterrarium.terrarium.engine_rich_cli import (
    _input_conflicts_with_terminal,
    run_engine_with_rich_cli,
)


class _FocusAgent(AgentInitMixin):
    """Minimal agent stub with slash-command wiring from :class:`AgentInitMixin`."""

    def __init__(self) -> None:
        self.input = CLIInput()
        self.session = None
        self._pending_resume_events = None
        self.config = SimpleNamespace(name="focus")
        self.output_router = SimpleNamespace(default_output=None)
        self.llm = SimpleNamespace(model="test-model", _profile_max_context=0)

    def llm_identifier(self) -> str:
        return "test/test-model"


class _FocusCreature:
    creature_id = "focus"

    def __init__(self) -> None:
        self.agent = _FocusAgent()
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True


class _FakeEngine:
    def __init__(self, creature: _FocusCreature) -> None:
        self._creature = creature

    def get_creature(self, creature_id: str) -> _FocusCreature:
        assert creature_id == self._creature.creature_id
        return self._creature

    def list_creatures(self) -> list[_FocusCreature]:
        return [self._creature]


class TestInputConflictsWithTerminal:
    def test_cli_input_conflicts(self) -> None:
        assert _input_conflicts_with_terminal(CLIInput()) is True

    def test_rich_cli_input_does_not_conflict(self) -> None:
        assert _input_conflicts_with_terminal(RichCLIInput()) is False


class TestRichCliInputSwapRewiresSlashCommands:
    """Mirrors the ``swap_input`` block in :func:`run_engine_with_rich_cli`."""

    def test_fresh_rich_cli_input_has_no_slash_registry(self) -> None:
        agent = _FocusAgent()
        agent.input = RichCLIInput()
        assert not agent.input._user_commands

    def test_reinit_user_commands_after_swap_registers_builtins(self) -> None:
        agent = _FocusAgent()
        AgentInitMixin._init_user_commands(agent)
        assert "clear" in agent.input._user_commands

        agent.input = RichCLIInput()
        assert not agent.input._user_commands
        AgentInitMixin._init_user_commands(agent)
        assert "clear" in agent.input._user_commands
        assert "help" in agent.input._user_commands

    @pytest.mark.asyncio
    async def test_try_slash_command_text_unknown_without_rewire(self) -> None:
        agent = _FocusAgent()
        agent.input = RichCLIInput()
        result = await agent._try_slash_command_text("/help")
        assert result is None

    @pytest.mark.asyncio
    async def test_try_slash_command_text_resolves_after_rewire(self) -> None:
        agent = _FocusAgent()
        agent.input = RichCLIInput()
        AgentInitMixin._init_user_commands(agent)
        result = await agent._try_slash_command_text("/help")
        assert result is not None


class TestRunEngineWithRichCli:
    @pytest.mark.asyncio
    async def test_input_swap_reinits_slash_commands(self, monkeypatch) -> None:
        """``run_engine_with_rich_cli`` must re-wire commands onto ``RichCLIInput``."""
        creature = _FocusCreature()
        engine = _FakeEngine(creature)
        during_run: dict[str, object] = {}

        async def _immediate_run(self) -> None:
            # ``finally`` restores the previous stdin-owning input after ``run()``.
            during_run["input_type"] = type(self.agent.input)
            during_run["commands"] = set(self.agent.input._user_commands)

        class _PassthroughOutput:
            def __init__(self, app) -> None:
                pass

        monkeypatch.setattr(engine_rich_cli.RichCLIApp, "run", _immediate_run)
        monkeypatch.setattr(engine_rich_cli, "RichCLIOutput", _PassthroughOutput)

        await run_engine_with_rich_cli(engine, creature.creature_id)

        assert during_run["input_type"] is RichCLIInput
        assert "clear" in during_run["commands"]
        assert "help" in during_run["commands"]
        assert isinstance(creature.agent.input, CLIInput)
