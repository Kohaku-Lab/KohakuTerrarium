"""The Phase E delivery seam: sink contract, outcomes, and the observer type.

Physical admission is separated from turn settlement (design §5.2): a
:class:`DriveDeliverySink` admits (or rejects-because-stopped) an event and, when
admitted, yields a settlement source resolved later. This module is the small,
stable surface Phase E implements over ``Creature.inject_event`` and maps back to
``EngineEvent`` — the dispatcher in :mod:`drive.delivery` builds on it.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SettlementStatus(str, Enum):
    """How an admitted Drive turn ended (design §5.2)."""

    OK = "ok"
    ERROR = "error"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class Settlement:
    """The terminal outcome of one admitted Drive turn plus outcome metadata."""

    status: SettlementStatus
    detail: dict[str, Any] = field(default_factory=dict)


# A settlement source is either the awaitable itself or a zero-arg callable that
# returns one; the dispatcher resolves both. ``None`` means fire-and-forget.
SettlementSource = Awaitable[Settlement] | Callable[[], Awaitable[Settlement]] | None


@dataclass
class DeliveryOutcome:
    """The sink's response to one ``deliver`` call (design §5.2).

    ``admitted=False`` is the *rejected-because-stopped* case: the creature is
    not running and the delivery is deferred with no failure count. ``admitted``
    means the event entered the creature; ``settlement`` (when present) resolves
    to the turn's terminal :class:`Settlement`.
    """

    admitted: bool
    settlement: SettlementSource = None

    @classmethod
    def rejected_stopped(cls) -> "DeliveryOutcome":
        return cls(admitted=False)

    @classmethod
    def accepted(cls, settlement: SettlementSource = None) -> "DeliveryOutcome":
        return cls(admitted=True, settlement=settlement)


class DriveDeliverySink(Protocol):
    """Physical delivery target implemented by Phase E over ``Creature``.

    ``deliver`` admits (or rejects-because-stopped) an event and, when admitted,
    yields a settlement source. ``has_queued_foreign_work`` is the fairness probe
    (§5.5): whether the creature has queued non-Drive work waiting.
    """

    async def deliver(
        self, creature_id: str, event: TriggerEvent, *, delivery_id: str
    ) -> DeliveryOutcome: ...

    def has_queued_foreign_work(self, creature_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# Observer (optional, fail-open) — Phase E maps these to EngineEvents
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriveObservation:
    """A structural Drive notification for the optional observer (design §9.4)."""

    kind: str
    drive_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


DriveObserver = Callable[[DriveObservation], None]


def emit_observation(
    observer: DriveObserver | None,
    kind: str,
    drive_id: str | None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Notify the observer, swallowing any error (design §8.8 observers fail open)."""
    if observer is None:
        return
    try:
        observer(
            DriveObservation(kind=kind, drive_id=drive_id, payload=dict(payload or {}))
        )
    except Exception as exc:  # observers never roll back a committed mutation
        logger.warning("drive observer failed", obs_kind=kind, error=str(exc))
