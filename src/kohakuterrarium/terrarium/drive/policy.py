"""Pure deterministic Drive policy functions (design §3-6).

Callers supply time; these functions perform no I/O or randomness. Keeping
status, readiness, assignment, scheduling, retry, split, and authorization
policy pure gives repositories, managers, and dispatchers one canonical answer
to each deterministic decision.
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

TERMINAL_STATUSES = frozenset(
    {
        DriveStatus.COMPLETED,
        DriveStatus.FAILED,
        DriveStatus.CANCELLED,
        DriveStatus.RETIRED,
    }
)
# Retirement is available only after another terminal outcome.
RETIRABLE_STATUSES = frozenset(
    {DriveStatus.COMPLETED, DriveStatus.FAILED, DriveStatus.CANCELLED}
)

# Generic edges remain sufficient to administer suspended drives even when their
# registration is unavailable. Completion and failure require active pursuit,
# terminal reopen requires an extension edge, and retirement follows another
# terminal outcome.
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
    """Validate a generic or registration-approved status transition."""
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


def is_deliverable_status(status: DriveStatus) -> bool:
    """Return whether status permits delivery; waiting drives must wake first."""
    return status is DriveStatus.ACTIVE


def is_time_ready(record: DriveRecord, now: datetime) -> bool:
    return record.not_before is None or record.not_before <= now


def is_expired(record: DriveRecord, now: datetime) -> bool:
    return record.expires_at is not None and record.expires_at <= now


def dependencies_terminal(dependency_statuses: dict[str, DriveStatus]) -> bool:
    """Return whether every dependency reached a terminal state.

    Registrations may impose a more specific dependency-state condition.
    """
    return all(is_terminal(status) for status in dependency_statuses.values())


def wake_conditions_met(
    record: DriveRecord,
    now: datetime,
    dependency_statuses: dict[str, DriveStatus],
) -> bool:
    """Return whether time, expiry, and dependency wake conditions are satisfied."""
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


def validate_assignment_target(
    record: DriveRecord,
    *,
    target_creature_id: str | None,
    graph_member_ids: frozenset[str],
) -> None:
    """Validate that assignment remains within the drive's scope.

    ``None`` means unassignment and is valid only for graph-scoped drives.
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
    """Validate consistency between assignment state and assignee identity."""
    state = assignment.assignment_state
    cid = assignment.assignee_creature_id
    if state == "assigned" and not cid:
        raise DriveValidationError("assigned assignment must name an assignee")
    if state == "unassigned" and cid is not None:
        raise DriveValidationError("unassigned assignment must not name an assignee")


@dataclass(frozen=True)
class RemovalDisposition:
    """Drive and assignment effects of permanent assignee removal."""

    assignment_state: Literal["unassigned", "orphaned"]
    drive_status: DriveStatus | None
    reassign: bool


def disposition_on_assignee_removed(
    record: DriveRecord,
    *,
    auto_assign: bool = False,
    on_assignee_removed: str = "default",
) -> RemovalDisposition:
    """Choose cancellation, orphaning, unassignment, or reassignment intent."""
    if on_assignee_removed == "cancel":
        state = "orphaned" if record.scope_type == "creature" else "unassigned"
        return RemovalDisposition(state, DriveStatus.CANCELLED, False)
    if record.scope_type == "creature":
        return RemovalDisposition("orphaned", DriveStatus.BLOCKED, False)
    return RemovalDisposition("unassigned", None, auto_assign)


def is_delivery_stale(
    delivery: DriveDelivery,
    record: DriveRecord | None,
    assignment: DriveAssignment | None,
    *,
    current_readiness_generation: int,
    allow_readmit: bool = False,
) -> bool:
    """Return whether a claimed delivery no longer matches canonical state."""
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


@dataclass(frozen=True)
class DriveScheduleItem:
    """Scheduling inputs for one drive.

    A missing ``last_delivered_at`` sorts first because the drive has never
    received service.
    """

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
    """Build a total, input-order-independent scheduling key.

    Earlier availability wins, then higher priority, least recent service,
    earlier creation, and finally unique drive ID.
    """
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


@dataclass(frozen=True)
class GraphComponent:
    """Child graph identity and membership after a topology split."""

    graph_id: str
    creature_ids: frozenset[str]


@dataclass(frozen=True)
class SplitPlacement:
    """Placement decision to follow a child, orphan, or clone a drive."""

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
    # Graph ID breaks equal-size ties so placement is independent of input order.
    return min(components, key=lambda c: (-len(c.creature_ids), c.graph_id))


def select_split_placement(
    record: DriveRecord,
    assignment: DriveAssignment | None,
    components: list[GraphComponent],
    *,
    split_policy: str | None = None,
) -> SplitPlacement:
    """Place a drive deterministically across child graphs after a split."""
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
    """Map failure type and attempt budget to a retry disposition."""
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

# Assignees may suspend blocked work but lack the owner's full transition set.
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
    """Derive capabilities from system, privilege, ownership, and assignment."""
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
    """Return whether default capability policy permits an operation."""
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
    """Require default capability policy to permit an operation."""
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
