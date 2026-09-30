"""Framework errors map to stable HTTP statuses at the API boundary."""

import pytest

from kohakuterrarium.api.app import kt_error_status
from kohakuterrarium.errors import (
    ConflictError,
    KTError,
    NotFoundError,
    SessionError,
    SessionLockedError,
    SessionNotResumableError,
)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (NotFoundError("missing"), 404),
        (FileNotFoundError("missing"), 404),
        (ConflictError("busy"), 409),
        (SessionLockedError("held by another writer", holder_pid=4242), 409),
        (SessionNotResumableError("bad metadata"), 400),
        (SessionError("plain session failure"), 500),
        (KTError("anything else"), 500),
    ],
)
def test_error_maps_to_status(error, status):
    assert kt_error_status(error) == status


def test_locked_session_is_a_conflict_and_keeps_the_holder_pid():
    error = SessionLockedError("held", holder_pid=4242)
    assert isinstance(error, ConflictError)
    assert isinstance(error, RuntimeError)
    assert error.holder_pid == 4242
