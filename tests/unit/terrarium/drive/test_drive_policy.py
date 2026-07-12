"""Unit tests for :mod:`kohakuterrarium.terrarium.drive.policy`."""

import random
from datetime import datetime, timedelta, timezone

import pytest

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
)
from kohakuterrarium.terrarium.drive import policy as dp

S = DriveStatus
NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
PAST = NOW - timedelta(hours=1)
FUTURE = NOW + timedelta(hours=1)
OWNER = ActorRef("creature", "owner")
WORKER = ActorRef("creature", "worker")
USER = ActorRef("user", "alice")


def make_record(**overrides):
    base = dict(
        drive_id="d1",
        kind="generic",
        schema_version=1,
        revision=1,
        title="t",
        spec={},
        presentation={},
        metadata={},
        scope_type="graph",
        scope_id="g1",
        origin_scope_id="g1",
        status=S.ACTIVE,
        status_reason=None,
        priority=0,
        policy_name="generic",
        created_by=OWNER,
        owner=OWNER,
        owner_scope="creature",
        created_at=NOW,
        updated_by=OWNER,
        updated_at=NOW,
        lifecycle_epoch=0,
    )
    base.update(overrides)
    return DriveRecord(**base)


def make_assignment(**overrides):
    base = dict(
        drive_id="d1",
        assignment_id="a1",
        revision=1,
        lifecycle_epoch=0,
        assignee_graph_id="g1",
        assignment_state="assigned",
        updated_at=NOW,
        assignee_creature_id="worker",
    )
    base.update(overrides)
    return DriveAssignment(**base)


def make_delivery(**overrides):
    base = dict(
        delivery_id="del1",
        drive_id="d1",
        drive_revision=1,
        lifecycle_epoch=0,
        assignment_id="a1",
        assignee_creature_id="worker",
        reason="ready",
        state="pending",
        attempt=0,
        available_at=NOW,
        created_at=NOW,
        readiness_generation=0,
    )
    base.update(overrides)
    return DriveDelivery(**base)


# ---------------------------------------------------------------------------
# Transition graph (design §3.3)
# ---------------------------------------------------------------------------

# Generic admin/actor exits out of the suspended states (waiting/paused/blocked)
# that must exist WITHOUT a loadable registration policy (design §8.6, §3.3,
# §6.2, §6.7).
SUSPENDED_EXIT_EDGES = {
    (S.WAITING, S.CANCELLED),
    (S.PAUSED, S.CANCELLED),
    (S.BLOCKED, S.CANCELLED),
    (S.WAITING, S.PAUSED),
    (S.BLOCKED, S.PAUSED),
    (S.BLOCKED, S.ACTIVE),
    (S.WAITING, S.BLOCKED),
    (S.PAUSED, S.BLOCKED),
}

EXPECTED_EDGES = {
    (S.DRAFT, S.ACTIVE),
    (S.DRAFT, S.CANCELLED),
    (S.ACTIVE, S.WAITING),
    (S.ACTIVE, S.BLOCKED),
    (S.ACTIVE, S.PAUSED),
    (S.ACTIVE, S.COMPLETED),
    (S.ACTIVE, S.FAILED),
    (S.ACTIVE, S.CANCELLED),
    (S.WAITING, S.ACTIVE),
    (S.PAUSED, S.ACTIVE),
    (S.COMPLETED, S.RETIRED),
    (S.FAILED, S.RETIRED),
    (S.CANCELLED, S.RETIRED),
} | SUSPENDED_EXIT_EDGES


def test_generic_transitions_match_design_exactly():
    assert dp.GENERIC_TRANSITIONS == EXPECTED_EDGES
    assert len(dp.GENERIC_TRANSITIONS) == 21


@pytest.mark.parametrize(
    "edge", sorted(EXPECTED_EDGES, key=lambda e: (e[0].value, e[1].value))
)
def test_every_legal_edge_is_accepted(edge):
    dp.validate_transition(edge[0], edge[1])  # no raise
    assert dp.is_generic_transition(edge[0], edge[1])


