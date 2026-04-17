"""Tests for trigger system improvements and require_manual_read."""

from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.modules.trigger.channel import ChannelTrigger
from kohakuterrarium.modules.trigger.scheduler import SchedulerTrigger
from kohakuterrarium.modules.trigger.timer import TimerTrigger
from kohakuterrarium.modules.tool.base import BaseTool


class TestTriggerProperties:
    """Test resumable and universal flags on trigger types."""

    def test_base_trigger_defaults(self):
        assert BaseTrigger.resumable is False
        assert BaseTrigger.universal is False

    def test_channel_trigger_flags(self):
        assert ChannelTrigger.resumable is True
        assert ChannelTrigger.universal is True

    def test_timer_trigger_flags(self):
        assert TimerTrigger.resumable is True
        assert TimerTrigger.universal is True

    def test_scheduler_trigger_flags(self):
        assert SchedulerTrigger.resumable is True
        assert SchedulerTrigger.universal is True


class TestTimerTriggerResume:
    def test_to_resume_dict(self):
        t = TimerTrigger(interval=120, prompt="check status")
        d = t.to_resume_dict()
        assert d["interval"] == 120
        assert d["prompt"] == "check status"
        assert d["immediate"] is False  # Never immediate on resume

    def test_from_resume_dict(self):
        t = TimerTrigger.from_resume_dict({"interval": 300, "prompt": "five min check"})
        assert t.interval == 300
        assert t.prompt == "five min check"
        assert t.immediate is False

    def test_roundtrip(self):
        original = TimerTrigger(interval=60, prompt="test", immediate=True)
        d = original.to_resume_dict()
        restored = TimerTrigger.from_resume_dict(d)
        assert restored.interval == original.interval
        assert restored.prompt == original.prompt
        # immediate is forced False on resume
        assert restored.immediate is False


class TestChannelTriggerResume:
    def test_to_resume_dict(self):
        t = ChannelTrigger(
            channel_name="results",
            subscriber_id="root_results",
            ignore_sender="root",
            prompt="Result: {content}",
        )
        d = t.to_resume_dict()
        assert d["channel_name"] == "results"
        assert d["subscriber_id"] == "root_results"
        assert d["ignore_sender"] == "root"
        assert d["prompt"] == "Result: {content}"

    def test_from_resume_dict(self):
        t = ChannelTrigger.from_resume_dict(
            {
                "channel_name": "tasks",
                "subscriber_id": "watcher",
                "filter_sender": "boss",
            }
        )
        assert t.channel_name == "tasks"
        assert t.subscriber_id == "watcher"
        assert t.filter_sender == "boss"
        assert t.ignore_sender is None
        assert t._registry is None  # Caller must set

    def test_roundtrip(self):
        original = ChannelTrigger(
            channel_name="review",
            ignore_sender="reviewer",
            prompt="Review needed",
        )
        restored = ChannelTrigger.from_resume_dict(original.to_resume_dict())
        assert restored.channel_name == original.channel_name
        assert restored.ignore_sender == original.ignore_sender
        assert restored.prompt == original.prompt


class TestSchedulerTrigger:
    def test_to_resume_dict(self):
        t = SchedulerTrigger(every_minutes=30, prompt="half hour")
        d = t.to_resume_dict()
        assert d["every_minutes"] == 30
        assert d["prompt"] == "half hour"
        assert d["daily_at"] is None
        assert d["hourly_at"] is None

    def test_from_resume_dict_every_minutes(self):
        t = SchedulerTrigger.from_resume_dict({"every_minutes": 15, "prompt": "15m"})
        assert t.every_minutes == 15
        assert t.prompt == "15m"

    def test_from_resume_dict_daily(self):
        t = SchedulerTrigger.from_resume_dict({"daily_at": "09:30"})
        assert t.daily_at == "09:30"
        assert t.every_minutes is None

    def test_from_resume_dict_hourly(self):
        t = SchedulerTrigger.from_resume_dict({"hourly_at": 45})
        assert t.hourly_at == 45

    def test_seconds_until_next_every_minutes(self):
        t = SchedulerTrigger(every_minutes=60)
        secs = t._seconds_until_next()
        assert 0 < secs <= 3600

    def test_seconds_until_next_daily(self):
        t = SchedulerTrigger(daily_at="00:00")
        secs = t._seconds_until_next()
        assert 0 < secs <= 86400

    def test_seconds_until_next_hourly(self):
        t = SchedulerTrigger(hourly_at=0)
        secs = t._seconds_until_next()
        assert 0 < secs <= 3600


