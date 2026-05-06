"""Engine event types and filter for ``Terrarium.subscribe()``.

Every observable thing the runtime engine does — a creature emitting
text, a tool firing, a channel message landing, a topology change, a
session getting forked — surfaces as an :class:`EngineEvent`.  WS / log
/ trace consumers translate these to their wire formats; programmatic
consumers can iterate them directly via ``async for ev in t.subscribe()``.
"""

import time
from dataclasses import dataclass, field
from enum import Enum


class EventKind(str, Enum):
    """Discriminator for :class:`EngineEvent`.

    Values are chosen so JSON serialization round-trips trivially.
    """

    TEXT = "text"
    ACTIVITY = "activity"
    CHANNEL_MESSAGE = "channel_message"
    TOPOLOGY_CHANGED = "topology_changed"
    SESSION_FORKED = "session_forked"
    CREATURE_STARTED = "creature_started"
    CREATURE_STOPPED = "creature_stopped"
    OUTPUT_WIRE_ADDED = "output_wire_added"
    OUTPUT_WIRE_REMOVED = "output_wire_removed"
    PARENT_LINK_CHANGED = "parent_link_changed"
    PROCESSING_START = "processing_start"
    PROCESSING_END = "processing_end"
    ERROR = "error"


@dataclass
class EngineEvent:
    """A single observable event emitted by the engine.

    ``payload`` is the kind-specific bag — its exact shape depends on
    ``kind`` and matches today's WS message schemas (so ``api/events.py``
    can translate by adding a couple of envelope fields and dropping the
    enum to its string).
    """

    kind: EventKind
    creature_id: str | None = None
    graph_id: str | None = None
    channel: str | None = None
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


@dataclass
class EventFilter:
    """Filter spec for ``Terrarium.subscribe(filter)``.

    All fields are AND-combined; ``None`` means "any".  Pass an empty
    filter (or omit) to receive everything.
    """

    kinds: set[EventKind] | None = None
    creature_ids: set[str] | None = None
    graph_ids: set[str] | None = None
    channels: set[str] | None = None

    def matches(self, ev: EngineEvent) -> bool:
        if self.kinds is not None and ev.kind not in self.kinds:
            return False
        if self.creature_ids is not None and ev.creature_id not in self.creature_ids:
            return False
        if self.graph_ids is not None and ev.graph_id not in self.graph_ids:
            return False
        if self.channels is not None and ev.channel not in self.channels:
            return False
        return True


@dataclass
class ConnectionResult:
    """Returned by ``Terrarium.connect``.

    Carries the channel name (created if needed), the trigger id
    injected on the receiver, the graph id after connection, and the
    topology delta kind (``"nothing"`` or ``"merge"``).
    """

    channel: str
    trigger_id: str = ""
    delta_kind: str = "nothing"
    graph_id: str = ""


@dataclass
class DisconnectionResult:
    """Returned by ``Terrarium.disconnect``.

    Lists the channels that were unwired and the topology delta kind
    (``"nothing"`` or ``"split"``).
    """

    channels: list[str] = field(default_factory=list)
    delta_kind: str = "nothing"


@dataclass(init=False)
class RootAssignment:
    """Returned by ``Terrarium.assign_root``.

    Describes the channel/wiring changes the helper made so callers
    can audit or undo them.
    """

    graph_id: str
    root_id: str
    report_channel: str = "report_to_root"
    # Channels the helper had to declare (it didn't replace existing
    # channels with the same name).
    channels_created: list[str] = field(default_factory=list)
    # Channels the root now listens on (includes ``report_channel`` and
    # any pre-existing channels the helper bound the root to).
    channels_listened: list[str] = field(default_factory=list)
    # Creature ids that gained ``report_channel`` as a send edge.
    senders_added: list[str] = field(default_factory=list)

    def __init__(
        self,
        graph_id: str = "",
        root_id: str | None = None,
        report_channel: str = "report_to_root",
        channels_created: list[str] | None = None,
        channels_listened: list[str] | None = None,
        senders_added: list[str] | None = None,
        *,
        creature_id: str | None = None,
        channels: list[str] | None = None,
    ) -> None:
        self.graph_id = graph_id
        self.root_id = root_id if root_id is not None else creature_id or ""
        self.report_channel = report_channel
        self.channels_created = list(channels_created or [])
        self.channels_listened = list(
            channels_listened if channels_listened is not None else channels or []
        )
        self.senders_added = list(senders_added or [])
