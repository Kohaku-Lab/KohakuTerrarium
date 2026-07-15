"""Define Drive delivery admission, settlement, and observation contracts.

Physical admission is separate from eventual turn settlement. A
:class:`DriveDeliverySink` admits or defers an event and, when admitted, returns
a settlement source that resolves later. The dispatcher builds on this stable
boundary without depending on creature internals.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SettlementStatus(str, Enum):
    """Classify how an admitted Drive turn ended."""

    OK = "ok"
    ERROR = "error"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class Settlement:
    """The terminal outcome of one admitted Drive turn plus outcome metadata."""

    status: SettlementStatus
    detail: dict[str, Any] = field(default_factory=dict)


# Settlement may be supplied directly or lazily; null means no settlement signal.
SettlementSource = Awaitable[Settlement] | Callable[[], Awaitable[Settlement]] | None


@dataclass
class DeliveryOutcome:
    """Describe whether delivery was admitted and how its turn will settle."""

    admitted: bool
    settlement: SettlementSource = None

    @classmethod
    def rejected_stopped(cls) -> "DeliveryOutcome":
        return cls(admitted=False)

    @classmethod
    def accepted(cls, settlement: SettlementSource = None) -> "DeliveryOutcome":
        return cls(admitted=True, settlement=settlement)


class DriveDeliverySink(Protocol):
    """Define physical Drive delivery and fairness checks for a creature target."""

    async def deliver(
        self, creature_id: str, event: TriggerEvent, *, delivery_id: str
    ) -> DeliveryOutcome: ...

    def has_queued_foreign_work(self, creature_id: str) -> bool: ...


@dataclass(frozen=True)
class DriveObservation:
    """Carry a structural Drive notification to an optional observer."""

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
    """Notify the observer without allowing it to affect committed state."""
    if observer is None:
        return
    try:
        observer(
            DriveObservation(kind=kind, drive_id=drive_id, payload=dict(payload or {}))
        )
    except Exception as exc:
        # Observer failures cannot roll back a committed mutation.
        logger.warning("drive observer failed", obs_kind=kind, error=str(exc))
