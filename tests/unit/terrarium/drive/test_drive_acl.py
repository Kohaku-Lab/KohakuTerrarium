"""Unit tests for :mod:`kohakuterrarium.terrarium.drive.acl`.

Behavior asserts on the §3.6 owner/assignee/privileged matrix and the §8.6/§8.8
fail-closed pipeline: owner can update/transition/propose but NOT transfer;
foreign assignee can report/propose but NOT cancel/reassign/retire/update;
non-privileged cannot create graph-scoped; privileged graph rights depend on the
privilege flag; a disabled/incompatible registration fails kind-semantic
operations closed while admin transitions stay open. Authorization is decided by
capability, never by whether a tool happens to be registered.
"""

from datetime import datetime, timezone

import pytest

from kohakuterrarium.terrarium.drive.acl import (
    DriveCapability,
    DriveOperation,
    allowed_actions,
    authorize,
    capabilities_for,
)
from kohakuterrarium.terrarium.drive.errors import (
    DrivePermissionError,
    DriveRegistrationDisabledError,
    DriveRegistrationIncompatibleError,
)
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveAssignment,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.registration import GenericDriveRegistration
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot

_NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)
_SNAP = EnabledRegistrySnapshot.build([GenericDriveRegistration()])
_EMPTY = EnabledRegistrySnapshot.build([])

_OWNER = ActorRef("creature", "owner1")
_USER = ActorRef("user", "alice")
_ASSIGNEE = ActorRef("creature", "worker1")


def _record(
    owner, *, kind="generic", schema_version=1, scope_type="creature", scope_id="owner1"
):
    return DriveRecord(
        drive_id="d1",
        kind=kind,
        schema_version=schema_version,
        revision=1,
        title="t",
        spec={},
        presentation={},
        metadata={},
        scope_type=scope_type,
        scope_id=scope_id,
        origin_scope_id=scope_id,
        status=DriveStatus.ACTIVE,
        status_reason=None,
        priority=0,
        policy_name="generic",
        created_by=owner,
        owner=owner,
        owner_scope="creature" if owner.kind == "creature" else "actor",
        created_at=_NOW,
        updated_by=owner,
        updated_at=_NOW,
        lifecycle_epoch=0,
    )


def _assignment(creature_id="worker1"):
    return DriveAssignment(
        drive_id="d1",
        assignment_id="a1",
        revision=1,
        lifecycle_epoch=0,
        assignee_graph_id="g1",
        assignment_state="assigned",
        updated_at=_NOW,
        assignee_creature_id=creature_id,
    )


def _ok(op, actor, record, assignment, snap, **kw):
    authorize(op, actor, record, assignment, snap, **kw)


def _denied(exc, op, actor, record, assignment, snap, **kw):
    with pytest.raises(exc):
        authorize(op, actor, record, assignment, snap, **kw)


class TestOwner:
    def test_owner_can_update_transition_propose(self):
        rec = _record(_OWNER)
        asg = _assignment("owner1")
        _ok(DriveOperation.UPDATE, _OWNER, rec, asg, _SNAP)
        _ok(
            DriveOperation.TRANSITION,
            _OWNER,
            rec,
            asg,
            _SNAP,
            target_status=DriveStatus.PAUSED,
        )
        _ok(DriveOperation.PROPOSE_TERMINAL, _OWNER, rec, asg, _SNAP)
        _ok(DriveOperation.REPORT_PROGRESS, _OWNER, rec, asg, _SNAP)

    def test_owner_cannot_transfer_owner(self):
        rec = _record(_OWNER)
        _denied(
            DrivePermissionError,
            DriveOperation.TRANSFER_OWNER,
            _OWNER,
            rec,
            None,
            _SNAP,
        )

    def test_owner_cannot_assign_or_admin(self):
        rec = _record(_OWNER)
        _denied(DrivePermissionError, DriveOperation.ASSIGN, _OWNER, rec, None, _SNAP)
        _denied(DrivePermissionError, DriveOperation.RETIRE, _OWNER, rec, None, _SNAP)

    def test_owner_capabilities_exclude_transfer_owner(self):
        caps = capabilities_for(_OWNER, _record(_OWNER), None)
        assert DriveCapability.UPDATE_OWNED in caps
        assert DriveCapability.TRANSITION in caps
        assert DriveCapability.PROPOSE_TERMINAL in caps
        assert DriveCapability.TRANSFER_OWNER not in caps


