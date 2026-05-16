"""Unit tests for :mod:`kohakuterrarium.modules.trigger.callable`.

Behavior-first: the CallableTriggerTool adapter validates the trigger
class at construction, rejects bad/missing args, builds + registers the
trigger via the agent's trigger_manager, and surfaces failures as
ToolResult errors instead of raising.
"""

from pathlib import Path

import pytest

from kohakuterrarium.modules.tool.base import ExecutionMode, ToolContext
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.modules.trigger.callable import CallableTriggerTool
from kohakuterrarium.modules.trigger.timer import TimerTrigger


class _FakeTriggerManager:
    """Records add() calls; raises ValueError on duplicate id."""

    def __init__(self):
        self.added: list[tuple[BaseTrigger, str | None]] = []
        self._used_ids: set[str] = set()

    async def add(self, trigger, trigger_id=None):
        if trigger_id and trigger_id in self._used_ids:
            raise ValueError(f"trigger id {trigger_id!r} already in use")
        resolved = trigger_id or f"trigger_{len(self.added)}"
        self._used_ids.add(resolved)
        self.added.append((trigger, trigger_id))
        return resolved


class _FakeAgent:
    def __init__(self):
        self.trigger_manager = _FakeTriggerManager()
        self.environment = None
        self.session = None


def _context(agent=None, agent_name="tester"):
    return ToolContext(
        agent_name=agent_name,
        session=None,
        working_dir=Path.cwd(),
        agent=agent,
    )


class _NotUniversal(BaseTrigger):
    async def wait_for_trigger(self):
        return None


class _UniversalNoToolName(BaseTrigger):
    universal = True

    async def wait_for_trigger(self):
        return None


class TestConstruction:
    def test_rejects_non_universal_trigger(self):
        with pytest.raises(ValueError, match="not universal"):
            CallableTriggerTool(_NotUniversal)

    def test_rejects_universal_without_setup_tool_name(self):
        with pytest.raises(ValueError, match="setup_tool_name"):
            CallableTriggerTool(_UniversalNoToolName)

    def test_metadata_derived_from_trigger_class(self):
        tool = CallableTriggerTool(TimerTrigger)
        assert tool.tool_name == "add_timer"
        assert tool.description.startswith("**Trigger** —")
        assert tool.execution_mode is ExecutionMode.DIRECT
        # require_manual_read is derived from the class flag, not writable.
        assert tool.require_manual_read is False

    def test_parameters_schema_injects_name_arg(self):
        tool = CallableTriggerTool(TimerTrigger)
        schema = tool.get_parameters_schema()
        assert "name" in schema["properties"]
        # Original schema props preserved alongside the injected name.
        assert "interval" in schema["properties"]
        assert schema["required"] == ["interval", "prompt"]


