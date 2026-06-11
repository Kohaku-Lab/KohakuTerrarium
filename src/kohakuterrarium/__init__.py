"""
KohakuTerrarium - A universal agent framework for building any type of fully self-driven agent system.

The framework enables building any kind of agent system - from SWE agents like Claude Code
to conversational bots like Neuro-sama to autonomous monitoring systems.
"""

import kohakuterrarium.errors as errors
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

# ``validate`` (and Agent) must import AFTER studio/terrarium: their
# import chain re-enters ``core.config`` through
# ``studio.editors.workspace_manifest`` and would hit a partially
# initialized module if they ran first.
import kohakuterrarium.validate as validate  # noqa: E402
from kohakuterrarium.core.agent import Agent  # noqa: E402
from kohakuterrarium.core.turn import (  # noqa: E402
    Activity,
    TextChunk,
    TurnEnded,
    TurnResult,
)
from kohakuterrarium.modules.tool.function import FunctionTool, tool  # noqa: E402
from kohakuterrarium.session.reader import SessionReader  # noqa: E402
from kohakuterrarium.session.store import SessionStore  # noqa: E402

__version__ = "2.0.0"

__all__ = [
    "Activity",
    "Agent",
    "ConnectionResult",
    "FunctionTool",
    "Creature",
    "DisconnectionResult",
    "EngineEvent",
    "EventFilter",
    "EventKind",
    "SessionReader",
    "SessionStore",
    "Studio",
    "Terrarium",
    "TextChunk",
    "TurnEnded",
    "TurnResult",
    "errors",
    "tool",
    "validate",
    "__version__",
]
