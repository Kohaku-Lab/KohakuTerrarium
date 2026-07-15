"""Creature lifecycle reconciliation for Drives (design §6.1-6.3).

Pure helpers classify interrupted delivery and deterministic removal outcomes.
Lifecycle coroutines use the manager's public mutation surface, preserving its
authorization and transition invariants. Stopping is temporary, removal is
permanent identity loss, and creature-scoped work is always orphaned and blocked
rather than silently reassigned.
"""

from dataclasses import dataclass

from kohakuterrarium.terrarium.drive.models import (
    SYSTEM_ACTOR,
    DriveAssignment,
    DriveDelivery,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.policy import disposition_on_assignee_removed
from kohakuterrarium.terrarium.drive.requests import DriveQuery

# Runnable pursuit work cannot survive removal of its assignee.
_LIVE_DELIVERY_STATES = frozenset({"pending", "claimed", "retry_wait"})


def classify_uncertain(delivery: DriveDelivery) -> bool:
    """Return whether admitted work may have run without acknowledgement.

    Reconciliation must recover this state rather than assuming the attempt was
    never delivered.
    """
    return delivery.state == "admitted" and delivery.acknowledged_at is None


@dataclass(frozen=True)
class RemovalPlan:
    """Deterministic drive and assignment outcome after assignee removal."""

    assignment_state: str
    drive_status: DriveStatus | None
    reassign: bool


def plan_removal(
    record: DriveRecord,
    *,
    on_assignee_removed: str = "default",
    auto_assign: bool = False,
) -> RemovalPlan:
    """Convert removal policy into the lifecycle actions the manager applies."""
    disposition = disposition_on_assignee_removed(
        record, auto_assign=auto_assign, on_assignee_removed=on_assignee_removed
    )
    return RemovalPlan(
        assignment_state=disposition.assignment_state,
        drive_status=disposition.drive_status,
        reassign=disposition.reassign,
    )


def select_auto_assignee(member_ids: frozenset[str], *, exclude: str) -> str | None:
    """Choose the lowest remaining member ID for deterministic auto-assignment."""
    candidates = sorted(m for m in member_ids if m != exclude)
    return candidates[0] if candidates else None


async def on_creature_stopped(manager, creature_id: str) -> None:
    """Release unadmitted claims when a creature stops temporarily.

    Claimed work returns to pending without a failure count. Admitted work remains
    intact because it may already have produced side effects and must be recovered
    after restoration.
    """
    repo = manager.repository
    now = manager.now()
    for record in await repo.list_drives(DriveQuery(assignee_creature_id=creature_id)):
        for delivery in await repo.list_deliveries(record.drive_id):
            if delivery.state == "claimed":
                await repo.mark_delivery(delivery.delivery_id, "pending", now=now)


async def on_creature_removed(
    manager,
    creature_id: str,
    *,
    graph_member_ids: frozenset[str] = frozenset(),
    on_assignee_removed: str = "default",
    auto_assign: bool = False,
) -> tuple[str, ...]:
    """Apply permanent assignee removal and return affected drive IDs.

    Creature-scoped drives orphan and block. Graph-scoped drives follow explicit
    cancellation, deterministic reassignment, or unassignment policy.
    """
    repo = manager.repository
    affected: list[str] = []
    for record in await repo.list_drives(DriveQuery(assignee_creature_id=creature_id)):
        assignment = await repo.get_assignment(record.drive_id)
        plan = plan_removal(
            record, on_assignee_removed=on_assignee_removed, auto_assign=auto_assign
        )
        await _supersede_live_deliveries(manager, record.drive_id)
        await _apply_removal(
            manager, record, assignment, plan, creature_id, graph_member_ids
        )
        affected.append(record.drive_id)
    return tuple(affected)


async def on_creature_restoration_ready(manager, creature_id: str) -> None:
    """Reconcile a creature after restoration has rebuilt its runtime state."""
    await manager.reconcile(creature_id=creature_id)


async def _apply_removal(
    manager,
    record: DriveRecord,
    assignment: DriveAssignment | None,
    plan: RemovalPlan,
    creature_id: str,
    graph_member_ids: frozenset[str],
) -> None:
    graph_id = (
        assignment.assignee_graph_id if assignment is not None else record.scope_id
    )
    if plan.drive_status is DriveStatus.CANCELLED:
        await manager.transition(
            record.drive_id,
            DriveStatus.CANCELLED,
            expected_revision=record.revision,
            actor=SYSTEM_ACTOR,
            status_reason="assignee_removed",
        )
        return
    if record.scope_type == "creature":
        await manager.orphan_and_block(record)
        return
    if plan.reassign:
        target = select_auto_assignee(graph_member_ids, exclude=creature_id)
        if target is not None:
            await manager.assign(
                record.drive_id,
                target,
                graph_id,
                expected_revision=record.revision,
                actor=SYSTEM_ACTOR,
            )
            return
    await manager.unassign(
        record.drive_id, expected_revision=record.revision, actor=SYSTEM_ACTOR
    )


async def _supersede_live_deliveries(manager, drive_id: str) -> None:
    repo = manager.repository
    now = manager.now()
    for delivery in await repo.list_deliveries(drive_id):
        if delivery.state in _LIVE_DELIVERY_STATES:
            await repo.mark_delivery(delivery.delivery_id, "superseded", now=now)
