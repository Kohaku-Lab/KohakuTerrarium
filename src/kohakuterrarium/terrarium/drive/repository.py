"""Transactional Drive repository — contract, row types, and pure builders.

Storage-agnostic Drive repository protocols, rows, and mutation builders.
"""

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

from kohakuterrarium.terrarium.drive.errors import (
    DriveConflictError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveAssignment,
    DriveAuditRecord,
    DriveDelivery,
    DriveProgress,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.policy import is_terminal, validate_transition
from kohakuterrarium.terrarium.drive.repository_rows import (
    DriveDeadLetter,
    DriveOutboxEntry,
    IdempotencyRecord,
    dead_letter_from_wire,
    dead_letter_to_wire,
    idempotency_from_wire,
    idempotency_to_wire,
    outbox_from_wire,
    outbox_to_wire,
    parse_dt,
)
from kohakuterrarium.terrarium.drive.requests import (
    CreateDriveRequest,
    DrivePatch,
    DriveQuery,
    DriveTransitionProposal,
)

# Preserve the original import surface after moving row definitions to a submodule.
__all__ = [
    "DriveDeadLetter",
    "DriveOutboxEntry",
    "IdempotencyRecord",
    "dead_letter_from_wire",
    "dead_letter_to_wire",
    "idempotency_from_wire",
    "idempotency_to_wire",
    "outbox_from_wire",
    "outbox_to_wire",
    "parse_dt",
]

# Delivery states whose pending work a new revision / reassignment invalidates.
SUPERSEDABLE_DELIVERY_STATES = frozenset({"pending", "claimed", "retry_wait"})


@dataclass
class Mutation:
    """Rows and identifier-only effects committed as one repository mutation.

    Every list is applied in one transaction; an empty list touches nothing.
    ``outbox_dispatched`` / ``deleted_deliveries`` carry id-only side effects.
    ``deleted_drives`` cascades to every row associated with each Drive.
    """

    drives: list[DriveRecord] = field(default_factory=list)
    assignments: list[DriveAssignment] = field(default_factory=list)
    deliveries: list[DriveDelivery] = field(default_factory=list)
    audit: list[DriveAuditRecord] = field(default_factory=list)
    progress: list[DriveProgress] = field(default_factory=list)
    outbox: list[DriveOutboxEntry] = field(default_factory=list)
    dead_letters: list[DriveDeadLetter] = field(default_factory=list)
    idempotency: list[IdempotencyRecord] = field(default_factory=list)
    proposals: list["DriveTransitionProposal"] = field(default_factory=list)
    outbox_dispatched: list[str] = field(default_factory=list)
    deleted_deliveries: list[str] = field(default_factory=list)
    deleted_proposals: list[str] = field(default_factory=list)
    deleted_drives: list[str] = field(default_factory=list)


class DriveTransaction(Protocol):
    """One atomic unit of work. Reads see committed state; :meth:`apply`
    stages a :class:`Mutation`; nothing persists until :meth:`commit`."""

    async def begin(self) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def apply(self, mutation: Mutation) -> None: ...

    async def get_drive(self, drive_id: str) -> DriveRecord | None: ...
    async def get_assignment(self, drive_id: str) -> DriveAssignment | None: ...
    async def get_delivery(self, delivery_id: str) -> DriveDelivery | None: ...
    async def get_idempotency(
        self, actor: str, key: str
    ) -> IdempotencyRecord | None: ...
    async def get_proposal(
        self, proposal_id: str
    ) -> DriveTransitionProposal | None: ...
    async def all_proposals(self) -> list[DriveTransitionProposal]: ...
    async def all_idempotency(self) -> list[IdempotencyRecord]: ...
    async def all_drives(self) -> list[DriveRecord]: ...
    async def all_deliveries(self) -> list[DriveDelivery]: ...
    async def all_dead_letters(self) -> list[DriveDeadLetter]: ...
    async def deliveries_for_drive(self, drive_id: str) -> list[DriveDelivery]: ...
    async def progress_for_drive(self, drive_id: str) -> list[DriveProgress]: ...
    async def audit_for_drive(self, drive_id: str) -> list[DriveAuditRecord]: ...
    async def outbox_entries(
        self, *, include_dispatched: bool
    ) -> list[DriveOutboxEntry]: ...


class DriveRepository(Protocol):
    """Repository operations required by the Drive manager."""

    def transaction(self) -> Any: ...
    async def get(self, drive_id: str) -> DriveRecord | None: ...
    async def list_drives(self, query: DriveQuery) -> tuple[DriveRecord, ...]: ...
    async def claim_deliveries(
        self, owner: str, limit: int, *, lease_seconds: float
    ) -> tuple[DriveDelivery, ...]: ...
    def watch_outbox(self) -> AsyncIterator[DriveOutboxEntry]: ...
    @property
    def durability(self) -> str: ...
    async def close(self) -> None: ...


def require_revision(record: DriveRecord, expected: int | None) -> None:
    """Optimistic-concurrency gate: raise unless ``expected`` is current."""
    if expected is None or expected != record.revision:
        raise DriveConflictError(
            f"expected revision {expected}, current is {record.revision}",
            expected_revision=expected,
            actual_revision=record.revision,
        )


def op_hash(operation: str, payload: dict[str, Any]) -> str:
    """Return a stable hash for an operation and its payload."""
    blob = json.dumps(
        {"op": operation, "payload": payload}, sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def new_audit(
    record: DriveRecord,
    actor: ActorRef,
    operation: str,
    now: datetime,
    audit_id: str,
    *,
    before: DriveStatus | None = None,
    after: DriveStatus | None = None,
    summary: str = "",
    details: dict[str, Any] | None = None,
) -> DriveAuditRecord:
    """Build an audit row for a Drive record revision."""
    return DriveAuditRecord(
        audit_id=audit_id,
        drive_id=record.drive_id,
        revision=record.revision,
        lifecycle_epoch=record.lifecycle_epoch,
        actor=actor,
        operation=operation,
        created_at=now,
        before_status=before,
        after_status=after,
        summary=summary,
        details=details or {},
    )


def new_outbox(
    drive_id: str,
    kind: str,
    now: datetime,
    outbox_id: str,
    *,
    delivery_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> DriveOutboxEntry:
    """Build an undispatched outbox entry for a Drive event."""
    return DriveOutboxEntry(
        outbox_id=outbox_id,
        drive_id=drive_id,
        kind=kind,
        created_at=now,
        delivery_id=delivery_id,
        payload=payload or {},
    )


# Builders remain deterministic by receiving both their identifier factory and time.
Mint = Any  # Callable[[], str]


def _initial_assignment(
    drive_id: str,
    request: CreateDriveRequest,
    graph_id: str,
    actor: ActorRef,
    now: datetime,
    mint: Mint,
) -> DriveAssignment:
    if request.scope_type == "creature":
        assignee = request.assignee_creature_id or request.scope_id
    else:
        assignee = request.assignee_creature_id
    return DriveAssignment(
        drive_id=drive_id,
        assignment_id=mint(),
        revision=1,
        lifecycle_epoch=0,
        assignee_graph_id=graph_id,
        assignment_state="assigned" if assignee else "unassigned",
        updated_at=now,
        assignee_creature_id=assignee,
        assigned_by=actor if assignee else None,
        assigned_at=now if assignee else None,
    )


def _operator_grant_audit(
    record: DriveRecord, actor: ActorRef, now: datetime, mint: Mint, details: dict
) -> DriveAuditRecord:
    """Build the operator-grant audit committed with an elevated mutation."""
    return new_audit(
        record,
        actor,
        "operator_grant",
        now,
        mint(),
        summary="operator capability elevation",
        details=details,
    )


def build_create(
    request: CreateDriveRequest,
    *,
    actor: ActorRef,
    graph_id: str,
    status: DriveStatus,
    now: datetime,
    mint: Mint,
    drive_id: str | None = None,
    operator_grant: dict[str, Any] | None = None,
) -> tuple[Mutation, DriveRecord]:
    if request.scope_type == "graph" and request.scope_id != graph_id:
        raise DriveValidationError(
            "graph-scoped Drive scope_id must equal its graph_id"
        )
    drive_id = drive_id or mint()
    record = DriveRecord(
        drive_id=drive_id,
        kind=request.kind,
        schema_version=request.schema_version,
        revision=1,
        title=request.title,
        spec=dict(request.spec),
        presentation=dict(request.presentation),
        metadata=dict(request.metadata),
        scope_type=request.scope_type,
        scope_id=request.scope_id,
        origin_scope_id=request.scope_id,
        status=status,
        status_reason=None,
        priority=request.priority,
        policy_name=request.policy_name,
        created_by=request.created_by,
        owner=request.owner,
        owner_scope=request.owner_scope,
        created_at=now,
        updated_by=actor,
        updated_at=now,
        lifecycle_epoch=0,
        not_before=request.not_before,
        expires_at=request.expires_at,
        dependency_ids=request.dependency_ids,
        policy_options=dict(request.policy_options),
    )
    assignment = _initial_assignment(drive_id, request, graph_id, actor, now, mint)
    audit = new_audit(
        record, actor, "create", now, mint(), after=status, summary=record.title
    )
    outbox = new_outbox(drive_id, "drive_created", now, mint())
    audits = [audit]
    if operator_grant is not None:
        audits.append(_operator_grant_audit(record, actor, now, mint, operator_grant))
    mutation = Mutation(
        drives=[record], assignments=[assignment], audit=audits, outbox=[outbox]
    )
    return mutation, record


def build_update(
    current: DriveRecord,
    patch: DrivePatch,
    *,
    actor: ActorRef,
    now: datetime,
    mint: Mint,
) -> tuple[Mutation, DriveRecord]:
    updated = replace(
        current,
        revision=current.revision + 1,
        updated_by=actor,
        updated_at=now,
        **patch.changes(),
    )
    audit = new_audit(updated, actor, "update", now, mint())
    outbox = new_outbox(current.drive_id, "drive_updated", now, mint())
    return Mutation(drives=[updated], audit=[audit], outbox=[outbox]), updated


def _validate_reassign(record: DriveRecord, assignee: str | None) -> None:
    if assignee is None:
        if record.scope_type == "creature":
            raise DriveValidationError(
                f"creature-scoped Drive {record.drive_id!r} cannot be unassigned"
            )
        return
    if record.scope_type == "creature" and assignee != record.scope_id:
        raise DriveValidationError(
            f"creature-scoped Drive {record.drive_id!r} is fixed to "
            f"{record.scope_id!r}"
        )


def build_reassign(
    current: DriveRecord,
    prev_assignment: DriveAssignment | None,
    superseded: list[DriveDelivery],
    *,
    assignee: str | None,
    graph: str | None,
    actor: ActorRef,
    operation: str,
    now: datetime,
    mint: Mint,
    operator_grant: dict[str, Any] | None = None,
) -> tuple[Mutation, DriveRecord]:
    _validate_reassign(current, assignee)
    epoch = current.lifecycle_epoch + 1
    updated = replace(
        current,
        revision=current.revision + 1,
        lifecycle_epoch=epoch,
        updated_by=actor,
        updated_at=now,
    )
    assignee_graph = graph or (
        prev_assignment.assignee_graph_id
        if prev_assignment is not None
        else current.scope_id
    )
    assignment = DriveAssignment(
        drive_id=current.drive_id,
        assignment_id=mint(),
        revision=updated.revision,
        lifecycle_epoch=epoch,
        assignee_graph_id=assignee_graph,
        assignment_state="assigned" if assignee else "unassigned",
        updated_at=now,
        assignee_creature_id=assignee,
        assigned_by=actor if assignee else None,
        assigned_at=now if assignee else None,
    )
    audit = new_audit(
        updated, actor, operation, now, mint(), details={"assignee": assignee}
    )
    outbox = new_outbox(
        current.drive_id,
        "drive_reassigned" if assignee else "drive_unassigned",
        now,
        mint(),
    )
    audits = [audit]
    if operator_grant is not None:
        audits.append(_operator_grant_audit(updated, actor, now, mint, operator_grant))
    mutation = Mutation(
        drives=[updated],
        assignments=[assignment],
        deliveries=[replace(d, state="superseded") for d in superseded],
        audit=audits,
        outbox=[outbox],
    )
    return mutation, updated


def build_transition(
    current: DriveRecord,
    target: DriveStatus,
    *,
    actor: ActorRef,
    terminal_evidence: dict[str, Any] | None,
    status_reason: str | None,
    extra_transitions: frozenset[tuple[DriveStatus, DriveStatus]],
    operation: str,
    now: datetime,
    mint: Mint,
    operator_grant: dict[str, Any] | None = None,
) -> tuple[Mutation, DriveRecord]:
    validate_transition(current.status, target, extra_transitions=extra_transitions)
    fields: dict[str, Any] = {
        "status": target,
        "revision": current.revision + 1,
        "updated_by": actor,
        "updated_at": now,
    }
    if status_reason is not None:
        fields["status_reason"] = status_reason
    if is_terminal(target) and terminal_evidence is not None:
        fields["terminal_evidence"] = terminal_evidence
    updated = replace(current, **fields)
    audit = new_audit(
        updated, actor, operation, now, mint(), before=current.status, after=target
    )
    outbox = new_outbox(current.drive_id, "drive_status_changed", now, mint())
    audits = [audit]
    if operator_grant is not None:
        audits.append(_operator_grant_audit(updated, actor, now, mint, operator_grant))
    return Mutation(drives=[updated], audit=audits, outbox=[outbox]), updated


def build_progress(
    current: DriveRecord,
    *,
    summary: str,
    evidence: dict[str, Any] | None,
    actor: ActorRef,
    now: datetime,
    mint: Mint,
) -> tuple[Mutation, DriveProgress]:
    progress = DriveProgress(
        progress_id=mint(),
        drive_id=current.drive_id,
        actor=actor,
        summary=summary,
        created_at=now,
        evidence=dict(evidence or {}),
    )
    # Progress preserves the current revision because it does not rewrite the Drive.
    audit = DriveAuditRecord(
        audit_id=mint(),
        drive_id=current.drive_id,
        revision=current.revision,
        lifecycle_epoch=current.lifecycle_epoch,
        actor=actor,
        operation="report_progress",
        created_at=now,
        summary=summary,
    )
    return Mutation(progress=[progress], audit=[audit]), progress


def in_graph(
    record: DriveRecord, assignment: DriveAssignment | None, graph_id: str
) -> bool:
    if assignment is not None and assignment.assignee_graph_id == graph_id:
        return True
    return record.scope_type == "graph" and record.scope_id == graph_id


def matches_query(
    query: DriveQuery, record: DriveRecord, assignment: DriveAssignment | None
) -> bool:
    if query.graph_id is not None and not in_graph(record, assignment, query.graph_id):
        return False
    if query.scope_type is not None and record.scope_type != query.scope_type:
        return False
    if query.scope_id is not None and record.scope_id != query.scope_id:
        return False
    if query.statuses is not None and record.status not in query.statuses:
        return False
    if query.kinds is not None and record.kind not in query.kinds:
        return False
    if query.assignee_creature_id is not None and (
        assignment is None
        or assignment.assignee_creature_id != query.assignee_creature_id
    ):
        return False
    if query.owner is not None and record.owner != query.owner:
        return False
    if not query.include_terminal and is_terminal(record.status):
        return False
    return True
