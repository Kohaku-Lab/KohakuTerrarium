"""Unit tests for :mod:`kohakuterrarium.terrarium.drive.wire`."""

from datetime import datetime, timedelta, timezone

import pytest

from kohakuterrarium.terrarium.drive import wire
from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveAssignment,
    DriveAuditRecord,
    DriveDelivery,
    DriveProgress,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.requests import (
    UNSET,
    CreateDriveRequest,
    DrivePatch,
    DriveQuery,
    DriveTransitionProposal,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=2)
C1 = ActorRef("creature", "c1")
USER = ActorRef("user", "alice")


def make_record(**overrides):
    base = dict(
        drive_id="d1",
        kind="generic",
        schema_version=1,
        revision=3,
        title="t",
        spec={"instruction": "watch"},
        presentation={"label": "Deploy"},
        metadata={"parent_drive_id": "d0"},
        scope_type="graph",
        scope_id="g1",
        origin_scope_id="g0",
        status=DriveStatus.WAITING,
        status_reason="awaiting dep",
        priority=7,
        policy_name="generic",
        created_by=C1,
        owner=USER,
        owner_scope="actor",
        created_at=NOW,
        updated_by=C1,
        updated_at=LATER,
        lifecycle_epoch=2,
        terminal_evidence={"hash": "abc"},
        not_before=NOW,
        expires_at=LATER,
        dependency_ids=("dep1", "dep2"),
        policy_options={"split_policy": "orphan"},
    )
    base.update(overrides)
    return DriveRecord(**base)


def make_assignment():
    return DriveAssignment(
        drive_id="d1",
        assignment_id="a1",
        revision=3,
        lifecycle_epoch=2,
        assignee_graph_id="g1",
        assignment_state="assigned",
        updated_at=NOW,
        assignee_creature_id="worker",
        lease_owner="node-1",
        lease_expires_at=LATER,
        assigned_by=C1,
        assigned_at=NOW,
    )


def make_delivery():
    return DriveDelivery(
        delivery_id="del1",
        drive_id="d1",
        drive_revision=3,
        lifecycle_epoch=2,
        assignment_id="a1",
        assignee_creature_id="worker",
        reason="ready",
        state="admitted",
        attempt=1,
        available_at=NOW,
        created_at=NOW,
        readiness_generation=4,
        claimed_at=NOW,
        admitted_at=LATER,
        last_error=None,
        ack_reason="user_interrupted",
    )


# ---------------------------------------------------------------------------
# round trips (generic pack/unpack dispatch)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obj",
    [
        make_record(),
        make_assignment(),
        make_delivery(),
        DriveProgress(
            progress_id="p1",
            drive_id="d1",
            actor=C1,
            summary="halfway",
            created_at=NOW,
            evidence={"k": "v"},
        ),
        DriveAuditRecord(
            audit_id="au1",
            drive_id="d1",
            revision=4,
            lifecycle_epoch=2,
            actor=C1,
            operation="transition",
            created_at=NOW,
            before_status=DriveStatus.ACTIVE,
            after_status=DriveStatus.COMPLETED,
            summary="done",
            details={"note": "ok"},
        ),
        CreateDriveRequest(
            kind="generic",
            title="obj",
            scope_type="creature",
            scope_id="c1",
            owner=C1,
            owner_scope="creature",
            created_by=C1,
            not_before=NOW,
            dependency_ids=("x",),
            idempotency_key="req-1",
        ),
        DriveTransitionProposal(
            proposal_id="pr1",
            drive_id="d1",
            target_status=DriveStatus.COMPLETED,
            proposed_by=C1,
            created_at=NOW,
            reason="finished",
            evidence={"log": "ok"},
            expected_revision=5,
        ),
    ],
)
def test_generic_round_trip(obj):
    payload = wire.pack(obj)
    assert payload["wire_schema"] == wire.WIRE_SCHEMA_VERSION
    restored = wire.unpack(payload)
    assert restored == obj
    assert type(restored) is type(obj)


def test_record_round_trip_preserves_actor_and_datetimes():
    rec = make_record()
    back = wire.unpack_drive_record(wire.pack_drive_record(rec))
    assert back.owner == USER
    assert back.created_by == C1
    assert back.created_at == NOW
    assert back.not_before == NOW
    assert back.dependency_ids == ("dep1", "dep2")
    assert back.status is DriveStatus.WAITING


def test_query_round_trip_with_statuses_kinds_owner():
    q = DriveQuery(
        graph_id="g1",
        statuses=frozenset({DriveStatus.ACTIVE, DriveStatus.WAITING}),
        kinds=frozenset({"generic", "goal"}),
        owner=USER,
        limit=25,
    )
    back = wire.unpack(wire.pack(q))
    assert back.statuses == q.statuses
    assert back.kinds == q.kinds
    assert back.owner == USER
    assert back.limit == 25


def test_query_round_trip_with_none_fields():
    q = DriveQuery()
    back = wire.unpack(wire.pack(q))
    assert back.statuses is None
    assert back.owner is None
    assert back.include_terminal is True


# ---------------------------------------------------------------------------
# DrivePatch: only set fields travel; clearing survives; empty stays empty
# ---------------------------------------------------------------------------