class TestForeignAssignee:
    """Record owned by a user, assigned to worker1."""

    def _rec(self):
        return _record(_USER, scope_type="graph", scope_id="g1")

    def test_assignee_can_report_and_propose(self):
        rec, asg = self._rec(), _assignment()
        _ok(DriveOperation.REPORT_PROGRESS, _ASSIGNEE, rec, asg, _SNAP)
        _ok(DriveOperation.PROPOSE_TERMINAL, _ASSIGNEE, rec, asg, _SNAP)

    def test_assignee_may_set_waiting_or_blocked(self):
        rec, asg = self._rec(), _assignment()
        _ok(
            DriveOperation.TRANSITION,
            _ASSIGNEE,
            rec,
            asg,
            _SNAP,
            target_status=DriveStatus.WAITING,
        )
        _ok(
            DriveOperation.TRANSITION,
            _ASSIGNEE,
            rec,
            asg,
            _SNAP,
            target_status=DriveStatus.BLOCKED,
        )

    def test_assignee_cannot_reactivate_update_cancel_reassign_retire(self):
        rec, asg = self._rec(), _assignment()
        # ACTIVE is not an assignee-permitted transition target.
        _denied(
            DrivePermissionError,
            DriveOperation.TRANSITION,
            _ASSIGNEE,
            rec,
            asg,
            _SNAP,
            target_status=DriveStatus.ACTIVE,
        )
        _denied(
            DrivePermissionError,
            DriveOperation.TRANSITION,
            _ASSIGNEE,
            rec,
            asg,
            _SNAP,
            target_status=DriveStatus.CANCELLED,
        )
        _denied(DrivePermissionError, DriveOperation.UPDATE, _ASSIGNEE, rec, asg, _SNAP)
        _denied(
            DrivePermissionError, DriveOperation.REASSIGN, _ASSIGNEE, rec, asg, _SNAP
        )
        _denied(DrivePermissionError, DriveOperation.RETIRE, _ASSIGNEE, rec, asg, _SNAP)

    def test_assignee_capabilities(self):
        caps = capabilities_for(_ASSIGNEE, self._rec(), _assignment())
        assert DriveCapability.MANAGE_ASSIGNED in caps
        assert DriveCapability.PROPOSE_TERMINAL in caps
        assert DriveCapability.UPDATE_OWNED not in caps
        assert DriveCapability.ASSIGN not in caps


class TestPrivilegeAndCreation:
    def test_non_privileged_creature_can_create_self(self):
        _ok(DriveOperation.CREATE_SELF, _OWNER, None, None, _SNAP, kind="generic")

    def test_non_privileged_creature_cannot_create_graph(self):
        _denied(
            DrivePermissionError,
            DriveOperation.CREATE_GRAPH,
            _OWNER,
            None,
            None,
            _SNAP,
            kind="generic",
        )

    def test_privileged_creature_gets_graph_rights(self):
        rec, asg = _record(_USER, scope_type="graph", scope_id="g1"), _assignment()
        _ok(
            DriveOperation.CREATE_GRAPH,
            _OWNER,
            None,
            None,
            _SNAP,
            is_privileged=True,
            kind="generic",
        )
        _ok(DriveOperation.ASSIGN, _OWNER, rec, asg, _SNAP, is_privileged=True)
        _ok(DriveOperation.TRANSFER_OWNER, _OWNER, rec, asg, _SNAP, is_privileged=True)
        _ok(DriveOperation.RETIRE, _OWNER, rec, asg, _SNAP, is_privileged=True)

    def test_privilege_is_gated_by_the_flag(self):
        # Same actor, without is_privileged, has no graph-scoped create right; this
        # is how "privileged rights bounded to their graph" is enforced — the flag
        # is only set within the creature's own graph.
        _denied(
            DrivePermissionError,
            DriveOperation.CREATE_GRAPH,
            _OWNER,
            None,
            None,
            _SNAP,
            kind="generic",
        )

    def test_extra_grants_widen_for_operators(self):
        rec = _record(_USER, scope_type="graph", scope_id="g1")
        grants = frozenset({DriveCapability.ADMIN})
        _ok(DriveOperation.RETIRE, _USER, rec, None, _SNAP, extra_grants=grants)
        # Without the explicit grant, the same operator is denied.
        _denied(DrivePermissionError, DriveOperation.RETIRE, _USER, rec, None, _SNAP)


