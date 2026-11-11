"""Writer-lock contention names the process that actually holds the lock."""

import os

import pytest

from kohakuterrarium.errors import SessionLockedError
from kohakuterrarium.session.store.lifecycle.lock import (
    acquire_writer_lock,
    release_writer_lock,
)


def test_same_process_holder_is_reported_as_this_process(tmp_path):
    target = str(tmp_path / "session.kohakutr")
    held = acquire_writer_lock(target)
    try:
        with pytest.raises(SessionLockedError) as excinfo:
            acquire_writer_lock(target)
    finally:
        release_writer_lock(held)

    message = str(excinfo.value)
    assert "open in this process" in message
    assert "another process" not in message
    assert excinfo.value.holder_pid == os.getpid()


def test_lock_is_reacquirable_after_release(tmp_path):
    target = str(tmp_path / "session.kohakutr")
    first = acquire_writer_lock(target)
    release_writer_lock(first)

    again = acquire_writer_lock(target)
    try:
        assert again.held
    finally:
        release_writer_lock(again)
