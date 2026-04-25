"""Terrarium management tool registration catalog."""

from kohakuterrarium.terrarium.tools_creature import CreatureInterruptTool
from kohakuterrarium.terrarium.tools_creature import CreatureStartTool
from kohakuterrarium.terrarium.tools_creature import CreatureStopTool
from kohakuterrarium.terrarium.tools_lifecycle import TerrariumCreateTool
from kohakuterrarium.terrarium.tools_lifecycle import TerrariumStatusTool
from kohakuterrarium.terrarium.tools_lifecycle import TerrariumStopTool
from kohakuterrarium.terrarium.tools_messaging import TerrariumHistoryTool
from kohakuterrarium.terrarium.tools_messaging import TerrariumObserveTool
from kohakuterrarium.terrarium.tools_messaging import TerrariumSendTool


def ensure_terrarium_tools_registered() -> None:
    """Import terrarium tool modules so their decorators register classes."""


__all__ = [
    "CreatureInterruptTool",
    "CreatureStartTool",
    "CreatureStopTool",
    "TerrariumCreateTool",
    "TerrariumHistoryTool",
    "TerrariumObserveTool",
    "TerrariumSendTool",
    "TerrariumStatusTool",
    "TerrariumStopTool",
    "ensure_terrarium_tools_registered",
]
