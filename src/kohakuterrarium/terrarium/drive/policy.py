"""Pure deterministic Drive policy functions (design §3-6).

No clock reads (callers pass ``now``), no I/O, no randomness. Everything here
is a total function over the immutable records in :mod:`drive_models`, so the
runtime layers (repository, manager, dispatcher) can rely on one canonical
answer to every deterministic question Terrarium is allowed to decide (§1.4).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

from kohakuterrarium.terrarium.drive.errors import (
    DrivePermissionError,
    DriveTransitionError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveAssignment,
    DriveDelivery,
    DriveRecord,
    DriveStatus,
    SYSTEM_ACTOR,
    _require_aware,
    _require_int,
    _require_nonempty,
)

# ---------------------------------------------------------------------------
# Status classification and the generic transition graph (design §3.3)
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = frozenset(
    {
        DriveStatus.COMPLETED,
        DriveStatus.FAILED,
        DriveStatus.CANCELLED,
        DriveStatus.RETIRED,
    }
)
# Terminal states that may still advance to RETIRED (RETIRED itself cannot).
RETIRABLE_STATUSES = frozenset(
    {DriveStatus.COMPLETED, DriveStatus.FAILED, DriveStatus.CANCELLED}
)

# The generic edges a Drive may take without an enabling registration policy.
# Suspended states (waiting/paused/blocked) keep generic admin cancel/pause and
# actor-unblock exits so a record whose registration is unavailable stays
# administratable (§8.6, §6.2, §6.7). completed/failed only from active;
# terminal reopen absent by design; retire only from a non-retired terminal.
GENERIC_TRANSITIONS: frozenset[tuple[DriveStatus, DriveStatus]] = frozenset(
    {
        (DriveStatus.DRAFT, DriveStatus.ACTIVE),
        (DriveStatus.DRAFT, DriveStatus.CANCELLED),
        (DriveStatus.ACTIVE, DriveStatus.WAITING),
        (DriveStatus.ACTIVE, DriveStatus.BLOCKED),
        (DriveStatus.ACTIVE, DriveStatus.PAUSED),
        (DriveStatus.ACTIVE, DriveStatus.COMPLETED),
        (DriveStatus.ACTIVE, DriveStatus.FAILED),
        (DriveStatus.ACTIVE, DriveStatus.CANCELLED),
        (DriveStatus.WAITING, DriveStatus.ACTIVE),
        (DriveStatus.PAUSED, DriveStatus.ACTIVE),
        (DriveStatus.BLOCKED, DriveStatus.ACTIVE),
        (DriveStatus.WAITING, DriveStatus.CANCELLED),
        (DriveStatus.PAUSED, DriveStatus.CANCELLED),
        (DriveStatus.BLOCKED, DriveStatus.CANCELLED),
        (DriveStatus.WAITING, DriveStatus.PAUSED),
        (DriveStatus.BLOCKED, DriveStatus.PAUSED),
        (DriveStatus.WAITING, DriveStatus.BLOCKED),
        (DriveStatus.PAUSED, DriveStatus.BLOCKED),
        (DriveStatus.COMPLETED, DriveStatus.RETIRED),
        (DriveStatus.FAILED, DriveStatus.RETIRED),
        (DriveStatus.CANCELLED, DriveStatus.RETIRED),
    }
)


def is_terminal(status: DriveStatus) -> bool:
    return status in TERMINAL_STATUSES


def is_generic_transition(current: DriveStatus, target: DriveStatus) -> bool:
    return (current, target) in GENERIC_TRANSITIONS


def validate_transition(
    current: DriveStatus,
    target: DriveStatus,
    *,
    extra_transitions: frozenset[tuple[DriveStatus, DriveStatus]] = frozenset(),
) -> None:
    """Raise :class:`DriveTransitionError` unless ``current -> target`` is legal;
    ``extra_transitions`` adds registration-specific edges (design §3.3)."""
    if not isinstance(current, DriveStatus) or not isinstance(target, DriveStatus):
        raise DriveValidationError("transition endpoints must be DriveStatus")
    if (current, target) in GENERIC_TRANSITIONS or (
        current,
        target,
    ) in extra_transitions:
        return
    if current == target:
        raise DriveTransitionError(
            f"no-op transition {current.value!r} -> {target.value!r}",
            from_status=current.value,
            to_status=target.value,
        )
    if is_terminal(current):
        raise DriveTransitionError(
            f"cannot reopen terminal Drive {current.value!r} to {target.value!r} "
            "without an enabling registration policy",
            from_status=current.value,
            to_status=target.value,
        )
    raise DriveTransitionError(
        f"transition {current.value!r} -> {target.value!r} is not permitted",
        from_status=current.value,
        to_status=target.value,
    )


# ---------------------------------------------------------------------------
# Deliverability and readiness — distinct concepts (design §3.3 table, §4.4)
# ---------------------------------------------------------------------------


def is_deliverable_status(status: DriveStatus) -> bool:
    """Only ACTIVE is deliverable; WAITING must first wake to ACTIVE (§4.4)."""
    return status is DriveStatus.ACTIVE


def is_time_ready(record: DriveRecord, now: datetime) -> bool:
    return record.not_before is None or record.not_before <= now


def is_expired(record: DriveRecord, now: datetime) -> bool:
    return record.expires_at is not None and record.expires_at <= now


def dependencies_terminal(dependency_statuses: dict[str, DriveStatus]) -> bool:
    """Generic dependency readiness: every dependency has reached a terminal
    state. Registrations refine "reached configured states" (§4.4)."""
    return all(is_terminal(status) for status in dependency_statuses.values())


def wake_conditions_met(
    record: DriveRecord,
    now: datetime,
    dependency_statuses: dict[str, DriveStatus],
) -> bool:
    """Whether a WAITING Drive's deterministic wake conditions are satisfied."""
    return (
        is_time_ready(record, now)
        and not is_expired(record, now)
        and dependencies_terminal(dependency_statuses)
    )