@pytest.mark.parametrize(
    "edge", sorted(SUSPENDED_EXIT_EDGES, key=lambda e: (e[0].value, e[1].value))
)
def test_suspended_state_admin_exits_are_generic(edge):
    # These must be legal with no extra_transitions so a Drive whose
    # registration is unavailable is still administratable (§8.6).
    assert edge in dp.GENERIC_TRANSITIONS
    assert dp.is_generic_transition(*edge)
    dp.validate_transition(*edge)  # no raise


def test_terminal_reopen_rejected_by_default():
    for terminal in (S.COMPLETED, S.FAILED, S.CANCELLED):
        with pytest.raises(DriveTransitionError):
            dp.validate_transition(terminal, S.ACTIVE)
    with pytest.raises(DriveTransitionError):
        dp.validate_transition(S.RETIRED, S.ACTIVE)


def test_no_op_transition_rejected():
    with pytest.raises(DriveTransitionError):
        dp.validate_transition(S.ACTIVE, S.ACTIVE)


@pytest.mark.parametrize(
    "current,target",
    [
        # completed/failed reachable only from active; keeps the graph closed
        # around the newly-generic suspended exits
        (S.WAITING, S.COMPLETED),
        (S.BLOCKED, S.COMPLETED),
        (S.WAITING, S.FAILED),
        (S.PAUSED, S.COMPLETED),
        # paused/draft have no generic edge into waiting/blocked/paused
        (S.PAUSED, S.WAITING),
        (S.DRAFT, S.PAUSED),
        (S.DRAFT, S.BLOCKED),
        (S.DRAFT, S.COMPLETED),
        (S.DRAFT, S.WAITING),
        # retired is fully terminal
        (S.RETIRED, S.RETIRED),
        (S.RETIRED, S.CANCELLED),
    ],
)
def test_illegal_edges_rejected(current, target):
    with pytest.raises(DriveTransitionError):
        dp.validate_transition(current, target)


def test_extra_transitions_allow_non_generic_edge():
    extra = frozenset({(S.COMPLETED, S.ACTIVE)})
    dp.validate_transition(S.COMPLETED, S.ACTIVE, extra_transitions=extra)  # no raise
    # a non-generic edge is legal only when a registration lists it
    with pytest.raises(DriveTransitionError):
        dp.validate_transition(S.WAITING, S.COMPLETED)
    dp.validate_transition(
        S.WAITING, S.COMPLETED, extra_transitions=frozenset({(S.WAITING, S.COMPLETED)})
    )


def test_transition_endpoints_must_be_status():
    with pytest.raises(DriveValidationError):
        dp.validate_transition("active", S.WAITING)


def test_is_terminal():
    assert dp.is_terminal(S.COMPLETED)
    assert dp.is_terminal(S.RETIRED)
    assert not dp.is_terminal(S.ACTIVE)
    assert not dp.is_terminal(S.WAITING)


# ---------------------------------------------------------------------------
# Deliverability + readiness (design §3.3 table, §4.4)
# ---------------------------------------------------------------------------


def test_only_active_is_deliverable_status():
    assert dp.is_deliverable_status(S.ACTIVE)
    for status in S:
        if status is not S.ACTIVE:
            assert not dp.is_deliverable_status(status)


def test_time_ready_and_expiry():
    assert dp.is_time_ready(make_record(not_before=None), NOW)
    assert dp.is_time_ready(make_record(not_before=PAST), NOW)
    assert not dp.is_time_ready(make_record(not_before=FUTURE), NOW)
    assert dp.is_expired(make_record(expires_at=PAST), NOW)
    assert not dp.is_expired(make_record(expires_at=FUTURE), NOW)
    assert not dp.is_expired(make_record(expires_at=None), NOW)


def test_dependencies_terminal():
    assert dp.dependencies_terminal({})
    assert dp.dependencies_terminal({"d2": S.COMPLETED, "d3": S.CANCELLED})
    assert not dp.dependencies_terminal({"d2": S.COMPLETED, "d3": S.ACTIVE})


