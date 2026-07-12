"""Creature lifecycle reconciliation for Drives (design §6.1-6.3).

Pure classification (:func:`classify_uncertain`, :func:`plan_removal`,
:func:`select_auto_assignee`) plus the coroutine helpers the manager and Phase E
call on creature stop / removal / restoration-ready. The coroutines drive the
:class:`~kohakuterrarium.terrarium.drive.manager.DriveManager` through its public
surface (repository reads, authorized transitions with the system actor, and
reconcile); they never bypass it. Removal never silently reassigns creature-scoped
work (§6.2) — a creature-scoped Drive is orphaned and blocked instead.
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

# Delivery states that still hold pursuit work and must be superseded on removal.
_LIVE_DELIVERY_STATES = frozenset({"pending", "claimed", "retry_wait"})


# ---------------------------------------------------------------------------
# Pure classification
# ---------------------------------------------------------------------------


def classify_uncertain(delivery: DriveDelivery) -> bool:
    """Whether a delivery is an uncertain prior attempt (§6.1): physically
    admitted to the creature but never acknowledged, so a side effect may have
    run. Such a delivery becomes a ``drive_recovery`` on reconcile."""
    return delivery.state == "admitted" and delivery.acknowledged_at is None


@dataclass(frozen=True)
class RemovalPlan:
    """Deterministic §6.2 outcome when an assigned creature is removed."""

    assignment_state: str
    drive_status: DriveStatus | None
    reassign: bool


def plan_removal(
    record: DriveRecord,
    *,
    on_assignee_removed: str = "default",
    auto_assign: bool = False,
) -> RemovalPlan:
    """Classify a removal per §6.2 (delegates to the pure policy function)."""
    disposition = disposition_on_assignee_removed(
        record, auto_assign=auto_assign, on_assignee_removed=on_assignee_removed
    )
    return RemovalPlan(
        assignment_state=disposition.assignment_state,
        drive_status=disposition.drive_status,
        reassign=disposition.reassign,
    )


def select_auto_assignee(member_ids: frozenset[str], *, exclude: str) -> str | None:
    """Deterministic auto-assign target: lowest remaining member id (§6.2)."""
    candidates = sorted(m for m in member_ids if m != exclude)
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Coroutine helpers (drive the manager's public surface)
# ---------------------------------------------------------------------------


async def on_creature_stopped(manager, creature_id: str) -> None:
    """Stop is temporary: defer this creature's unadmitted claims back to
    pending with NO failure count (§6.1). Admitted rows stay admitted and are
    classified uncertain on the next restoration-ready reconcile."""
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
    """Removal is permanent identity loss (§6.2): creature-scoped Drives orphan
    and block, graph-scoped Drives unassign or deterministically auto-assign, and
    an explicit cancel policy cancels. Returns the affected Drive ids."""
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
    """The restoration barrier is complete (§6.5): reconcile this creature's
    Drives — resume the still-current ones and recover uncertain attempts."""
    await manager.reconcile(creature_id=creature_id)


# ---------------------------------------------------------------------------
# internal removal application
# ---------------------------------------------------------------------------


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