def is_ready_for_delivery(
    record: DriveRecord,
    now: datetime,
    dependency_statuses: dict[str, DriveStatus],
) -> bool:
    return is_deliverable_status(record.status) and wake_conditions_met(
        record, now, dependency_statuses
    )


# ---------------------------------------------------------------------------
# Assignment constraints (design §3.4, §6.2)
# ---------------------------------------------------------------------------


def validate_assignment_target(
    record: DriveRecord,
    *,
    target_creature_id: str | None,
    graph_member_ids: frozenset[str],
) -> None:
    """Raise if assigning ``record`` to ``target_creature_id`` breaks scope.

    ``target_creature_id=None`` means unassign, legal only for graph scope.
    """
    if target_creature_id is None:
        if record.scope_type == "creature":
            raise DriveValidationError(
                f"creature-scoped Drive {record.drive_id!r} cannot be unassigned"
            )
        return
    _require_nonempty(target_creature_id, "target_creature_id")
    if record.scope_type == "creature":
        if target_creature_id != record.scope_id:
            raise DriveValidationError(
                f"creature-scoped Drive {record.drive_id!r} is fixed to "
                f"{record.scope_id!r}, cannot assign {target_creature_id!r}"
            )
        return
    if target_creature_id not in graph_member_ids:
        raise DriveValidationError(
            f"assignee {target_creature_id!r} is not a member of graph "
            f"{record.scope_id!r}"
        )


def validate_assignment_consistency(assignment: DriveAssignment) -> None:
    """Raise if an assignment's state and assignee field disagree (§3.4)."""
    state = assignment.assignment_state
    cid = assignment.assignee_creature_id
    if state == "assigned" and not cid:
        raise DriveValidationError("assigned assignment must name an assignee")
    if state == "unassigned" and cid is not None:
        raise DriveValidationError("unassigned assignment must not name an assignee")


@dataclass(frozen=True)
class RemovalDisposition:
    """What happens to an assignment when its assignee creature is removed."""

    assignment_state: Literal["unassigned", "orphaned"]
    drive_status: DriveStatus | None
    reassign: bool


def disposition_on_assignee_removed(
    record: DriveRecord,
    *,
    auto_assign: bool = False,
    on_assignee_removed: str = "default",
) -> RemovalDisposition:
    """Deterministic §6.2 outcome when an assigned creature is removed."""
    if on_assignee_removed == "cancel":
        state = "orphaned" if record.scope_type == "creature" else "unassigned"
        return RemovalDisposition(state, DriveStatus.CANCELLED, False)
    if record.scope_type == "creature":
        return RemovalDisposition("orphaned", DriveStatus.BLOCKED, False)
    return RemovalDisposition("unassigned", None, auto_assign)