def test_is_ready_for_delivery_requires_active_and_conditions():
    rec = make_record(status=S.ACTIVE, not_before=PAST)
    assert dp.is_ready_for_delivery(rec, NOW, {})
    # waiting is not deliverable even when its wake conditions are met
    waiting = make_record(status=S.WAITING, not_before=PAST)
    assert dp.wake_conditions_met(waiting, NOW, {})
    assert not dp.is_ready_for_delivery(waiting, NOW, {})
    # active but future not_before / expired / pending dep => not ready
    assert not dp.is_ready_for_delivery(make_record(not_before=FUTURE), NOW, {})
    assert not dp.is_ready_for_delivery(make_record(expires_at=PAST), NOW, {})
    assert not dp.is_ready_for_delivery(rec, NOW, {"d2": S.ACTIVE})


# ---------------------------------------------------------------------------
# Assignment constraints (design §3.4, §6.2)
# ---------------------------------------------------------------------------


def test_creature_scoped_assignee_is_fixed():
    rec = make_record(scope_type="creature", scope_id="worker")
    dp.validate_assignment_target(
        rec, target_creature_id="worker", graph_member_ids=frozenset()
    )
    with pytest.raises(DriveValidationError):
        dp.validate_assignment_target(
            rec, target_creature_id="other", graph_member_ids=frozenset({"other"})
        )
    with pytest.raises(DriveValidationError):
        dp.validate_assignment_target(
            rec, target_creature_id=None, graph_member_ids=frozenset()
        )


def test_graph_scoped_assignment_requires_membership():
    rec = make_record(scope_type="graph", scope_id="g1")
    dp.validate_assignment_target(
        rec, target_creature_id="worker", graph_member_ids=frozenset({"worker", "x"})
    )
    with pytest.raises(DriveValidationError):
        dp.validate_assignment_target(
            rec, target_creature_id="stranger", graph_member_ids=frozenset({"worker"})
        )
    # graph-scoped may be unassigned
    dp.validate_assignment_target(
        rec, target_creature_id=None, graph_member_ids=frozenset({"worker"})
    )


def test_assignment_consistency():
    dp.validate_assignment_consistency(make_assignment())
    dp.validate_assignment_consistency(
        make_assignment(assignment_state="unassigned", assignee_creature_id=None)
    )
    dp.validate_assignment_consistency(
        make_assignment(assignment_state="orphaned", assignee_creature_id=None)
    )
    with pytest.raises(DriveValidationError):
        dp.validate_assignment_consistency(
            make_assignment(assignment_state="assigned", assignee_creature_id=None)
        )
    with pytest.raises(DriveValidationError):
        dp.validate_assignment_consistency(
            make_assignment(
                assignment_state="unassigned", assignee_creature_id="worker"
            )
        )


def test_disposition_on_assignee_removed():
    creature = make_record(scope_type="creature", scope_id="worker")
    d = dp.disposition_on_assignee_removed(creature)
    assert d.assignment_state == "orphaned"
    assert d.drive_status is S.BLOCKED
    assert d.reassign is False

    graph = make_record(scope_type="graph")
    manual = dp.disposition_on_assignee_removed(graph, auto_assign=False)
    assert manual.assignment_state == "unassigned"
    assert manual.drive_status is None
    assert manual.reassign is False

    auto = dp.disposition_on_assignee_removed(graph, auto_assign=True)
    assert auto.reassign is True

    cancel = dp.disposition_on_assignee_removed(creature, on_assignee_removed="cancel")
    assert cancel.drive_status is S.CANCELLED


# ---------------------------------------------------------------------------
# Stale / duplicate delivery suppression (design §5.4)
# ---------------------------------------------------------------------------


def test_fresh_delivery_is_not_stale():
    assert not dp.is_delivery_stale(
        make_delivery(),
        make_record(),
        make_assignment(),
        current_readiness_generation=0,
    )


