"""Unit tests for :mod:`kohakuterrarium.terrarium.drive.requests`."""

from datetime import datetime, timezone

import pytest

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.requests import (
    UNSET,
    CreateDriveRequest,
    DrivePatch,
    DriveQuery,
    DriveTransitionProposal,
    _Unset,
)

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 7, 11, 12, 0)
USER = ActorRef("user", "alice")
C1 = ActorRef("creature", "c1")


def create_kwargs(**overrides):
    base = dict(
        kind="generic",
        title="Objective",
        scope_type="creature",
        scope_id="c1",
        owner=C1,
        owner_scope="creature",
        created_by=C1,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# CreateDriveRequest
# ---------------------------------------------------------------------------


def test_create_request_defaults():
    r = CreateDriveRequest(**create_kwargs())
    assert r.priority == 0
    assert r.dependency_ids == ()
    assert r.policy_name == "generic"
    assert r.schema_version == 1
    assert r.spec == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": ""},
        {"title": ""},
        {"scope_type": "process"},
        {"scope_id": ""},
        {"owner": "creature:c1"},
        {"owner_scope": "root"},
        {"created_by": "creature:c1"},
        {"schema_version": 0},
        {"priority": "hi"},
        {"not_before": NAIVE},
        {"expires_at": NAIVE},
        {"assignee_creature_id": ""},
        {"policy_name": ""},
        {"dependency_ids": ["d"]},
        {"dependency_ids": ("",)},
        {"idempotency_key": ""},
    ],
)
def test_create_request_rejects_invalid(overrides):
    with pytest.raises(DriveValidationError):
        CreateDriveRequest(**create_kwargs(**overrides))


# ---------------------------------------------------------------------------
# DrivePatch
# ---------------------------------------------------------------------------


def test_empty_patch_has_no_changes():
    p = DrivePatch()
    assert p.is_empty()
    assert p.changes() == {}


def test_patch_reports_only_set_fields():
    p = DrivePatch(title="new", priority=5)
    assert p.changes() == {"title": "new", "priority": 5}
    assert not p.is_empty()


def test_patch_clearing_a_field_is_a_change_distinct_from_unset():
    p = DrivePatch(not_before=None)
    assert p.changes() == {"not_before": None}
    assert p.not_before is None
    # a field the caller never touched stays UNSET, not None
    assert p.expires_at is UNSET


def test_unset_sentinel_is_a_falsey_singleton():
    assert _Unset() is UNSET
    assert bool(UNSET) is False
    assert repr(UNSET) == "UNSET"


def test_patch_has_no_identity_fields():
    # A patch structurally cannot carry canonical identity, so it can never
    # overwrite drive_id / revision / owner / scope / status.
    p = DrivePatch()
    for forbidden in ("drive_id", "revision", "owner", "scope_id", "status", "kind"):
        assert not hasattr(p, forbidden)


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": ""},
        {"priority": "hi"},
        {"not_before": NAIVE},
        {"dependency_ids": ["d"]},
        {"dependency_ids": ("",)},
    ],
)
def test_patch_rejects_invalid_set_values(overrides):
    with pytest.raises(DriveValidationError):
        DrivePatch(**overrides)


# ---------------------------------------------------------------------------
# DriveQuery
# ---------------------------------------------------------------------------


def test_query_valid():
    q = DriveQuery(
        graph_id="g1",
        statuses=frozenset({DriveStatus.ACTIVE, DriveStatus.WAITING}),
        kinds=frozenset({"generic"}),
        owner=USER,
        limit=10,
    )
    assert q.include_terminal is True
    assert DriveStatus.ACTIVE in q.statuses


@pytest.mark.parametrize(
    "kwargs",
    [
        {"graph_id": ""},
        {"scope_type": "process"},
        {"scope_id": ""},
        {"statuses": frozenset({"active"})},
        {"kinds": frozenset({""})},
        {"assignee_creature_id": ""},
        {"owner": "user:alice"},
        {"limit": 0},
    ],
)
def test_query_rejects_invalid(kwargs):
    with pytest.raises(DriveValidationError):
        DriveQuery(**kwargs)


# ---------------------------------------------------------------------------
# DriveTransitionProposal
# ---------------------------------------------------------------------------


def test_transition_proposal_valid():
    p = DriveTransitionProposal(
        proposal_id="pr1",
        drive_id="d1",
        target_status=DriveStatus.COMPLETED,
        proposed_by=C1,
        created_at=NOW,
        evidence={"log": "ok"},
        expected_revision=4,
    )
    assert p.target_status is DriveStatus.COMPLETED
    assert p.evidence == {"log": "ok"}


def proposal_kwargs(**overrides):
    base = dict(
        proposal_id="pr1",
        drive_id="d1",
        target_status=DriveStatus.COMPLETED,
        proposed_by=C1,
        created_at=NOW,
    )
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "overrides",
    [
        {"proposal_id": ""},
        {"drive_id": ""},
        {"target_status": "completed"},
        {"proposed_by": "creature:c1"},
        {"created_at": NAIVE},
        {"expected_revision": -1},
    ],
)
def test_transition_proposal_rejects_invalid(overrides):
    with pytest.raises(DriveValidationError):
        DriveTransitionProposal(**proposal_kwargs(**overrides))