# ---------------------------------------------------------------------------
# Stale / duplicate delivery suppression (design §5.4)
# ---------------------------------------------------------------------------


def is_delivery_stale(
    delivery: DriveDelivery,
    record: DriveRecord | None,
    assignment: DriveAssignment | None,
    *,
    current_readiness_generation: int,
    allow_readmit: bool = False,
) -> bool:
    """Whether a claimed delivery must be superseded before admission (§5.4)."""
    if record is None or not is_deliverable_status(record.status):
        return True
    if delivery.drive_revision != record.revision:
        return True
    if delivery.lifecycle_epoch != record.lifecycle_epoch:
        return True
    if assignment is None:
        return True
    if delivery.assignment_id != assignment.assignment_id:
        return True
    if delivery.assignee_creature_id != assignment.assignee_creature_id:
        return True
    if delivery.readiness_generation != current_readiness_generation:
        return True
    if delivery.state in {"superseded", "dead_letter", "acknowledged"}:
        return True
    if delivery.state == "admitted" and not allow_readmit:
        return True
    return False


# ---------------------------------------------------------------------------
# Deterministic scheduler ordering (design §5.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriveScheduleItem:
    """One schedulable Drive; ``last_delivered_at=None`` (never delivered)
    sorts first, having waited longest."""

    drive_id: str
    available_at: datetime
    priority: int
    created_at: datetime
    last_delivered_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.drive_id, "drive_id")
        _require_int(self.priority, "priority")
        _require_aware(self.available_at, "available_at", allow_none=False)
        _require_aware(self.created_at, "created_at", allow_none=False)
        _require_aware(self.last_delivered_at, "last_delivered_at")


def schedule_sort_key(item: DriveScheduleItem) -> tuple[float, int, float, float, str]:
    """Ascending key giving: available_at ASC, priority DESC, last_delivered_at
    ASC (None first), created_at ASC, drive_id ASC. ``drive_id`` is unique per
    Drive, so the order is total and independent of input order."""
    last = item.last_delivered_at
    last_key = last.timestamp() if last is not None else float("-inf")
    return (
        item.available_at.timestamp(),
        -item.priority,
        last_key,
        item.created_at.timestamp(),
        item.drive_id,
    )


def order_schedule(items: list[DriveScheduleItem]) -> list[DriveScheduleItem]:
    return sorted(items, key=schedule_sort_key)


# ---------------------------------------------------------------------------
# Graph split placement (design §6.7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphComponent:
    """A post-split child graph: its id and the creatures it now holds."""

    graph_id: str
    creature_ids: frozenset[str]


@dataclass(frozen=True)
class SplitPlacement:
    """Where a Drive goes after a split: follow a child, orphan, or clone."""

    kind: Literal["follow", "orphan", "clone"]
    graph_id: str | None = None


def _component_with(
    components: list[GraphComponent], creature_id: str
) -> GraphComponent | None:
    for comp in components:
        if creature_id in comp.creature_ids:
            return comp
    return None


def _largest_component(components: list[GraphComponent]) -> GraphComponent | None:
    if not components:
        return None
    # deterministic: largest by size, ties broken by lowest graph_id.
    return min(components, key=lambda c: (-len(c.creature_ids), c.graph_id))


def select_split_placement(
    record: DriveRecord,
    assignment: DriveAssignment | None,
    components: list[GraphComponent],
    *,
    split_policy: str | None = None,
) -> SplitPlacement:
    """Deterministic §6.7 placement of one Drive across split child graphs."""
    if record.scope_type == "creature":
        comp = _component_with(components, record.scope_id)
        return _follow_or_orphan(comp)

    if (
        assignment is not None
        and assignment.assignment_state == "assigned"
        and assignment.assignee_creature_id is not None
    ):
        comp = _component_with(components, assignment.assignee_creature_id)
        return _follow_or_orphan(comp)

    policy = (
        split_policy or record.policy_options.get("split_policy") or "largest_component"
    )
    if policy.startswith("anchor:"):
        anchor = policy.split(":", 1)[1]
        return _follow_or_orphan(_component_with(components, anchor))
    if policy == "largest_component":
        return _follow_or_orphan(_largest_component(components))
    if policy == "orphan":
        return SplitPlacement(kind="orphan")
    if policy == "clone":
        return SplitPlacement(kind="clone")
    raise DriveValidationError(f"unknown split_policy {policy!r}")