@pytest.mark.parametrize(
    "record,assignment,delivery_overrides,gen",
    [
        (None, make_assignment(), {}, 0),
        (make_record(status=S.COMPLETED), make_assignment(), {}, 0),
        (make_record(revision=2), make_assignment(), {}, 0),
        (make_record(lifecycle_epoch=1), make_assignment(), {}, 0),
        (make_record(), None, {}, 0),
        (make_record(), make_assignment(assignment_id="a2"), {}, 0),
        (
            make_record(),
            make_assignment(assignee_creature_id="other"),
            {},
            0,
        ),
        (make_record(), make_assignment(), {}, 1),
        (make_record(), make_assignment(), {"state": "superseded"}, 0),
        (make_record(), make_assignment(), {"state": "dead_letter"}, 0),
        (make_record(), make_assignment(), {"state": "acknowledged"}, 0),
        (make_record(), make_assignment(), {"state": "admitted"}, 0),
    ],
)
def test_stale_delivery_suppressed(record, assignment, delivery_overrides, gen):
    delivery = make_delivery(**delivery_overrides)
    assert dp.is_delivery_stale(
        delivery, record, assignment, current_readiness_generation=gen
    )


def test_admitted_delivery_readmit_allowed_when_flagged():
    delivery = make_delivery(state="admitted")
    assert dp.is_delivery_stale(
        delivery, make_record(), make_assignment(), current_readiness_generation=0
    )
    assert not dp.is_delivery_stale(
        delivery,
        make_record(),
        make_assignment(),
        current_readiness_generation=0,
        allow_readmit=True,
    )


# ---------------------------------------------------------------------------
# Deterministic scheduler ordering (design §5.5)
# ---------------------------------------------------------------------------


def sched(drive_id, *, available_at=NOW, priority=0, created_at=NOW, last=None):
    return dp.DriveScheduleItem(
        drive_id=drive_id,
        available_at=available_at,
        priority=priority,
        created_at=created_at,
        last_delivered_at=last,
    )


def test_scheduler_orders_by_all_five_keys():
    a = sched("a", available_at=PAST)  # earliest available => first
    b = sched("b", priority=5)  # higher priority
    c = sched("c", priority=1)
    ordered = dp.order_schedule([c, b, a])
    assert [x.drive_id for x in ordered] == ["a", "b", "c"]


def test_scheduler_never_delivered_sorts_before_delivered():
    fresh = sched("fresh", last=None)
    old = sched("old", last=PAST)
    assert dp.order_schedule([old, fresh])[0].drive_id == "fresh"


def test_scheduler_drive_id_is_final_tiebreak():
    items = [sched("z"), sched("a"), sched("m")]
    assert [x.drive_id for x in dp.order_schedule(items)] == ["a", "m", "z"]


def test_scheduler_order_is_total_and_deterministic():
    # Distinct drive_ids across otherwise-equal items => a strict total order,
    # so sorting shuffled copies always yields the identical sequence.
    base = [
        sched(f"d{i}", priority=i % 3, available_at=NOW + timedelta(seconds=i % 2))
        for i in range(40)
    ]
    reference = [x.drive_id for x in dp.order_schedule(base)]
    rng = random.Random(1234)
    for _ in range(25):
        shuffled = base[:]
        rng.shuffle(shuffled)
        assert [x.drive_id for x in dp.order_schedule(shuffled)] == reference


# ---------------------------------------------------------------------------
# Graph split placement (design §6.7)
# ---------------------------------------------------------------------------


def comp(gid, *members):
    return dp.GraphComponent(graph_id=gid, creature_ids=frozenset(members))


def test_creature_scoped_follows_its_creature():
    rec = make_record(scope_type="creature", scope_id="worker")
    parts = [comp("g_a", "worker", "x"), comp("g_b", "y")]
    placement = dp.select_split_placement(rec, None, parts)
    assert placement.kind == "follow"
    assert placement.graph_id == "g_a"


def test_creature_scoped_orphans_when_creature_absent():
    rec = make_record(scope_type="creature", scope_id="ghost")
    parts = [comp("g_a", "worker"), comp("g_b", "y")]
    assert dp.select_split_placement(rec, None, parts).kind == "orphan"


