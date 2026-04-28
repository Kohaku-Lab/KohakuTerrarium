"""Terrarium - multi-agent orchestration runtime.

Both the legacy ``TerrariumRuntime`` + ``HotPlugMixin`` surface and the
unified :class:`Terrarium` engine are exported.  New code should reach
for ``Terrarium``; the legacy names remain for in-flight callers.
"""

from kohakuterrarium.terrarium.api import TerrariumAPI
from kohakuterrarium.terrarium.config import (
    ChannelConfig,
    CreatureConfig,
    TerrariumConfig,
    load_terrarium_config,
)
from kohakuterrarium.terrarium.creature_host import (
    Creature,
    build_creature,
)
from kohakuterrarium.terrarium.engine import (
    ConnectionResult,
    DisconnectionResult,
    Terrarium,
)
from kohakuterrarium.terrarium.events import (
    EngineEvent,
    EventFilter,
    EventKind,
    RootAssignment,
)
from kohakuterrarium.terrarium.hotplug import HotPlugMixin
from kohakuterrarium.terrarium.observer import ChannelObserver, ObservedMessage
from kohakuterrarium.terrarium.output_log import LogEntry, OutputLogCapture
from kohakuterrarium.terrarium.runtime import TerrariumRuntime
from kohakuterrarium.terrarium.topology import (
    ChannelInfo,
    ChannelKind,
    GraphTopology,
    TopologyDelta,
    TopologyState,
)

__all__ = [
    # legacy facade
    "ChannelConfig",
    "ChannelObserver",
    "CreatureConfig",
    "HotPlugMixin",
    "LogEntry",
    "ObservedMessage",
    "OutputLogCapture",
    "TerrariumAPI",
    "TerrariumConfig",
    "TerrariumRuntime",
    "load_terrarium_config",
    # unified engine
    "ChannelInfo",
    "ChannelKind",
    "ConnectionResult",
    "Creature",
    "DisconnectionResult",
    "EngineEvent",
    "EventFilter",
    "EventKind",
    "GraphTopology",
    "RootAssignment",
    "Terrarium",
    "TopologyDelta",
    "TopologyState",
    "build_creature",
]
