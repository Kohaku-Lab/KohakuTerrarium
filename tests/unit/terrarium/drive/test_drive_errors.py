"""Unit tests for :mod:`kohakuterrarium.terrarium.drive.errors`."""

import pytest

from kohakuterrarium.errors import (
    ConflictError,
    InvalidRequestError,
    KTError,
    NotFoundError,
)
from kohakuterrarium.terrarium.drive.errors import (
    DriveBackpressureError,
    DriveConflictError,
    DriveDeliveryError,
    DriveError,
    DriveIdempotencyConflictError,
    DriveNotFoundError,
    DrivePermissionError,
    DrivePersistenceRequiredError,
    DriveReconfigurationRequiredError,
    DriveRegistrationDisabledError,
    DriveRegistrationIncompatibleError,
    DriveRegistrationNotFoundError,
    DriveTransitionError,
    DriveValidationError,
)

ALL_DRIVE_ERRORS = [
    DriveError,
    DriveNotFoundError,
    DriveValidationError,
    DriveTransitionError,
    DrivePermissionError,
    DriveConflictError,
    DriveIdempotencyConflictError,
    DriveRegistrationNotFoundError,
    DriveRegistrationDisabledError,
    DriveRegistrationIncompatibleError,
    DriveReconfigurationRequiredError,
    DrivePersistenceRequiredError,
    DriveBackpressureError,
    DriveDeliveryError,
]


@pytest.mark.parametrize("err_cls", ALL_DRIVE_ERRORS)
def test_every_error_is_a_kterror(err_cls):
    assert issubclass(err_cls, KTError)
    assert issubclass(err_cls, DriveError)


def test_not_found_errors_map_to_repo_notfound_and_keyerror():
    assert issubclass(DriveNotFoundError, NotFoundError)
    assert issubclass(DriveNotFoundError, KeyError)
    assert issubclass(DriveRegistrationNotFoundError, NotFoundError)


def test_validation_errors_are_invalid_request_and_valueerror():
    for cls in (DriveValidationError, DriveTransitionError):
        assert issubclass(cls, InvalidRequestError)
        assert issubclass(cls, ValueError)


def test_permission_error_is_builtin_permission_error():
    assert issubclass(DrivePermissionError, PermissionError)
    assert str(DrivePermissionError("nope")) == "nope"


def test_conflict_errors_share_conflict_base_but_are_distinct():
    assert issubclass(DriveConflictError, ConflictError)
    assert issubclass(DriveIdempotencyConflictError, ConflictError)
    # idempotency conflict is a sibling, not a subtype of revision conflict:
    # catching DriveConflictError must NOT swallow an idempotency conflict.
    assert not issubclass(DriveIdempotencyConflictError, DriveConflictError)
    assert not issubclass(DriveConflictError, DriveIdempotencyConflictError)


def test_conflict_error_carries_revisions_and_message():
    err = DriveConflictError("stale", expected_revision=7, actual_revision=9)
    assert err.expected_revision == 7
    assert err.actual_revision == 9
    assert str(err) == "stale"
    # defaults when omitted
    bare = DriveConflictError("x")
    assert bare.expected_revision is None and bare.actual_revision is None


def test_idempotency_conflict_carries_key():
    err = DriveIdempotencyConflictError("reused", idempotency_key="req-1")
    assert err.idempotency_key == "req-1"
    assert str(err) == "reused"


def test_transition_error_carries_status_endpoints():
    err = DriveTransitionError("bad", from_status="completed", to_status="active")
    assert err.from_status == "completed"
    assert err.to_status == "active"
    assert str(err) == "bad"


@pytest.mark.parametrize("err_cls", ALL_DRIVE_ERRORS)
def test_all_catchable_through_base(err_cls):
    with pytest.raises(KTError):
        raise err_cls("boom")
    with pytest.raises(DriveError):
        raise err_cls("boom")