class TestRegistrationFailClosed:
    """§8.6: kind-semantic operations fail closed when the registration is not
    available, but generic admin transitions / reads stay open."""

    def test_semantic_ops_fail_closed_when_disabled(self):
        rec, asg = _record(_OWNER), _assignment("owner1")
        _denied(
            DriveRegistrationDisabledError,
            DriveOperation.UPDATE,
            _OWNER,
            rec,
            asg,
            _EMPTY,
        )
        _denied(
            DriveRegistrationDisabledError,
            DriveOperation.PROPOSE_TERMINAL,
            _OWNER,
            rec,
            asg,
            _EMPTY,
        )

    def test_create_fails_closed_when_disabled(self):
        _denied(
            DriveRegistrationDisabledError,
            DriveOperation.CREATE_SELF,
            _OWNER,
            None,
            None,
            _EMPTY,
            kind="generic",
        )

    def test_admin_transitions_allowed_without_registration(self):
        rec, asg = _record(_OWNER), _assignment("owner1")
        _ok(
            DriveOperation.TRANSITION,
            _OWNER,
            rec,
            asg,
            _EMPTY,
            target_status=DriveStatus.PAUSED,
        )
        _ok(
            DriveOperation.TRANSITION,
            _OWNER,
            rec,
            asg,
            _EMPTY,
            target_status=DriveStatus.CANCELLED,
        )
        _ok(DriveOperation.READ, _OWNER, rec, asg, _EMPTY)
        _ok(DriveOperation.RETIRE, _OWNER, rec, asg, _EMPTY, is_privileged=True)

    def test_reactivating_transition_fails_closed_without_registration(self):
        rec, asg = _record(_OWNER), _assignment("owner1")
        _denied(
            DriveRegistrationDisabledError,
            DriveOperation.TRANSITION,
            _OWNER,
            rec,
            asg,
            _EMPTY,
            target_status=DriveStatus.ACTIVE,
        )

    def test_incompatible_schema_raises_incompatible(self):
        rec = _record(_OWNER, schema_version=2)  # snapshot serves generic v1 only
        _denied(
            DriveRegistrationIncompatibleError,
            DriveOperation.UPDATE,
            _OWNER,
            rec,
            _assignment("owner1"),
            _SNAP,
        )


class TestAllowedActions:
    def test_owner_action_list(self):
        actions = allowed_actions(_OWNER, _record(_OWNER), _assignment("owner1"), _SNAP)
        assert "update" in actions
        assert "transition" in actions
        assert "propose_terminal" in actions
        assert "read" in actions
        assert "transfer_owner" not in actions
        assert "assign" not in actions

    def test_unavailable_registration_drops_semantic_actions_keeps_admin(self):
        actions = allowed_actions(
            _OWNER, _record(_OWNER), _assignment("owner1"), _EMPTY
        )
        assert "update" not in actions  # kind-semantic, dropped
        assert "propose_terminal" not in actions
        assert "transition" in actions  # admin pause/cancel stays offered
        assert "read" in actions

    def test_privileged_action_list_includes_admin(self):
        rec = _record(_USER, scope_type="graph", scope_id="g1")
        actions = allowed_actions(_OWNER, rec, _assignment(), _SNAP, is_privileged=True)
        assert "assign" in actions
        assert "transfer_owner" in actions
        assert "retire" in actions
        assert "admin" in actions

    def test_foreign_assignee_action_list(self):
        rec = _record(_USER, scope_type="graph", scope_id="g1")
        actions = allowed_actions(_ASSIGNEE, rec, _assignment(), _SNAP)
        assert "report_progress" in actions
        assert "propose_terminal" in actions
        assert "transition" in actions  # waiting/blocked only, but the control shows
        assert "update" not in actions
        assert "assign" not in actions
