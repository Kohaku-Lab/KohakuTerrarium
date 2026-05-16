"""Unit tests for :mod:`kohakuterrarium.modules.input.base`.

Behavior-first: BaseInputModule lifecycle, slash-command dispatch order
(exact command → alias → skill fallback → None), interactive data
payload handoff to render_command_data, and follow-up execution.
"""

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.modules.input.base import BaseInputModule, InputModule
from kohakuterrarium.modules.user_command.base import (
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)
from kohakuterrarium.skills.registry import Skill


class _ConcreteInput(BaseInputModule):
    """Minimal input module that satisfies the abstract get_input."""

    def __init__(self):
        super().__init__()
        self._queued: list[TriggerEvent] = []

    async def get_input(self) -> TriggerEvent | None:
        return self._queued.pop(0) if self._queued else None


class _FakeCommand:
    """A registered user command that records its invocation."""

    def __init__(self, name, aliases=None, result=None):
        self.name = name
        self.aliases = aliases or []
        self.description = "fake"
        self.layer = CommandLayer.INPUT
        self._result = result or UserCommandResult(output=f"{name} ran")
        self.calls: list[tuple[str, object]] = []

    async def execute(self, args, context):
        self.calls.append((args, context))
        return self._result


class _FakeSkillRegistry:
    def __init__(self, skills):
        self._skills = {s.name: s for s in skills}

    def get(self, name):
        return self._skills.get(name)


class _FakeAgent:
    def __init__(self, skills=None):
        self.skills = skills


def _context(agent=None):
    return UserCommandContext(agent=agent, extra={})


class TestLifecycle:
    async def test_start_sets_running(self):
        mod = _ConcreteInput()
        assert mod.is_running is False
        await mod.start()
        assert mod.is_running is True

    async def test_stop_clears_running(self):
        mod = _ConcreteInput()
        await mod.start()
        await mod.stop()
        assert mod.is_running is False


class TestSetUserCommands:
    def test_alias_map_built_from_command_aliases(self):
        mod = _ConcreteInput()
        cmd = _FakeCommand("model", aliases=["m", "llm"])
        mod.set_user_commands({"model": cmd}, _context())
        assert mod._command_alias_map == {"m": "model", "llm": "model"}


class TestTryUserCommand:
    async def test_non_slash_text_returns_none(self):
        mod = _ConcreteInput()
        assert await mod.try_user_command("just text") is None

    async def test_exact_command_match_executes(self):
        mod = _ConcreteInput()
        cmd = _FakeCommand("help")
        mod.set_user_commands({"help": cmd}, _context())
        result = await mod.try_user_command("/help")
        assert result.output == "help ran"
        assert cmd.calls[0][0] == ""

    async def test_alias_resolves_to_canonical_command(self):
        mod = _ConcreteInput()
        cmd = _FakeCommand("model", aliases=["m"])
        mod.set_user_commands({"model": cmd}, _context())
        result = await mod.try_user_command("/m gpt-5")
        assert result.output == "model ran"
        # The alias dispatched to the canonical command with the args.
        assert cmd.calls[0][0] == "gpt-5"

    async def test_command_registry_injected_into_context_extra(self):
        # Contract: try_user_command exposes the full command map to the
        # command via ctx.extra["command_registry"].
        mod = _ConcreteInput()
        cmd = _FakeCommand("help")
        ctx = _context()
        mod.set_user_commands({"help": cmd}, ctx)
        await mod.try_user_command("/help")
        passed_ctx = cmd.calls[0][1]
        assert passed_ctx.extra["command_registry"] == {"help": cmd}

    async def test_unknown_slash_falls_through_to_skill(self):
        # No matching user command → /<skill-name> dispatches the skill.
        skill = Skill(name="review", description="review code", body="DO THE REVIEW")
        agent = _FakeAgent(skills=_FakeSkillRegistry([skill]))
        mod = _ConcreteInput()
        mod.set_user_commands({}, _context(agent))
        result = await mod.try_user_command("/review src/")
        assert result is not None
        assert result.consumed is False  # non-consuming injection turn
        assert "review" in result.output
        assert "DO THE REVIEW" in result.output
        assert "Arguments the user provided: src/" in result.output

    async def test_unknown_slash_with_no_skill_returns_none(self):
        agent = _FakeAgent(skills=_FakeSkillRegistry([]))
        mod = _ConcreteInput()
        mod.set_user_commands({}, _context(agent))
        # Unknown command, unknown skill → caller's legacy path takes over.
        assert await mod.try_user_command("/nonexistent") is None

    async def test_disabled_skill_returns_error_result(self):
        skill = Skill(name="lint", description="lint", body="lint it", enabled=False)
        agent = _FakeAgent(skills=_FakeSkillRegistry([skill]))
        mod = _ConcreteInput()
        mod.set_user_commands({}, _context(agent))
        result = await mod.try_user_command("/lint")
        assert result.success is False
        assert "disabled" in result.error

    async def test_skill_fallback_skipped_when_no_agent(self):
        # ctx.agent is None → no skill registry reachable → None.
        mod = _ConcreteInput()
        mod.set_user_commands({}, _context(agent=None))
        assert await mod.try_user_command("/anything") is None

    async def test_command_takes_precedence_over_skill(self):
        # A registered command named the same as a skill must win.
        skill = Skill(name="status", description="s", body="skill body")
        agent = _FakeAgent(skills=_FakeSkillRegistry([skill]))
        cmd = _FakeCommand("status")
        mod = _ConcreteInput()
        mod.set_user_commands({"status": cmd}, _context(agent))
        result = await mod.try_user_command("/status")
        assert result.output == "status ran"