def test_patch_partial_round_trip():
    patch = DrivePatch(
        title="new", priority=3, not_before=None, dependency_ids=("a", "b")
    )
    back = wire.unpack(wire.pack(patch))
    assert back.changes() == {
        "title": "new",
        "priority": 3,
        "not_before": None,
        "dependency_ids": ("a", "b"),
    }
    assert back.expires_at is UNSET


def test_patch_with_datetime_round_trips():
    patch = DrivePatch(not_before=NOW, expires_at=LATER)
    back = wire.unpack(wire.pack(patch))
    assert back.not_before == NOW
    assert back.expires_at == LATER


def test_empty_patch_round_trips_empty():
    back = wire.unpack(wire.pack(DrivePatch()))
    assert back.is_empty()


# ---------------------------------------------------------------------------
# version / type / malformed handling
# ---------------------------------------------------------------------------


def test_unknown_wire_version_rejected():
    payload = wire.pack(make_record())
    payload["wire_schema"] = 999
    with pytest.raises(DriveValidationError):
        wire.unpack(payload)
    with pytest.raises(DriveValidationError):
        wire.unpack_drive_record(payload)


def test_unknown_wire_type_rejected():
    payload = wire.pack(make_record())
    payload["wire_type"] = "drive_unicorn"
    with pytest.raises(DriveValidationError):
        wire.unpack(payload)


def test_specific_unpacker_rejects_wrong_type():
    payload = wire.pack(make_assignment())
    with pytest.raises(DriveValidationError):
        wire.unpack_drive_record(payload)


@pytest.mark.parametrize("payload", [None, 42, "x", [], ("a",)])
def test_non_dict_payload_rejected(payload):
    with pytest.raises(DriveValidationError):
        wire.unpack(payload)


def test_missing_data_object_rejected():
    with pytest.raises(DriveValidationError):
        wire.unpack_drive_record(
            {"wire_schema": wire.WIRE_SCHEMA_VERSION, "wire_type": "drive_record"}
        )


def test_missing_required_identity_field_fails_closed():
    payload = wire.pack(make_record())
    payload["data"].pop("drive_id")
    with pytest.raises(DriveValidationError):
        wire.unpack(payload)


def test_bad_datetime_and_status_fail_closed():
    payload = wire.pack(make_record())
    payload["data"]["created_at"] = "not-a-date"
    with pytest.raises(DriveValidationError):
        wire.unpack(payload)
    payload2 = wire.pack(make_record())
    payload2["data"]["status"] = "sleeping"
    with pytest.raises(DriveValidationError):
        wire.unpack(payload2)


def test_pack_unknown_type_rejected():
    with pytest.raises(DriveValidationError):
        wire.pack(object())


# ---------------------------------------------------------------------------
# unknown / extra fields cannot overwrite framework identity
# ---------------------------------------------------------------------------


def test_extra_unknown_fields_are_ignored_and_identity_preserved():
    rec = make_record(drive_id="canonical", revision=7, owner=USER)
    payload = wire.pack(rec)
    # inject fields the schema does not define, including identity-shaped aliases
    payload["data"]["injected"] = "ignore me"
    payload["data"]["drive_id_shadow"] = "attacker"
    payload["data"]["owner_alias"] = "creature:evil"
    back = wire.unpack(payload)
    assert back.drive_id == "canonical"
    assert back.revision == 7
    assert back.owner == USER
    assert not hasattr(back, "injected")
    assert not hasattr(back, "drive_id_shadow")


def test_datetime_z_suffix_is_accepted():
    payload = wire.pack(make_record())
    payload["data"]["created_at"] = "2026-07-11T12:00:00Z"
    back = wire.unpack(payload)
    assert back.created_at == NOW


# ---------------------------------------------------------------------------
# fail-closed coercion of individual field types
# ---------------------------------------------------------------------------


def test_specific_unpacker_rejects_non_dict_payload():
    with pytest.raises(DriveValidationError):
        wire.unpack_drive_record("not a payload")


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("created_at", 12345),  # datetime field given a non-string
        ("owner", 99),  # actor field given a non-string
        ("spec", 42),  # dict field given a non-dict
        ("dependency_ids", "dep1"),  # sequence field given a bare string
    ],
)
def test_wrong_field_type_fails_closed(field, bad_value):
    payload = wire.pack(make_record())
    payload["data"][field] = bad_value
    with pytest.raises(DriveValidationError):
        wire.unpack(payload)


def test_missing_actor_fails_closed():
    payload = wire.pack(make_record())
    payload["data"]["owner"] = None
    with pytest.raises(DriveValidationError):
        wire.unpack(payload)


def test_omitted_optional_collections_default_to_empty():
    payload = wire.pack(make_record())
    for key in ("dependency_ids", "policy_options", "spec"):
        payload["data"].pop(key)
    back = wire.unpack(payload)
    assert back.dependency_ids == ()
    assert back.policy_options == {}
    assert back.spec == {}


def test_delivery_without_ack_reason_unpacks_as_none():
    # Rows written before ``ack_reason`` existed must still load.
    payload = wire.pack_drive_delivery(make_delivery())
    del payload["data"]["ack_reason"]
    assert wire.unpack_drive_delivery(payload).ack_reason is None
