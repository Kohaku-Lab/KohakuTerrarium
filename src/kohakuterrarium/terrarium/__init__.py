"""Terrarium - the unified runtime engine.

The :class:`Terrarium` engine is the single per-process runtime. Every
running creature lives inside it; a solo ``kt run creature.yaml`` is a
1-creature graph, a recipe is an N-creature graph applied via
:meth:`Terrarium.apply_recipe`. Topology can change at runtime via
``add_creature`` / ``connect`` / ``add_channel`` etc., or via the
``group_*`` tool surface from a privileged creature.

Privilege is set at creature creation time and immutable after — see
:class:`Creature.is_privileged` and :meth:`Terrarium.assign_root`. The
legacy ``TerrariumRuntime`` / ``KohakuManager`` / ``terrarium_*`` tool
stack has been removed; the engine and the ``group_*`` tool surface
are the only paths.
"""

# Trigger ``@register_builtin`` for every group_* tool so the catalog
# is populated regardless of which engine path constructs creatures.
# ``engine.py`` already imports ``tools_group``; this is a defensive
# anchor so a future engine refactor can't silently lose the
# registration.
import kohakuterrarium.terrarium.tools_group as _tools_group  # noqa: F401
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
from kohakuterrarium.terrarium.observer import ChannelObserver, ObservedMessage
from kohakuterrarium.terrarium.output_log import LogEntry, OutputLogCapture
from kohakuterrarium.terrarium.topology import (
    ChannelInfo,
    GraphTopology,
    TopologyDelta,
    TopologyState,
)

__all__ = [
    "ChannelConfig",
    "ChannelInfo",
    "ChannelObserver",
    "ConnectionResult",
    "Creature",
    "CreatureConfig",
    "DisconnectionResult",
    "EngineEvent",
    "EventFilter",
    "EventKind",
    "GraphTopology",
    "LogEntry",
    "ObservedMessage",
    "OutputLogCapture",
    "RootAssignment",
    "Terrarium",
    "TerrariumConfig",
    "TopologyDelta",
    "TopologyState",
    "build_creature",
    "load_terrarium_config",
]