def _follow_or_orphan(comp: GraphComponent | None) -> SplitPlacement:
    if comp is None:
        return SplitPlacement(kind="orphan")
    return SplitPlacement(kind="follow", graph_id=comp.graph_id)


# ---------------------------------------------------------------------------
# Retry classification (design §5.6)
# ---------------------------------------------------------------------------


class DeliveryFailureKind(str, Enum):
    TRANSIENT = "transient"
    UNAVAILABLE_ASSIGNEE = "unavailable_assignee"
    INVALID_OR_STALE = "invalid_or_stale"
    POLICY = "policy"
    TURN_ERROR = "turn_error"


class RetryDisposition(str, Enum):
    RETRY_BACKOFF = "retry_backoff"
    DEFER = "defer"
    SUPERSEDE = "supersede"
    DEAD_LETTER = "dead_letter"
    FAIL_CLOSED = "fail_closed"


def classify_delivery_failure(
    kind: DeliveryFailureKind,
    *,
    attempt: int,
    max_attempts: int,
) -> RetryDisposition:
    """Map a delivery failure to its retry disposition (§5.6)."""
    _require_int(attempt, "attempt", minimum=0)
    _require_int(max_attempts, "max_attempts", minimum=1)
    match kind:
        case DeliveryFailureKind.UNAVAILABLE_ASSIGNEE:
            return RetryDisposition.DEFER
        case DeliveryFailureKind.INVALID_OR_STALE:
            return RetryDisposition.SUPERSEDE
        case DeliveryFailureKind.POLICY:
            return RetryDisposition.FAIL_CLOSED
        case DeliveryFailureKind.TRANSIENT | DeliveryFailureKind.TURN_ERROR:
            if attempt < max_attempts:
                return RetryDisposition.RETRY_BACKOFF
            return RetryDisposition.DEAD_LETTER
    raise DriveValidationError(f"unknown delivery failure kind {kind!r}")


# ---------------------------------------------------------------------------
# Actor capabilities and authorization (design §3.6)
# ---------------------------------------------------------------------------


class DriveCapability(str, Enum):
    READ = "drive.read"
    CREATE_SELF = "drive.create_self"
    CREATE_GRAPH = "drive.create_graph"
    UPDATE_OWNED = "drive.update_owned"
    MANAGE_ASSIGNED = "drive.manage_assigned"
    ASSIGN = "drive.assign"
    TRANSITION = "drive.transition"
    PROPOSE_TERMINAL = "drive.propose_terminal"
    VERIFY_TERMINAL = "drive.verify_terminal"
    TRANSFER_OWNER = "drive.transfer_owner"
    ADMIN = "drive.admin"


class DriveOperation(str, Enum):
    READ = "read"
    CREATE_SELF = "create_self"
    CREATE_GRAPH = "create_graph"
    UPDATE = "update"
    ASSIGN = "assign"
    UNASSIGN = "unassign"
    REASSIGN = "reassign"
    TRANSFER_OWNER = "transfer_owner"
    TRANSITION = "transition"
    PROPOSE_TERMINAL = "propose_terminal"
    VERIFY_TERMINAL = "verify_terminal"
    REPORT_PROGRESS = "report_progress"
    RETIRE = "retire"
    REPLAY = "replay"
    ADMIN = "admin"


