"""Catalog of universal trigger classes exposed as setup tools."""

from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.modules.trigger.channel import ChannelTrigger
from kohakuterrarium.modules.trigger.scheduler import SchedulerTrigger
from kohakuterrarium.modules.trigger.timer import TimerTrigger

UNIVERSAL_TRIGGER_CLASSES: tuple[type[BaseTrigger], ...] = (
    TimerTrigger,
    ChannelTrigger,
    SchedulerTrigger,
)


def list_universal_trigger_classes() -> list[type[BaseTrigger]]:
    """Return built-in trigger classes that expose setup tools."""
    return [
        cls
        for cls in UNIVERSAL_TRIGGER_CLASSES
        if getattr(cls, "universal", False) and getattr(cls, "setup_tool_name", "")
    ]