def test_graph_assigned_follows_assignee():
    rec = make_record(scope_type="graph")
    parts = [comp("g_a", "x"), comp("g_b", "worker")]
    placement = dp.select_split_placement(rec, make_assignment(), parts)
    assert placement.kind == "follow"
    assert placement.graph_id == "g_b"


def test_graph_unassigned_anchor_policy():
    rec = make_record(scope_type="graph")
    unassigned = make_assignment(
        assignment_state="unassigned", assignee_creature_id=None
    )
    parts = [comp("g_a", "x"), comp("g_b", "y", "z")]
    placement = dp.select_split_placement(
        rec, unassigned, parts, split_policy="anchor:y"
    )
    assert placement.graph_id == "g_b"
    assert (
        dp.select_split_placement(
            rec, unassigned, parts, split_policy="anchor:missing"
        ).kind
        == "orphan"
    )


def test_graph_unassigned_largest_component_with_graph_id_tiebreak():
    rec = make_record(scope_type="graph")
    unassigned = make_assignment(
        assignment_state="unassigned", assignee_creature_id=None
    )
    # two components of equal size => lowest graph_id wins deterministically
    parts = [comp("g_z", "a", "b"), comp("g_a", "c", "d")]
    placement = dp.select_split_placement(
        rec, unassigned, parts, split_policy="largest_component"
    )
    assert placement.graph_id == "g_a"
    # a strictly larger component wins regardless of id
    parts2 = [comp("g_z", "a", "b", "c"), comp("g_a", "d")]
    assert (
        dp.select_split_placement(
            rec, unassigned, parts2, split_policy="largest_component"
        ).graph_id
        == "g_z"
    )


def test_graph_unassigned_orphan_and_clone_and_policy_from_options():
    rec = make_record(scope_type="graph")
    unassigned = make_assignment(
        assignment_state="unassigned", assignee_creature_id=None
    )
    parts = [comp("g_a", "x"), comp("g_b", "y")]
    assert (
        dp.select_split_placement(rec, unassigned, parts, split_policy="orphan").kind
        == "orphan"
    )
    assert (
        dp.select_split_placement(rec, unassigned, parts, split_policy="clone").kind
        == "clone"
    )
    # policy taken from record.policy_options when not passed explicitly
    rec2 = make_record(scope_type="graph", policy_options={"split_policy": "orphan"})
    assert dp.select_split_placement(rec2, unassigned, parts).kind == "orphan"
    with pytest.raises(DriveValidationError):
        dp.select_split_placement(rec, unassigned, parts, split_policy="bogus")
    # degenerate split with no child components => orphan, never a crash
    assert (
        dp.select_split_placement(
            rec, unassigned, [], split_policy="largest_component"
        ).kind
        == "orphan"
    )


# ---------------------------------------------------------------------------
# Retry classification (design §5.6)
# ---------------------------------------------------------------------------


def test_retry_classification_by_kind():
    F = dp.DeliveryFailureKind
    R = dp.RetryDisposition
    assert (
        dp.classify_delivery_failure(F.UNAVAILABLE_ASSIGNEE, attempt=0, max_attempts=5)
        is R.DEFER
    )
    assert (
        dp.classify_delivery_failure(F.INVALID_OR_STALE, attempt=0, max_attempts=5)
        is R.SUPERSEDE
    )
    assert (
        dp.classify_delivery_failure(F.POLICY, attempt=0, max_attempts=5)
        is R.FAIL_CLOSED
    )


def test_retry_transient_and_turn_error_backoff_then_dead_letter():
    F = dp.DeliveryFailureKind
    R = dp.RetryDisposition
    for kind in (F.TRANSIENT, F.TURN_ERROR):
        assert (
            dp.classify_delivery_failure(kind, attempt=2, max_attempts=5)
            is R.RETRY_BACKOFF
        )
        assert (
            dp.classify_delivery_failure(kind, attempt=5, max_attempts=5)
            is R.DEAD_LETTER
        )


