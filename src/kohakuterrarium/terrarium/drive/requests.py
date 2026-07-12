"""Caller-input DTOs for the Drive runtime resource (design §3-4, §9).

Create / patch / query / transition-proposal request objects. These are the
arguments trusted ingress adapters build; the manager derives ``ActorRef`` and
mints identity/revision fields, so these DTOs deliberately carry *no* canonical
identity a client could forge (a :class:`DrivePatch` has no ``drive_id``,
``revision``, ``owner``, ``scope``, or ``status`` field at all).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveStatus,
    _require_actor,
    _require_aware,
    _require_in,
    _require_int,
    _require_nonempty,
    _SCOPE_TYPES,
)


class _Unset:
    """Sentinel: a :class:`DrivePatch` field the caller did not touch.

    Distinct from ``None``, which for a nullable field means "clear it".
    """

    _instance: "_Unset | None" = None

    def __new__(cls) -> "_Unset":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset()


def _aware_or_clear(value: object, name: str) -> None:
    # Patch datetime fields: None clears the value; anything else must be aware.
    if value is None:
        return
    _require_aware(value, name, allow_none=False)


# ---------------------------------------------------------------------------
# CreateDriveRequest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateDriveRequest:
    """Everything a caller supplies to mint a new Drive (design §9.1)."""

    kind: str
    title: str
    scope_type: Literal["creature", "graph"]
    scope_id: str
    owner: ActorRef
    owner_scope: Literal["actor", "creature", "graph", "service"]
    created_by: ActorRef

    schema_version: int = 1
    spec: dict[str, Any] = field(default_factory=dict)
    presentation: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    assignee_creature_id: str | None = None
    priority: int = 0
    not_before: datetime | None = None
    expires_at: datetime | None = None
    dependency_ids: tuple[str, ...] = ()
    policy_name: str = "generic"
    policy_options: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.kind, "kind")
        _require_nonempty(self.title, "title")
        _require_in(self.scope_type, "scope_type", _SCOPE_TYPES)
        _require_nonempty(self.scope_id, "scope_id")
        _require_actor(self.owner, "owner")
        _require_actor(self.created_by, "created_by")
        _require_in(
            self.owner_scope,
            "owner_scope",
            frozenset({"actor", "creature", "graph", "service"}),
        )
        _require_int(self.schema_version, "schema_version", minimum=1)
        _require_int(self.priority, "priority")
        _require_aware(self.not_before, "not_before")
        _require_aware(self.expires_at, "expires_at")
        if self.assignee_creature_id is not None:
            _require_nonempty(self.assignee_creature_id, "assignee_creature_id")
        _require_nonempty(self.policy_name, "policy_name")
        if not isinstance(self.dependency_ids, tuple):
            raise DriveValidationError("dependency_ids must be a tuple")
        for dep in self.dependency_ids:
            _require_nonempty(dep, "dependency_ids entry")
        if self.idempotency_key is not None:
            _require_nonempty(self.idempotency_key, "idempotency_key")


# ---------------------------------------------------------------------------
# DrivePatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DrivePatch:
    """A partial update to a Drive's non-identity fields (design §4.1).

    Absent fields stay :data:`UNSET`; :meth:`changes` returns only the fields
    the caller actually set. Status transitions, ownership transfer, and
    assignment are separate operations and are intentionally not patchable here.
    """

    title: str | _Unset = UNSET
    spec: dict[str, Any] | _Unset = UNSET
    presentation: dict[str, Any] | _Unset = UNSET
    metadata: dict[str, Any] | _Unset = UNSET
    priority: int | _Unset = UNSET
    status_reason: str | None | _Unset = UNSET
    not_before: datetime | None | _Unset = UNSET
    expires_at: datetime | None | _Unset = UNSET
    dependency_ids: tuple[str, ...] | _Unset = UNSET
    policy_options: dict[str, Any] | _Unset = UNSET

    def __post_init__(self) -> None:
        if self.title is not UNSET:
            _require_nonempty(self.title, "title")
        if self.priority is not UNSET:
            _require_int(self.priority, "priority")
        if self.not_before is not UNSET:
            _aware_or_clear(self.not_before, "not_before")
        if self.expires_at is not UNSET:
            _aware_or_clear(self.expires_at, "expires_at")
        if self.dependency_ids is not UNSET:
            if not isinstance(self.dependency_ids, tuple):
                raise DriveValidationError("dependency_ids must be a tuple")
            for dep in self.dependency_ids:
                _require_nonempty(dep, "dependency_ids entry")

    def changes(self) -> dict[str, Any]:
        """Map of field name -> new value, for fields the caller set."""
        out: dict[str, Any] = {}
        for name in (
            "title",
            "spec",
            "presentation",
            "metadata",
            "priority",
            "status_reason",
            "not_before",
            "expires_at",
            "dependency_ids",
            "policy_options",
        ):
            value = getattr(self, name)
            if value is not UNSET:
                out[name] = value
        return out

    def is_empty(self) -> bool:
        return not self.changes()


# ---------------------------------------------------------------------------
# DriveQuery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriveQuery:
    """Filter for a list/read over Drive records (design §7.2, §9.1)."""

    graph_id: str | None = None
    scope_type: Literal["creature", "graph"] | None = None
    scope_id: str | None = None
    statuses: frozenset[DriveStatus] | None = None
    kinds: frozenset[str] | None = None
    assignee_creature_id: str | None = None
    owner: ActorRef | None = None
    include_terminal: bool = True
    limit: int | None = None

    def __post_init__(self) -> None:
        if self.graph_id is not None:
            _require_nonempty(self.graph_id, "graph_id")
        if self.scope_type is not None:
            _require_in(self.scope_type, "scope_type", _SCOPE_TYPES)
        if self.scope_id is not None:
            _require_nonempty(self.scope_id, "scope_id")
        if self.statuses is not None:
            for status in self.statuses:
                if not isinstance(status, DriveStatus):
                    raise DriveValidationError(
                        f"statuses entries must be DriveStatus, got {status!r}"
                    )
        if self.kinds is not None:
            for kind in self.kinds:
                _require_nonempty(kind, "kinds entry")
        if self.assignee_creature_id is not None:
            _require_nonempty(self.assignee_creature_id, "assignee_creature_id")
        if self.owner is not None:
            _require_actor(self.owner, "owner")
        if self.limit is not None:
            _require_int(self.limit, "limit", minimum=1)


# ---------------------------------------------------------------------------
# DriveTransitionProposal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriveTransitionProposal:
    """A proposed status transition awaiting validation (design §4.2).

    Terminal transitions (completed/failed) go through this instead of a direct
    status write so registration validators and verifiers can run first.
    """

    proposal_id: str
    drive_id: str
    target_status: DriveStatus
    proposed_by: ActorRef
    created_at: datetime

    reason: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    expected_revision: int | None = None
    # Pinned lifecycle epoch at proposal creation. Persisted so approval can
    # reject a proposal whose Drive has since been reassigned (R1-08).
    lifecycle_epoch: int = 0

    def __post_init__(self) -> None:
        _require_nonempty(self.proposal_id, "proposal_id")
        _require_nonempty(self.drive_id, "drive_id")
        if not isinstance(self.target_status, DriveStatus):
            raise DriveValidationError(
                f"target_status must be a DriveStatus, got {self.target_status!r}"
            )
        _require_actor(self.proposed_by, "proposed_by")
        _require_aware(self.created_at, "created_at", allow_none=False)
        if self.expected_revision is not None:
            _require_int(self.expected_revision, "expected_revision", minimum=0)
        _require_int(self.lifecycle_epoch, "lifecycle_epoch", minimum=0)
