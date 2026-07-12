"""Unit tests for :mod:`kohakuterrarium.terrarium.drive.models`."""

import dataclasses
from datetime import datetime, timezone

import pytest

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveAssignment,
    DriveAuditRecord,
    DriveAvailability,
    DriveDelivery,
    DriveProgress,
    DriveRecord,
    DriveStatus,
    SYSTEM_ACTOR,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 7, 11, 12, 0)
C1 = ActorRef("creature", "c1")


def record_kwargs(**overrides):
    base = dict(
        drive_id="d1",
        kind="generic",
        schema_version=1,
        revision=0,
        title="Watch the deployment",
        spec={},
        presentation={},
        metadata={},
        scope_type="graph",
        scope_id="g1",
        origin_scope_id="g1",
        status=DriveStatus.ACTIVE,
        status_reason=None,
        priority=0,
        policy_name="generic",
        created_by=C1,
        owner=C1,
        owner_scope="creature",
        created_at=NOW,
        updated_by=C1,
        updated_at=NOW,
        lifecycle_epoch=0,
    )
    base.update(overrides)
    return base


def make_record(**overrides):
    return DriveRecord(**record_kwargs(**overrides))


def assignment_kwargs(**overrides):
    base = dict(
        drive_id="d1",
        assignment_id="a1",
        revision=0,
        lifecycle_epoch=0,
        assignee_graph_id="g1",
        assignment_state="assigned",
        updated_at=NOW,
        assignee_creature_id="c1",
    )
    base.update(overrides)
    return base


def delivery_kwargs(**overrides):
    base = dict(
        delivery_id="del1",
        drive_id="d1",
        drive_revision=0,
        lifecycle_epoch=0,
        assignment_id="a1",
        assignee_creature_id="c1",
        reason="ready",
        state="pending",
        attempt=0,
        available_at=NOW,
        created_at=NOW,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# enums
# ---------------------------------------------------------------------------


def test_drive_status_values():
    assert [s.value for s in DriveStatus] == [
        "draft",
        "active",
        "waiting",
        "blocked",
        "paused",
        "completed",
        "failed",
        "cancelled",
        "retired",
    ]
    # str-enum: value comparison works for wire round trips
    assert DriveStatus.ACTIVE == "active"


def test_drive_availability_values():
    assert DriveAvailability.AVAILABLE.value == "available"
    assert {a.value for a in DriveAvailability} == {
        "available",
        "registration_disabled",
        "registration_unavailable",
        "registration_incompatible",
    }


# ---------------------------------------------------------------------------
# ActorRef
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,kind,identity",
    [
        ("user:alice", "user", "alice"),
        ("service:deploy-bot", "service", "deploy-bot"),
        ("creature:c1", "creature", "c1"),
        ("plugin:kt-biome/goal", "plugin", "kt-biome/goal"),
        ("system:terrarium", "system", "terrarium"),
        ("user:has:colons", "user", "has:colons"),
    ],
)
def test_actor_parse_and_format_round_trip(text, kind, identity):
    actor = ActorRef.parse(text)
    assert actor.kind == kind
    assert actor.identity == identity
    assert actor.format() == text


def test_system_actor_constant():
    assert SYSTEM_ACTOR == ActorRef("system", "terrarium")
    assert SYSTEM_ACTOR.format() == "system:terrarium"


@pytest.mark.parametrize("bad", ["noseparator", ":alice", "user:", "unknown:x", ""])
def test_actor_parse_rejects_malformed(bad):
    with pytest.raises(DriveValidationError):
        ActorRef.parse(bad)


def test_actor_rejects_bad_kind_and_empty_identity():
    with pytest.raises(DriveValidationError):
        ActorRef("robot", "x")
    with pytest.raises(DriveValidationError):
        ActorRef("user", "")


def test_actor_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        C1.kind = "user"


# ---------------------------------------------------------------------------
# DriveRecord
# ---------------------------------------------------------------------------


def test_drive_record_valid_construction():
    r = make_record(dependency_ids=("dep1", "dep2"))
    assert r.drive_id == "d1"
    assert r.status is DriveStatus.ACTIVE
    assert r.dependency_ids == ("dep1", "dep2")


