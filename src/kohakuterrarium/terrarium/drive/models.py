"""Immutable domain records for the Drive runtime resource (design §3).

Persisted, addressable vocabulary: :class:`DriveStatus`, :class:`ActorRef`,
:class:`DriveRecord`, :class:`DriveAssignment`, :class:`DriveDelivery`,
:class:`DriveProgress`, and :class:`DriveAuditRecord`. Caller-input DTOs
(create/patch/query/proposal) live in :mod:`drive_requests`.

Records are frozen dataclasses validated at construction. Fields are
JSON/msgpack-safe once passed through :mod:`drive_wire`; ``datetime`` values
are kept timezone-aware in memory and serialized as ISO strings there.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from kohakuterrarium.terrarium.drive.errors import DriveValidationError

# Literal domains are validated at construction because policy assumes
# well-formed records.
_ACTOR_KINDS = frozenset({"user", "service", "creature", "plugin", "system"})
_SCOPE_TYPES = frozenset({"creature", "graph"})
_OWNER_SCOPES = frozenset({"actor", "creature", "graph", "service"})
_ASSIGNMENT_STATES = frozenset({"unassigned", "assigned", "orphaned"})
_DELIVERY_REASONS = frozenset(
    {
        "activated",
        "ready",
        "retry",
        "resume",
        "recovery",
        "reassigned",
        "dependency_ready",
        "manual_wake",
    }
)
_DELIVERY_STATES = frozenset(
    {
        "pending",
        "claimed",
        "admitted",
        "acknowledged",
        "retry_wait",
        "dead_letter",
        "superseded",
    }
)


class DriveStatus(str, Enum):
    """Runtime control state of a Drive (design §3.3)."""

    DRAFT = "draft"
    ACTIVE = "active"
    WAITING = "waiting"
    BLOCKED = "blocked"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETIRED = "retired"


class DriveAvailability(str, Enum):
    """Derived runtime condition when a record's registration can't serve it.

    Not a :class:`DriveStatus` and never a reason to consume a revision
    (design §8.6); ``AVAILABLE`` means the enabled registry can serve the kind.
    """

    AVAILABLE = "available"
    REGISTRATION_DISABLED = "registration_disabled"
    REGISTRATION_UNAVAILABLE = "registration_unavailable"
    REGISTRATION_INCOMPATIBLE = "registration_incompatible"


def _require_nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DriveValidationError(f"{name} must be a non-empty string, got {value!r}")


def _require_int(value: object, name: str, *, minimum: int | None = None) -> None:
    # Booleans are integers in Python but are invalid for numeric Drive fields.
    if not isinstance(value, int) or isinstance(value, bool):
        raise DriveValidationError(f"{name} must be an int, got {value!r}")
    if minimum is not None and value < minimum:
        raise DriveValidationError(f"{name} must be >= {minimum}, got {value}")


def _require_aware(value: object, name: str, *, allow_none: bool = True) -> None:
    if value is None:
        if allow_none:
            return
        raise DriveValidationError(f"{name} is required")
    if not isinstance(value, datetime):
        raise DriveValidationError(f"{name} must be a datetime, got {value!r}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DriveValidationError(f"{name} must be timezone-aware, got {value!r}")


def _require_actor(value: object, name: str) -> None:
    if not isinstance(value, ActorRef):
        raise DriveValidationError(f"{name} must be an ActorRef, got {value!r}")


def _require_status(value: object, name: str) -> None:
    if not isinstance(value, DriveStatus):
        raise DriveValidationError(f"{name} must be a DriveStatus, got {value!r}")


def _require_in(value: object, name: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise DriveValidationError(
            f"{name} must be one of {sorted(allowed)}, got {value!r}"
        )


@dataclass(frozen=True)
class ActorRef:
    """A mutation's authenticated principal (design §3.6).

    Wire form is ``"<kind>:<identity>"`` where kind is one of user / service /
    creature / plugin / system. ``identity`` may contain further colons or a
    ``package/name`` slash; :meth:`parse` splits on the first colon only.
    """

    kind: str
    identity: str

    def __post_init__(self) -> None:
        _require_in(self.kind, "ActorRef.kind", _ACTOR_KINDS)
        _require_nonempty(self.identity, "ActorRef.identity")

    def format(self) -> str:
        return f"{self.kind}:{self.identity}"

    @classmethod
    def parse(cls, value: str) -> "ActorRef":
        if not isinstance(value, str) or ":" not in value:
            raise DriveValidationError(
                f"ActorRef must be '<kind>:<identity>', got {value!r}"
            )
        kind, identity = value.split(":", 1)
        return cls(kind=kind, identity=identity)


# Engine-originated reconciliation mutations use the framework system actor.
SYSTEM_ACTOR = ActorRef(kind="system", identity="terrarium")


@dataclass(frozen=True)
class DriveRecord:
    """The canonical, revisioned commitment (design §3.2)."""

    drive_id: str
    kind: str
    schema_version: int
    revision: int

    title: str
    spec: dict[str, Any]
    presentation: dict[str, Any]
    metadata: dict[str, Any]

    scope_type: Literal["creature", "graph"]
    scope_id: str
    origin_scope_id: str

    status: DriveStatus
    status_reason: str | None

    priority: int
    policy_name: str

    created_by: ActorRef
    owner: ActorRef
    owner_scope: Literal["actor", "creature", "graph", "service"]
    created_at: datetime
    updated_by: ActorRef
    updated_at: datetime
    lifecycle_epoch: int

    terminal_evidence: dict[str, Any] | None = None
    not_before: datetime | None = None
    expires_at: datetime | None = None
    dependency_ids: tuple[str, ...] = ()
    policy_options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonempty(self.drive_id, "drive_id")
        _require_nonempty(self.kind, "kind")
        _require_int(self.schema_version, "schema_version", minimum=1)
        _require_int(self.revision, "revision", minimum=0)
        _require_int(self.lifecycle_epoch, "lifecycle_epoch", minimum=0)
        _require_int(self.priority, "priority")
        _require_nonempty(self.title, "title")
        _require_in(self.scope_type, "scope_type", _SCOPE_TYPES)
        _require_nonempty(self.scope_id, "scope_id")
        _require_nonempty(self.origin_scope_id, "origin_scope_id")
        _require_status(self.status, "status")
        _require_in(self.owner_scope, "owner_scope", _OWNER_SCOPES)
        _require_actor(self.created_by, "created_by")
        _require_actor(self.owner, "owner")
        _require_actor(self.updated_by, "updated_by")
        _require_aware(self.created_at, "created_at", allow_none=False)
        _require_aware(self.updated_at, "updated_at", allow_none=False)
        _require_aware(self.not_before, "not_before")
        _require_aware(self.expires_at, "expires_at")
        if not isinstance(self.dependency_ids, tuple):
            raise DriveValidationError("dependency_ids must be a tuple")
        for dep in self.dependency_ids:
            _require_nonempty(dep, "dependency_ids entry")


@dataclass(frozen=True)
class DriveAssignment:
    """Which creature (if any) currently owns pursuit of a Drive (design §3.4).

    Record-level shape only; scope / graph-membership / one-active-assignee
    constraints are enforced by :mod:`drive_policy`.
    """

    drive_id: str
    assignment_id: str
    revision: int
    lifecycle_epoch: int

    assignee_graph_id: str
    assignment_state: Literal["unassigned", "assigned", "orphaned"]
    updated_at: datetime

    assignee_creature_id: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    assigned_by: ActorRef | None = None
    assigned_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.drive_id, "drive_id")
        _require_nonempty(self.assignment_id, "assignment_id")
        _require_int(self.revision, "revision", minimum=0)
        _require_int(self.lifecycle_epoch, "lifecycle_epoch", minimum=0)
        _require_nonempty(self.assignee_graph_id, "assignee_graph_id")
        _require_in(self.assignment_state, "assignment_state", _ASSIGNMENT_STATES)
        _require_aware(self.updated_at, "updated_at", allow_none=False)
        if self.assignee_creature_id is not None:
            _require_nonempty(self.assignee_creature_id, "assignee_creature_id")
        if self.lease_owner is not None:
            _require_nonempty(self.lease_owner, "lease_owner")
        _require_aware(self.lease_expires_at, "lease_expires_at")
        _require_aware(self.assigned_at, "assigned_at")
        if self.assigned_by is not None:
            _require_actor(self.assigned_by, "assigned_by")


@dataclass(frozen=True)
class DriveDelivery:
    """Represent one physical attempt for a revisioned logical Drive delivery."""

    delivery_id: str
    drive_id: str
    drive_revision: int
    lifecycle_epoch: int
    assignment_id: str
    assignee_creature_id: str

    reason: Literal[
        "activated",
        "ready",
        "retry",
        "resume",
        "recovery",
        "reassigned",
        "dependency_ready",
        "manual_wake",
    ]
    state: Literal[
        "pending",
        "claimed",
        "admitted",
        "acknowledged",
        "retry_wait",
        "dead_letter",
        "superseded",
    ]
    attempt: int
    available_at: datetime
    created_at: datetime

    readiness_generation: int = 0
    claimed_at: datetime | None = None
    admitted_at: datetime | None = None
    acknowledged_at: datetime | None = None
    last_error: str | None = None
    # Why an acknowledged row settled, when the turn did not simply finish.
    ack_reason: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.delivery_id, "delivery_id")
        _require_nonempty(self.drive_id, "drive_id")
        _require_int(self.drive_revision, "drive_revision", minimum=0)
        _require_int(self.lifecycle_epoch, "lifecycle_epoch", minimum=0)
        _require_nonempty(self.assignment_id, "assignment_id")
        _require_nonempty(self.assignee_creature_id, "assignee_creature_id")
        _require_in(self.reason, "reason", _DELIVERY_REASONS)
        _require_in(self.state, "state", _DELIVERY_STATES)
        _require_int(self.attempt, "attempt", minimum=0)
        _require_int(self.readiness_generation, "readiness_generation", minimum=0)
        _require_aware(self.available_at, "available_at", allow_none=False)
        _require_aware(self.created_at, "created_at", allow_none=False)
        _require_aware(self.claimed_at, "claimed_at")
        _require_aware(self.admitted_at, "admitted_at")
        _require_aware(self.acknowledged_at, "acknowledged_at")

    def logical_key(self) -> tuple[str, int, int, str, int]:
        """Return the identity tuple for one logical nonterminal delivery."""
        return (
            self.drive_id,
            self.lifecycle_epoch,
            self.drive_revision,
            self.assignment_id,
            self.readiness_generation,
        )


@dataclass(frozen=True)
class DriveProgress:
    """Append-only observation of progress (design §4.3)."""

    progress_id: str
    drive_id: str
    actor: ActorRef
    summary: str
    created_at: datetime
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonempty(self.progress_id, "progress_id")
        _require_nonempty(self.drive_id, "drive_id")
        _require_actor(self.actor, "actor")
        if not isinstance(self.summary, str):
            raise DriveValidationError("summary must be a string")
        _require_aware(self.created_at, "created_at", allow_none=False)


@dataclass(frozen=True)
class DriveAuditRecord:
    """Immutable record of one canonical mutation (design §7.3)."""

    audit_id: str
    drive_id: str
    revision: int
    lifecycle_epoch: int
    actor: ActorRef
    operation: str
    created_at: datetime

    before_status: DriveStatus | None = None
    after_status: DriveStatus | None = None
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonempty(self.audit_id, "audit_id")
        _require_nonempty(self.drive_id, "drive_id")
        _require_int(self.revision, "revision", minimum=0)
        _require_int(self.lifecycle_epoch, "lifecycle_epoch", minimum=0)
        _require_actor(self.actor, "actor")
        _require_nonempty(self.operation, "operation")
        _require_aware(self.created_at, "created_at", allow_none=False)
        if self.before_status is not None:
            _require_status(self.before_status, "before_status")
        if self.after_status is not None:
            _require_status(self.after_status, "after_status")