class TestExecute:
    async def test_missing_context_returns_error(self):
        tool = CallableTriggerTool(TimerTrigger)
        result = await tool._execute({"interval": 5, "prompt": "p"}, context=None)
        assert result.success is False
        assert "ToolContext" in result.error

    async def test_missing_required_arg_returns_error(self):
        tool = CallableTriggerTool(TimerTrigger)
        agent = _FakeAgent()
        # 'prompt' is required but absent.
        result = await tool._execute({"interval": 5}, context=_context(agent))
        assert result.success is False
        assert "prompt" in result.error
        # Nothing should have been registered.
        assert agent.trigger_manager.added == []

    async def test_successful_install_registers_trigger(self):
        tool = CallableTriggerTool(TimerTrigger)
        agent = _FakeAgent()
        result = await tool._execute(
            {"interval": 30, "prompt": "wake up"}, context=_context(agent)
        )
        assert result.success is True
        assert len(agent.trigger_manager.added) == 1
        trigger, requested_id = agent.trigger_manager.added[0]
        assert isinstance(trigger, TimerTrigger)
        assert trigger.interval == 30
        assert trigger.prompt == "wake up"
        # Auto-generated id surfaced in metadata + output.
        assert result.metadata["trigger_id"] == "trigger_0"
        assert "trigger_0" in result.output

    async def test_explicit_name_used_as_trigger_id(self):
        tool = CallableTriggerTool(TimerTrigger)
        agent = _FakeAgent()
        result = await tool._execute(
            {"name": "my-timer", "interval": 10, "prompt": "p"},
            context=_context(agent),
        )
        assert result.success is True
        assert result.metadata["trigger_id"] == "my-timer"
        # 'name' is an adapter arg — it must NOT leak into the trigger ctor.
        trigger, _ = agent.trigger_manager.added[0]
        assert "name" not in trigger.options

    async def test_duplicate_name_returns_error_not_raise(self):
        tool = CallableTriggerTool(TimerTrigger)
        agent = _FakeAgent()
        await tool._execute(
            {"name": "dup", "interval": 10, "prompt": "p"},
            context=_context(agent),
        )
        # Second install with the same name → manager raises ValueError,
        # adapter must convert it to a clean ToolResult error.
        result = await tool._execute(
            {"name": "dup", "interval": 10, "prompt": "p"},
            context=_context(agent),
        )
        assert result.success is False
        assert "dup" in result.error

    async def test_trigger_build_failure_surfaces_as_error(self):
        # from_setup_args raising must become a ToolResult error, not a crash.
        class _BadBuild(BaseTrigger):
            universal = True
            setup_tool_name = "bad_build"
            setup_param_schema = {"type": "object", "properties": {}}

            @classmethod
            def from_setup_args(cls, args):
                raise RuntimeError("cannot build")

            async def wait_for_trigger(self):
                return None

        tool = CallableTriggerTool(_BadBuild)
        result = await tool._execute({}, context=_context(_FakeAgent()))
        assert result.success is False
        assert "cannot build" in result.error

    async def test_post_setup_failure_surfaces_as_error(self):
        # A post_setup hook raising must become a ToolResult error.
        class _BadPostSetup(BaseTrigger):
            universal = True
            setup_tool_name = "bad_post"
            setup_param_schema = {"type": "object", "properties": {}}

            @classmethod
            def post_setup(cls, trigger, context):
                raise RuntimeError("post_setup exploded")

            async def wait_for_trigger(self):
                return None

        tool = CallableTriggerTool(_BadPostSetup)
        result = await tool._execute({}, context=_context(_FakeAgent()))
        assert result.success is False
        assert "post_setup exploded" in result.error

    async def test_non_value_error_from_register_surfaces_as_error(self):
        # A non-ValueError raised by trigger_manager.add is still caught
        # and surfaced as a ToolResult error, not propagated.
        class _BrokenManager:
            async def add(self, trigger, trigger_id=None):
                raise RuntimeError("manager is broken")

        agent = _FakeAgent()
        agent.trigger_manager = _BrokenManager()
        tool = CallableTriggerTool(TimerTrigger)
        result = await tool._execute(
            {"interval": 5, "prompt": "p"}, context=_context(agent)
        )
        assert result.success is False
        assert "manager is broken" in result.error

    def test_require_manual_read_setter_is_silently_ignored(self):
        # require_manual_read is derived from the trigger class; BaseTool's
        # __init__ assigns it, and external writes are accepted but ignored.
        tool = CallableTriggerTool(TimerTrigger)
        tool.require_manual_read = True  # must not raise, must not stick
        assert tool.require_manual_read is False


class TestDocumentation:
    def test_full_documentation_lists_parameters(self):
        tool = CallableTriggerTool(TimerTrigger)
        doc = tool.get_full_documentation()
        assert "# add_timer" in doc
        assert "## Parameters" in doc
        assert "`interval`" in doc
        assert "(required)" in doc  # interval/prompt are required


class TestSetupSummary:
    async def test_no_args_summary_says_no_parameters(self):
        # A trigger with an empty schema → the confirmation says
        # "no parameters" rather than an empty arg list.
        class _NoArgTrigger(BaseTrigger):
            universal = True
            setup_tool_name = "no_arg"
            setup_param_schema = {"type": "object", "properties": {}}

            async def wait_for_trigger(self):
                return None

        tool = CallableTriggerTool(_NoArgTrigger)
        result = await tool._execute({}, context=_context(_FakeAgent()))
        assert result.success is True
        assert "no parameters" in result.output