def test_drive_record_is_frozen():
    r = make_record()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.revision = 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"drive_id": ""},
        {"kind": ""},
        {"schema_version": 0},
        {"schema_version": True},
        {"revision": -1},
        {"lifecycle_epoch": -1},
        {"priority": "high"},
        {"title": ""},
        {"scope_type": "process"},
        {"scope_id": ""},
        {"origin_scope_id": ""},
        {"status": "active"},
        {"owner_scope": "root"},
        {"created_by": "creature:c1"},
        {"created_at": NAIVE},
        {"created_at": None},
        {"created_at": "2026-07-11T12:00:00+00:00"},
        {"not_before": NAIVE},
        {"expires_at": NAIVE},
        {"dependency_ids": ["dep1"]},
        {"dependency_ids": ("",)},
    ],
)
def test_drive_record_rejects_invalid(overrides):
    with pytest.raises(DriveValidationError):
        make_record(**overrides)


# ---------------------------------------------------------------------------
# DriveAssignment
# ---------------------------------------------------------------------------


def test_drive_assignment_valid():
    a = DriveAssignment(**assignment_kwargs())
    assert a.assignment_state == "assigned"
    assert a.assignee_creature_id == "c1"


@pytest.mark.parametrize(
    "overrides",
    [
        {"drive_id": ""},
        {"assignment_id": ""},
        {"revision": -1},
        {"assignee_graph_id": ""},
        {"assignment_state": "detached"},
        {"updated_at": NAIVE},
        {"assignee_creature_id": ""},
        {"lease_owner": ""},
        {"lease_expires_at": NAIVE},
        {"assigned_by": "creature:c1"},
    ],
)
def test_drive_assignment_rejects_invalid(overrides):
    with pytest.raises(DriveValidationError):
        DriveAssignment(**assignment_kwargs(**overrides))


def test_drive_assignment_allows_unassigned_without_creature():
    a = DriveAssignment(
        **assignment_kwargs(assignment_state="unassigned", assignee_creature_id=None)
    )
    assert a.assignee_creature_id is None


# ---------------------------------------------------------------------------
# DriveDelivery
# ---------------------------------------------------------------------------


def test_drive_delivery_valid_and_logical_key():
    d = DriveDelivery(**delivery_kwargs(drive_revision=3, readiness_generation=2))
    assert d.logical_key() == ("d1", 0, 3, "a1", 2)


@pytest.mark.parametrize(
    "overrides",
    [
        {"delivery_id": ""},
        {"drive_revision": -1},
        {"assignee_creature_id": ""},
        {"reason": "woke_up"},
        {"state": "done"},
        {"attempt": -1},
        {"readiness_generation": -1},
        {"available_at": NAIVE},
        {"created_at": None},
        {"claimed_at": NAIVE},
    ],
)
def test_drive_delivery_rejects_invalid(overrides):
    with pytest.raises(DriveValidationError):
        DriveDelivery(**delivery_kwargs(**overrides))


def test_drive_delivery_attempt_zero_is_valid():
    d = DriveDelivery(**delivery_kwargs(attempt=0))
    assert d.attempt == 0


# ---------------------------------------------------------------------------
# DriveProgress + DriveAuditRecord
# ---------------------------------------------------------------------------


def test_drive_progress_valid_and_invalid():
    p = DriveProgress(
        progress_id="p1", drive_id="d1", actor=C1, summary="halfway", created_at=NOW
    )
    assert p.evidence == {}
    with pytest.raises(DriveValidationError):
        DriveProgress(
            progress_id="", drive_id="d1", actor=C1, summary="x", created_at=NOW
        )
    with pytest.raises(DriveValidationError):
        DriveProgress(
            progress_id="p1",
            drive_id="d1",
            actor="creature:c1",
            summary="x",
            created_at=NOW,
        )
    with pytest.raises(DriveValidationError):
        DriveProgress(
            progress_id="p1", drive_id="d1", actor=C1, summary=123, created_at=NOW
        )


def test_drive_audit_record_valid_and_invalid():
    a = DriveAuditRecord(
        audit_id="au1",
        drive_id="d1",
        revision=1,
        lifecycle_epoch=0,
        actor=C1,
        operation="update",
        created_at=NOW,
        before_status=DriveStatus.DRAFT,
        after_status=DriveStatus.ACTIVE,
    )
    assert a.before_status is DriveStatus.DRAFT
    with pytest.raises(DriveValidationError):
        DriveAuditRecord(
            audit_id="au1",
            drive_id="d1",
            revision=1,
            lifecycle_epoch=0,
            actor=C1,
            operation="",
            created_at=NOW,
        )
    with pytest.raises(DriveValidationError):
        DriveAuditRecord(
            audit_id="au1",
            drive_id="d1",
            revision=1,
            lifecycle_epoch=0,
            actor=C1,
            operation="update",
            created_at=NOW,
            after_status="active",
        )
