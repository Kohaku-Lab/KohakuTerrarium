"""Unit tests for :mod:`kohakuterrarium.modules.trigger.universal`."""

from kohakuterrarium.modules.trigger.channel import ChannelTrigger
from kohakuterrarium.modules.trigger.scheduler import SchedulerTrigger
from kohakuterrarium.modules.trigger.timer import TimerTrigger
from kohakuterrarium.modules.trigger.universal import (
    list_universal_trigger_classes,
)


class TestUniversalCatalog:
    def test_lists_only_universal_classes_with_setup_tool_name(self):
        classes = list_universal_trigger_classes()
        # Every entry is genuinely universal + has a setup tool name.
        for cls in classes:
            assert cls.universal is True
            assert cls.setup_tool_name

    def test_includes_the_three_builtin_universal_triggers(self):
        classes = set(list_universal_trigger_classes())
        assert {TimerTrigger, ChannelTrigger, SchedulerTrigger} <= classes