_OPERATION_CAPABILITY: dict[DriveOperation, DriveCapability] = {
    DriveOperation.READ: DriveCapability.READ,
    DriveOperation.CREATE_SELF: DriveCapability.CREATE_SELF,
    DriveOperation.CREATE_GRAPH: DriveCapability.CREATE_GRAPH,
    DriveOperation.UPDATE: DriveCapability.UPDATE_OWNED,
    DriveOperation.ASSIGN: DriveCapability.ASSIGN,
    DriveOperation.UNASSIGN: DriveCapability.ASSIGN,
    DriveOperation.REASSIGN: DriveCapability.ASSIGN,
    DriveOperation.TRANSFER_OWNER: DriveCapability.TRANSFER_OWNER,
    DriveOperation.TRANSITION: DriveCapability.TRANSITION,
    DriveOperation.PROPOSE_TERMINAL: DriveCapability.PROPOSE_TERMINAL,
    DriveOperation.VERIFY_TERMINAL: DriveCapability.VERIFY_TERMINAL,
    DriveOperation.RETIRE: DriveCapability.ADMIN,
    DriveOperation.REPLAY: DriveCapability.ADMIN,
    DriveOperation.ADMIN: DriveCapability.ADMIN,
}

# Transitions an assignee (not the owner) may request on a foreign Drive (§3.6).
_ASSIGNEE_TRANSITIONS = frozenset({DriveStatus.WAITING, DriveStatus.BLOCKED})


def _is_assignee(actor: ActorRef, assignment: DriveAssignment | None) -> bool:
    return (
        assignment is not None
        and actor.kind == "creature"
        and assignment.assignee_creature_id is not None
        and actor.identity == assignment.assignee_creature_id
    )


def effective_capabilities(
    actor: ActorRef,
    record: DriveRecord | None,
    assignment: DriveAssignment | None,
    *,
    is_privileged: bool = False,
) -> frozenset[DriveCapability]:
    """Default-policy capabilities an actor holds for one record (§3.6): the
    system actor is omnipotent, else the owner/assignee/privileged model."""
    if actor == SYSTEM_ACTOR:
        return frozenset(DriveCapability)
    caps: set[DriveCapability] = {DriveCapability.READ}
    if actor.kind == "creature":
        caps.add(DriveCapability.CREATE_SELF)
    if is_privileged:
        caps |= {
            DriveCapability.CREATE_GRAPH,
            DriveCapability.ASSIGN,
            DriveCapability.TRANSFER_OWNER,
            DriveCapability.VERIFY_TERMINAL,
            DriveCapability.ADMIN,
            DriveCapability.UPDATE_OWNED,
            DriveCapability.TRANSITION,
            DriveCapability.PROPOSE_TERMINAL,
        }
    if record is not None:
        if actor == record.owner:
            caps |= {
                DriveCapability.UPDATE_OWNED,
                DriveCapability.TRANSITION,
                DriveCapability.PROPOSE_TERMINAL,
            }
        if _is_assignee(actor, assignment):
            caps |= {
                DriveCapability.MANAGE_ASSIGNED,
                DriveCapability.PROPOSE_TERMINAL,
            }
    return frozenset(caps)


def is_operation_allowed(
    actor: ActorRef,
    record: DriveRecord | None,
    assignment: DriveAssignment | None,
    operation: DriveOperation,
    *,
    is_privileged: bool = False,
    target_status: DriveStatus | None = None,
) -> bool:
    """Default-policy authorization decision for one operation (design §3.6)."""
    caps = effective_capabilities(
        actor, record, assignment, is_privileged=is_privileged
    )
    if operation == DriveOperation.REPORT_PROGRESS:
        return bool(
            caps
            & {
                DriveCapability.UPDATE_OWNED,
                DriveCapability.MANAGE_ASSIGNED,
                DriveCapability.ADMIN,
            }
        )
    if operation == DriveOperation.TRANSITION:
        if DriveCapability.TRANSITION in caps:
            return True
        return (
            DriveCapability.MANAGE_ASSIGNED in caps
            and target_status in _ASSIGNEE_TRANSITIONS
        )
    return _OPERATION_CAPABILITY[operation] in caps


def require_operation(
    actor: ActorRef,
    record: DriveRecord | None,
    assignment: DriveAssignment | None,
    operation: DriveOperation,
    *,
    is_privileged: bool = False,
    target_status: DriveStatus | None = None,
) -> None:
    """Raise :class:`DrivePermissionError` if the operation is not allowed."""
    if not is_operation_allowed(
        actor,
        record,
        assignment,
        operation,
        is_privileged=is_privileged,
        target_status=target_status,
    ):
        raise DrivePermissionError(
            f"actor {actor.format()!r} may not {operation.value} this Drive"
        )
