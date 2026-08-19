"""
Public API for building and running agent systems with KohakuTerrarium.
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

# Import after Studio and Terrarium to avoid re-entering a partial core.config.
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

__version__ = "2.1.0"

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