def test_retry_classification_rejects_bad_counts():
    F = dp.DeliveryFailureKind
    with pytest.raises(DriveValidationError):
        dp.classify_delivery_failure(F.TRANSIENT, attempt=-1, max_attempts=5)
    with pytest.raises(DriveValidationError):
        dp.classify_delivery_failure(F.TRANSIENT, attempt=0, max_attempts=0)


# ---------------------------------------------------------------------------
# Capabilities + authorization (design §3.6)
# ---------------------------------------------------------------------------

Cap = dp.DriveCapability
Op = dp.DriveOperation


def test_system_actor_is_omnipotent():
    caps = dp.effective_capabilities(SYSTEM_ACTOR, make_record(), make_assignment())
    assert caps == frozenset(Cap)


def test_owner_creature_can_create_update_and_control_own_drive():
    rec = make_record(owner=OWNER)
    assert dp.is_operation_allowed(OWNER, None, None, Op.CREATE_SELF)
    assert dp.is_operation_allowed(OWNER, rec, None, Op.UPDATE)
    assert dp.is_operation_allowed(
        OWNER, rec, None, Op.TRANSITION, target_status=S.PAUSED
    )
    assert dp.is_operation_allowed(
        OWNER, rec, None, Op.TRANSITION, target_status=S.CANCELLED
    )
    assert dp.is_operation_allowed(OWNER, rec, None, Op.PROPOSE_TERMINAL)
    # owner is not privileged: cannot assign or create graph-owned drives
    assert not dp.is_operation_allowed(OWNER, rec, None, Op.ASSIGN)
    assert not dp.is_operation_allowed(OWNER, None, None, Op.CREATE_GRAPH)


def test_assignee_of_foreign_drive_can_report_and_propose_but_not_own():
    # user-owned Drive assigned to WORKER
    rec = make_record(owner=USER, owner_scope="actor")
    asg = make_assignment(assignee_creature_id="worker")
    assert dp.is_operation_allowed(WORKER, rec, asg, Op.REPORT_PROGRESS)
    assert dp.is_operation_allowed(WORKER, rec, asg, Op.PROPOSE_TERMINAL)
    # assignee may set waiting/blocked but NOT cancel
    assert dp.is_operation_allowed(
        WORKER, rec, asg, Op.TRANSITION, target_status=S.WAITING
    )
    assert not dp.is_operation_allowed(
        WORKER, rec, asg, Op.TRANSITION, target_status=S.CANCELLED
    )
    assert not dp.is_operation_allowed(WORKER, rec, asg, Op.UPDATE)
    assert not dp.is_operation_allowed(WORKER, rec, asg, Op.REASSIGN)
    assert not dp.is_operation_allowed(WORKER, rec, asg, Op.TRANSFER_OWNER)
    assert not dp.is_operation_allowed(WORKER, rec, asg, Op.RETIRE)


def test_privileged_creature_gets_graph_admin_rights():
    rec = make_record(owner=USER)
    priv = ActorRef("creature", "root")
    assert dp.is_operation_allowed(
        priv, None, None, Op.CREATE_GRAPH, is_privileged=True
    )
    assert dp.is_operation_allowed(priv, rec, None, Op.ASSIGN, is_privileged=True)
    assert dp.is_operation_allowed(
        priv, rec, None, Op.TRANSFER_OWNER, is_privileged=True
    )
    assert dp.is_operation_allowed(priv, rec, None, Op.RETIRE, is_privileged=True)


def test_require_operation_raises_on_denial():
    rec = make_record(owner=USER)
    asg = make_assignment(assignee_creature_id="worker")
    with pytest.raises(DrivePermissionError):
        dp.require_operation(WORKER, rec, asg, Op.TRANSITION, target_status=S.CANCELLED)
    # allowed op does not raise
    dp.require_operation(WORKER, rec, asg, Op.REPORT_PROGRESS)


def test_non_creature_actor_lacks_create_self():
    caps = dp.effective_capabilities(USER, None, None)
    assert Cap.CREATE_SELF not in caps
    assert Cap.READ in caps