class TestRequireManualRead:
    """Test the require_manual_read tool property."""

    def test_default_false(self):
        class MyTool(BaseTool):
            @property
            def tool_name(self):
                return "my_tool"

            @property
            def description(self):
                return "test"

            async def _execute(self, args, context=None):
                pass

        t = MyTool()
        assert t.require_manual_read is False
        assert t._manual_read is False

    def test_can_set_true(self):
        class LockedTool(BaseTool):
            require_manual_read = True

            @property
            def tool_name(self):
                return "locked"

            @property
            def description(self):
                return "test"

            async def _execute(self, args, context=None):
                pass

        t = LockedTool()
        assert t.require_manual_read is True
        assert t._manual_read is False

    def test_manual_read_unlocks(self):
        class LockedTool(BaseTool):
            require_manual_read = True

            @property
            def tool_name(self):
                return "locked"

            @property
            def description(self):
                return "test"

            async def _execute(self, args, context=None):
                pass

        t = LockedTool()
        assert not t._manual_read
        t._manual_read = True
        assert t._manual_read


class TestCallableTriggerTool:
    """Each universal trigger class becomes an individually-named tool."""

    def test_timer_tool_shape(self):
        from kohakuterrarium.modules.trigger.callable import CallableTriggerTool
        from kohakuterrarium.modules.trigger.timer import TimerTrigger

        tool = CallableTriggerTool(TimerTrigger)
        assert tool.tool_name == "add_timer"
        assert tool.description.startswith("**Trigger** — ")
        schema = tool.get_parameters_schema()
        assert "interval" in schema["properties"]
        assert "prompt" in schema["properties"]
        assert "interval" in schema["required"]

    def test_channel_tool_shape(self):
        from kohakuterrarium.modules.trigger.callable import CallableTriggerTool
        from kohakuterrarium.modules.trigger.channel import ChannelTrigger

        tool = CallableTriggerTool(ChannelTrigger)
        assert tool.tool_name == "watch_channel"
        assert tool.description.startswith("**Trigger** — ")
        schema = tool.get_parameters_schema()
        assert "channel_name" in schema["properties"]

    def test_scheduler_tool_shape(self):
        from kohakuterrarium.modules.trigger.callable import CallableTriggerTool
        from kohakuterrarium.modules.trigger.scheduler import SchedulerTrigger

        tool = CallableTriggerTool(SchedulerTrigger)
        assert tool.tool_name == "add_schedule"

    def test_rejects_non_universal(self):
        import pytest

        from kohakuterrarium.modules.trigger.base import BaseTrigger
        from kohakuterrarium.modules.trigger.callable import CallableTriggerTool

        class Plain(BaseTrigger):
            async def wait_for_trigger(self):
                return None

        with pytest.raises(ValueError):
            CallableTriggerTool(Plain)

    def test_full_documentation_includes_params(self):
        from kohakuterrarium.modules.trigger.callable import CallableTriggerTool
        from kohakuterrarium.modules.trigger.timer import TimerTrigger

        doc = CallableTriggerTool(TimerTrigger).get_full_documentation()
        assert "add_timer" in doc
        assert "interval" in doc
        assert "prompt" in doc
        assert "(required)" in doc

    def test_name_arg_is_always_exposed(self):
        """Adapter injects an optional `name` arg into every tool's schema."""
        from kohakuterrarium.modules.trigger.callable import CallableTriggerTool
        from kohakuterrarium.modules.trigger.timer import TimerTrigger

        schema = CallableTriggerTool(TimerTrigger).get_parameters_schema()
        assert "name" in schema["properties"]
        # `name` is optional — it does NOT appear in the class schema's
        # required list (only `interval` and `prompt` should be required).
        assert "name" not in schema.get("required", [])
        assert set(schema["required"]) == {"interval", "prompt"}

    async def test_execute_with_name_uses_it_as_trigger_id(self):
        """Passing `name` to the tool makes trigger_manager use it as the id."""
        from pathlib import Path

        from kohakuterrarium.core.trigger_manager import TriggerManager
        from kohakuterrarium.modules.tool.base import ToolContext
        from kohakuterrarium.modules.trigger.callable import CallableTriggerTool
        from kohakuterrarium.modules.trigger.timer import TimerTrigger

        class MockAgent:
            trigger_manager = TriggerManager(lambda e: None)

        agent = MockAgent()
        ctx = ToolContext(
            agent_name="test",
            session=None,
            working_dir=Path("."),
            agent=agent,
        )
        tool = CallableTriggerTool(TimerTrigger)
        result = await tool.execute(
            {"name": "hourly_check", "interval": 3600, "prompt": "Check queue"},
            context=ctx,
        )
        assert result.error is None, result.error
        assert result.metadata["trigger_id"] == "hourly_check"
        assert "hourly_check" in agent.trigger_manager._triggers
        # Cleanup to avoid leaking background tasks.
        await agent.trigger_manager.remove("hourly_check")