class _RenderingInput(_ConcreteInput):
    """Input module that renders interactive data payloads."""

    def __init__(self, followup=None):
        super().__init__()
        self._followup = followup
        self.rendered: list[tuple] = []

    async def render_command_data(self, result, command_name):
        self.rendered.append((result, command_name))
        return self._followup


class TestRenderCommandData:
    async def test_data_payload_triggers_render_hook(self):
        followup = UserCommandResult(output="user picked option B")
        mod = _RenderingInput(followup=followup)
        cmd = _FakeCommand(
            "model",
            result=UserCommandResult(output="choose", data={"type": "select"}),
        )
        mod.set_user_commands({"model": cmd}, _context())
        result = await mod.try_user_command("/model")
        # The render hook fired and its follow-up result replaced the original.
        assert mod.rendered[0][1] == "model"
        assert result.output == "user picked option B"

    async def test_render_returning_none_keeps_original_result(self):
        mod = _RenderingInput(followup=None)
        original = UserCommandResult(output="orig", data={"type": "select"})
        cmd = _FakeCommand("model", result=original)
        mod.set_user_commands({"model": cmd}, _context())
        result = await mod.try_user_command("/model")
        assert result is original

    async def test_error_result_does_not_trigger_render(self):
        # Contract: render_command_data only fires for data + no error.
        mod = _RenderingInput()
        cmd = _FakeCommand(
            "model",
            result=UserCommandResult(error="bad", data={"type": "select"}),
        )
        mod.set_user_commands({"model": cmd}, _context())
        result = await mod.try_user_command("/model")
        assert mod.rendered == []
        assert result.error == "bad"

    async def test_base_render_command_data_is_noop(self):
        # The base implementation returns None (CLI/web override it).
        mod = _ConcreteInput()
        out = await mod.render_command_data(UserCommandResult(output="x"), "cmd")
        assert out is None


class TestExecuteFollowup:
    async def test_followup_executes_by_canonical_name(self):
        mod = _ConcreteInput()
        cmd = _FakeCommand("model", aliases=["m"])
        mod.set_user_commands({"model": cmd}, _context())
        result = await mod._execute_followup("m", "gpt-5")
        # Alias resolved, command executed.
        assert result.output == "model ran"
        assert cmd.calls[0][0] == "gpt-5"

    async def test_followup_unknown_command_returns_none(self):
        mod = _ConcreteInput()
        mod.set_user_commands({}, _context())
        assert await mod._execute_followup("ghost", "") is None


class TestProtocol:
    def test_concrete_input_satisfies_protocol(self):
        assert isinstance(_ConcreteInput(), InputModule)
