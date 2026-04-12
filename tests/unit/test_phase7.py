"""
Phase 7 Tests - Custom Module System and Interactive Sub-agents.

Tests for:
- ModuleLoader (core/loader.py)
- Trigger modules (modules/trigger/)
- Interactive sub-agents (modules/subagent/interactive.py)
- ContextUpdateMode enum
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from kohakuterrarium.core.events import EventType
from kohakuterrarium.core.loader import ModuleLoader, ModuleLoadError
from kohakuterrarium.modules.subagent.config import (
    ContextUpdateMode,
    OutputTarget,
    SubAgentConfig,
)
from kohakuterrarium.modules.subagent.interactive import (
    ContextUpdate,
    InteractiveOutput,
)
from kohakuterrarium.modules.trigger.context import ContextUpdateTrigger
from kohakuterrarium.modules.trigger.timer import TimerTrigger

# =============================================================================
# EventType Tests
# =============================================================================


class TestEventTypeConstants:
    """Tests for EventType constants."""

    def test_context_update_exists(self):
        """Test CONTEXT_UPDATE constant exists."""
        assert hasattr(EventType, "CONTEXT_UPDATE")
        assert EventType.CONTEXT_UPDATE == "context_update"

    def test_timer_exists(self):
        """Test TIMER constant exists."""
        assert EventType.TIMER == "timer"


# =============================================================================
# ContextUpdateMode Tests
# =============================================================================


class TestContextUpdateMode:
    """Tests for ContextUpdateMode enum."""

    def test_enum_values(self):
        """Test all enum values exist."""
        assert ContextUpdateMode.INTERRUPT_RESTART.value == "interrupt_restart"
        assert ContextUpdateMode.QUEUE_APPEND.value == "queue_append"
        assert ContextUpdateMode.FLUSH_REPLACE.value == "flush_replace"

    def test_from_string(self):
        """Test creating enum from string."""
        assert (
            ContextUpdateMode("interrupt_restart")
            == ContextUpdateMode.INTERRUPT_RESTART
        )
        assert ContextUpdateMode("queue_append") == ContextUpdateMode.QUEUE_APPEND
        assert ContextUpdateMode("flush_replace") == ContextUpdateMode.FLUSH_REPLACE

    def test_invalid_value(self):
        """Test invalid enum value raises error."""
        with pytest.raises(ValueError):
            ContextUpdateMode("invalid")


# =============================================================================
# SubAgentConfig with Interactive Fields Tests
# =============================================================================


class TestSubAgentConfigInteractive:
    """Tests for SubAgentConfig interactive fields."""

    def test_interactive_config(self):
        """Test creating interactive sub-agent config."""
        config = SubAgentConfig(
            name="output",
            description="Output agent",
            interactive=True,
            context_mode=ContextUpdateMode.INTERRUPT_RESTART,
            output_to=OutputTarget.EXTERNAL,
        )
        assert config.interactive is True
        assert config.context_mode == ContextUpdateMode.INTERRUPT_RESTART
        assert config.output_to == OutputTarget.EXTERNAL

    def test_non_interactive_defaults(self):
        """Test non-interactive sub-agent defaults."""
        config = SubAgentConfig(
            name="explore",
            description="Explorer",
        )
        assert config.interactive is False
        # Default context_mode is INTERRUPT_RESTART per the actual implementation
        assert config.context_mode == ContextUpdateMode.INTERRUPT_RESTART
        assert config.output_to == OutputTarget.CONTROLLER

    def test_from_dict_with_interactive(self):
        """Test from_dict with interactive fields."""
        data = {
            "name": "output",
            "description": "Output agent",
            "interactive": True,
            "context_mode": "interrupt_restart",
            "output_to": "external",
        }
        config = SubAgentConfig.from_dict(data)
        assert config.interactive is True
        assert config.context_mode == ContextUpdateMode.INTERRUPT_RESTART
        assert config.output_to == OutputTarget.EXTERNAL

    def test_return_as_context(self):
        """Test return_as_context field."""
        config = SubAgentConfig(
            name="memory",
            description="Memory agent",
            return_as_context=True,
        )
        assert config.return_as_context is True


# =============================================================================
# Trigger Module Tests
# =============================================================================


class TestTriggerModule:
    """Tests for TriggerModule protocol."""

    def test_timer_trigger_is_trigger_module(self):
        """Test TimerTrigger implements TriggerModule protocol."""
        trigger = TimerTrigger(interval=1.0)
        # Check it has required methods
        assert hasattr(trigger, "start")
        assert hasattr(trigger, "stop")
        assert hasattr(trigger, "wait_for_trigger")
        assert hasattr(trigger, "set_context")


class TestTimerTrigger:
    """Tests for TimerTrigger."""

    def test_create_timer_trigger(self):
        """Test creating timer trigger."""
        trigger = TimerTrigger(interval=1.0)
        assert trigger.interval == 1.0
        assert trigger.immediate is False
        assert trigger._running is False

    def test_timer_trigger_with_options(self):
        """Test timer trigger with all options."""
        trigger = TimerTrigger(
            interval=5.0,
            immediate=True,
            prompt="Check status",
        )
        assert trigger.interval == 5.0
        assert trigger.immediate is True
        assert trigger.prompt == "Check status"

    async def test_timer_trigger_immediate(self):
        """Test timer trigger with immediate=True fires immediately."""
        trigger = TimerTrigger(interval=10.0, immediate=True)
        await trigger.start()
        assert trigger._running is True

        try:
            # Should return event immediately without waiting
            event = await asyncio.wait_for(trigger.wait_for_trigger(), timeout=0.5)
            assert event is not None
            assert event.type == EventType.TIMER
        finally:
            await trigger.stop()

    async def test_timer_trigger_interval(self):
        """Test timer trigger fires after interval."""
        trigger = TimerTrigger(interval=0.1)  # 100ms
        await trigger.start()

        try:
            event = await asyncio.wait_for(trigger.wait_for_trigger(), timeout=0.5)
            assert event is not None
            assert event.type == EventType.TIMER
        finally:
            await trigger.stop()

    async def test_timer_trigger_stopped_returns_none(self):
        """Test timer trigger returns None when stopped."""
        trigger = TimerTrigger(interval=10.0)
        await trigger.start()
        await trigger.stop()

        event = await trigger.wait_for_trigger()
        assert event is None

    async def test_timer_trigger_lifecycle(self):
        """Test timer trigger start/stop lifecycle."""
        trigger = TimerTrigger(interval=1.0)
        assert trigger._running is False

        await trigger.start()
        assert trigger._running is True

        await trigger.stop()
        assert trigger._running is False

    def test_timer_trigger_set_context(self):
        """Test timer trigger set_context."""
        trigger = TimerTrigger(interval=1.0)
        trigger.set_context({"key": "value"})
        assert trigger._context == {"key": "value"}


class TestContextUpdateTrigger:
    """Tests for ContextUpdateTrigger."""

    def test_create_context_trigger(self):
        """Test creating context update trigger."""
        trigger = ContextUpdateTrigger()
        assert trigger.debounce_ms == 100  # Default
        assert trigger._running is False

    def test_context_trigger_with_debounce(self):
        """Test context trigger with custom debounce."""
        trigger = ContextUpdateTrigger(debounce_ms=500)
        assert trigger.debounce_ms == 500

    async def test_context_trigger_fires_on_update(self):
        """Test context trigger fires when context is set."""
        trigger = ContextUpdateTrigger(debounce_ms=0)  # No debounce for test
        await trigger.start()

        try:
            # Set context in background
            async def set_context_later():
                await asyncio.sleep(0.05)
                trigger.set_context({"message": "hello"})

            task = asyncio.create_task(set_context_later())

            event = await asyncio.wait_for(trigger.wait_for_trigger(), timeout=1.0)
            assert event is not None
            assert event.type == EventType.CONTEXT_UPDATE
            assert event.context.get("message") == "hello"

            await task
        finally:
            await trigger.stop()

    async def test_context_trigger_lifecycle(self):
        """Test context trigger start/stop lifecycle."""
        trigger = ContextUpdateTrigger()
        assert trigger._running is False

        await trigger.start()
        assert trigger._running is True

        await trigger.stop()
        assert trigger._running is False

    def test_context_trigger_manual_trigger(self):
        """Test trigger_now method exists."""
        trigger = ContextUpdateTrigger()
        assert hasattr(trigger, "trigger_now")
        trigger.trigger_now({"test": "value"})


# =============================================================================
# ModuleLoader Tests
# =============================================================================


class TestModuleLoader:
    """Tests for ModuleLoader."""

    def test_create_loader(self):
        """Test creating module loader."""
        loader = ModuleLoader()
        assert loader is not None
        assert loader.agent_path is None

    def test_create_loader_with_path(self):
        """Test creating module loader with agent path."""
        path = Path("/some/agent/path")
        loader = ModuleLoader(agent_path=path)
        assert loader.agent_path == path

    def test_load_builtin_tool(self):
        """Test loading a builtin tool class."""
        loader = ModuleLoader()

        # Load bash tool
        tool_class = loader.load_class(
            "kohakuterrarium.builtins.tools.bash",
            "BashTool",
            module_type="package",
        )
        assert tool_class is not None
        assert tool_class.__name__ in ("ShellTool", "BashTool")

    def test_load_builtin_tool_instance(self):
        """Test loading a builtin tool instance."""
        loader = ModuleLoader()

        tool = loader.load_instance(
            "kohakuterrarium.builtins.tools.bash",
            "BashTool",
            module_type="package",
        )
        assert tool is not None
        # BashTool uses tool_name property, not name
        assert tool.tool_name == "bash"

    def test_load_nonexistent_module(self):
        """Test loading nonexistent module raises ModuleLoadError."""
        loader = ModuleLoader()

        with pytest.raises(ModuleLoadError):
            loader.load_class(
                "kohakuterrarium.nonexistent",
                "SomeClass",
                module_type="package",
            )

    def test_load_nonexistent_class(self):
        """Test loading nonexistent class raises ModuleLoadError."""
        loader = ModuleLoader()

        with pytest.raises(ModuleLoadError):
            loader.load_class(
                "kohakuterrarium.builtins.tools.bash",
                "NonexistentClass",
                module_type="package",
            )

    def test_load_custom_module_from_file(self):
        """Test loading custom module from file."""
        # Create a temporary Python file
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
        ) as f:
            f.write(
                """
