"""
KohakuTerrarium - A universal agent framework for building any type of fully self-driven agent system.

The framework enables building any kind of agent system - from SWE agents like Claude Code
to conversational bots like Neuro-sama to autonomous monitoring systems.
"""

from kohakuterrarium.studio import Studio
from kohakuterrarium.terrarium import (
    ConnectionResult,
    Creature,
    DisconnectionResult,
    EngineEvent,
    EventFilter,
    EventKind,
    Terrarium,
)

__version__ = "1.4.0"

__all__ = [
    "ConnectionResult",
    "Creature",
    "DisconnectionResult",
    "EngineEvent",
    "EventFilter",
    "EventKind",
    "Studio",
    "Terrarium",
    "__version__",
]
