"""Drive repository row types and wire serialization.

Outbox, dead-letter, and idempotency rows with their wire conversions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DriveOutboxEntry:
    """An outbox event or delivery intent awaiting dispatch."""

    outbox_id: str
    drive_id: str
    kind: str
    created_at: datetime
    delivery_id: str | None = None
    dispatched: bool = False
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DriveDeadLetter:
    """Failure snapshot for a delivery that exhausted its retries."""

    delivery_id: str
    drive_id: str
    reason: str
    attempt: int
    created_at: datetime
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IdempotencyRecord:
    """A recorded mutation keyed by actor and idempotency key."""

    actor: str
    key: str
    operation_hash: str
    result: dict[str, Any]
    created_at: datetime


def parse_dt(value: str) -> datetime:
    """Parse an ISO datetime, including a trailing UTC ``Z`` marker."""
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(text)


def outbox_to_wire(entry: DriveOutboxEntry) -> dict[str, Any]:
    """Serialize an outbox entry to its wire representation."""
    return {
        "outbox_id": entry.outbox_id,
        "drive_id": entry.drive_id,
        "kind": entry.kind,
        "created_at": entry.created_at.isoformat(),
        "delivery_id": entry.delivery_id,
        "dispatched": entry.dispatched,
        "payload": entry.payload,
    }


def outbox_from_wire(data: dict[str, Any]) -> DriveOutboxEntry:
    """Deserialize an outbox entry from its wire representation."""
    return DriveOutboxEntry(
        outbox_id=data["outbox_id"],
        drive_id=data["drive_id"],
        kind=data["kind"],
        created_at=parse_dt(data["created_at"]),
        delivery_id=data.get("delivery_id"),
        dispatched=bool(data.get("dispatched", False)),
        payload=data.get("payload") or {},
    )


def dead_letter_to_wire(entry: DriveDeadLetter) -> dict[str, Any]:
    """Serialize a dead-letter row to its wire representation."""
    return {
        "delivery_id": entry.delivery_id,
        "drive_id": entry.drive_id,
        "reason": entry.reason,
        "attempt": entry.attempt,
        "created_at": entry.created_at.isoformat(),
        "detail": entry.detail,
    }


def dead_letter_from_wire(data: dict[str, Any]) -> DriveDeadLetter:
    """Deserialize a dead-letter row from its wire representation."""
    return DriveDeadLetter(
        delivery_id=data["delivery_id"],
        drive_id=data["drive_id"],
        reason=data["reason"],
        attempt=int(data["attempt"]),
        created_at=parse_dt(data["created_at"]),
        detail=data.get("detail") or {},
    )


def idempotency_to_wire(rec: IdempotencyRecord) -> dict[str, Any]:
    """Serialize an idempotency record to its wire representation."""
    return {
        "actor": rec.actor,
        "key": rec.key,
        "operation_hash": rec.operation_hash,
        "result": rec.result,
        "created_at": rec.created_at.isoformat(),
    }


def idempotency_from_wire(data: dict[str, Any]) -> IdempotencyRecord:
    """Deserialize an idempotency record from its wire representation."""
    return IdempotencyRecord(
        actor=data["actor"],
        key=data["key"],
        operation_hash=data["operation_hash"],
        result=data.get("result") or {},
        created_at=parse_dt(data["created_at"]),
    )