class CustomTool:
    name = "custom"
    description = "A custom tool"

    def execute(self):
        return "executed"
"""
            )
            temp_path = Path(f.name)

        try:
            loader = ModuleLoader(agent_path=temp_path.parent)

            tool_class = loader.load_class(
                str(temp_path.name),  # Just filename, relative to agent_path
                "CustomTool",
                module_type="custom",
            )
            assert tool_class is not None
            assert tool_class.name == "custom"

            instance = tool_class()
            assert instance.execute() == "executed"
        finally:
            temp_path.unlink()

    def test_load_custom_without_agent_path(self):
        """Test loading custom module without agent_path raises error."""
        loader = ModuleLoader()  # No agent_path

        with pytest.raises(ModuleLoadError) as exc_info:
            loader.load_class(
                "some_module.py",
                "SomeClass",
                module_type="custom",
            )
        assert "agent_path required" in str(exc_info.value)

    def test_clear_cache(self):
        """Test clearing module cache."""
        loader = ModuleLoader()
        # Load something to populate cache
        loader.load_class(
            "kohakuterrarium.builtins.tools.bash",
            "BashTool",
            module_type="package",
        )
        # Clear and verify doesn't raise
        loader.clear_cache()
        assert loader._loaded_modules == {}


# =============================================================================
# InteractiveOutput and ContextUpdate Tests
# =============================================================================


class TestInteractiveOutput:
    """Tests for InteractiveOutput dataclass."""

    def test_create_output(self):
        """Test creating interactive output."""
        output = InteractiveOutput(text="Hello")
        assert output.text == "Hello"
        assert output.is_complete is False
        assert output.context == {}

    def test_complete_output(self):
        """Test creating complete output."""
        output = InteractiveOutput(
            text="Final response",
            is_complete=True,
            context={"source": "test"},
        )
        assert output.is_complete is True
        assert output.context == {"source": "test"}


class TestContextUpdate:
    """Tests for ContextUpdate dataclass."""

    def test_create_update(self):
        """Test creating context update."""
        update = ContextUpdate(context={"message": "hello"})
        assert update.context == {"message": "hello"}
        assert update.timestamp is not None


# =============================================================================
# InteractiveSubAgent Tests (Unit level - no LLM)
# =============================================================================


class TestInteractiveSubAgentConfig:
    """Tests for InteractiveSubAgent configuration."""

    def test_create_interactive_config(self):
        """Test creating config for interactive sub-agent."""
        config = SubAgentConfig(
            name="output",
            description="Output generator",
            interactive=True,
            context_mode=ContextUpdateMode.INTERRUPT_RESTART,
            output_to=OutputTarget.EXTERNAL,
        )
        assert config.interactive is True
        assert config.context_mode == ContextUpdateMode.INTERRUPT_RESTART

    def test_all_context_modes(self):
        """Test all context update modes can be configured."""
        for mode in ContextUpdateMode:
            config = SubAgentConfig(
                name=f"agent_{mode.value}",
                description=f"Agent with {mode.value}",
                interactive=True,
                context_mode=mode,
            )
            assert config.context_mode == mode


# =============================================================================
# Integration Tests for Manager with Interactive Sub-agents
# =============================================================================


class TestSubAgentManagerInteractive:
    """Tests for SubAgentManager interactive methods."""

    def test_manager_has_interactive_methods(self):
        """Test SubAgentManager has interactive sub-agent methods."""
        from kohakuterrarium.modules.subagent.manager import SubAgentManager

        # Check methods exist
        assert hasattr(SubAgentManager, "start_interactive")
        assert hasattr(SubAgentManager, "stop_interactive")
        assert hasattr(SubAgentManager, "push_context")
        assert hasattr(SubAgentManager, "get_interactive")
        assert hasattr(SubAgentManager, "list_interactive")

    def test_manager_interactive_storage(self):
        """Test manager has storage for interactive sub-agents."""
        from kohakuterrarium.core.registry import Registry
        from kohakuterrarium.llm.base import LLMProvider
        from kohakuterrarium.modules.subagent.manager import SubAgentManager

        # Create minimal mock
        class MockLLM(LLMProvider):
            async def chat(self, messages, stream=False):
                yield "response"

        registry = Registry()
        manager = SubAgentManager(registry, MockLLM())

        assert hasattr(manager, "_interactive")
        assert isinstance(manager._interactive, dict)
        assert manager.list_interactive() == []
