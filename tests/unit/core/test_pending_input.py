"""Unit tests for :mod:`kohakuterrarium.core.pending_input`.

The stable-id + edit/cancel primitives backing UXI-08(a): a queued
mid-turn message can be corrected or dropped by id, but only while it
is still in the buffer (before the drain claims it).
"""

from kohakuterrarium.core.events import create_user_input_event
from kohakuterrarium.core.pending_input import (
    PENDING_ID_KEY,
    cancel_pending,
    edit_pending,
    new_pending_id,
    pending_id_of,
    stamp_pending_id,
)


class TestStamp:
    def test_new_id_is_unique(self):
        assert new_pending_id() != new_pending_id()

    def test_stamp_sets_and_returns_id(self):
        evt = create_user_input_event("hi")
        pid = stamp_pending_id(evt)
        assert evt.context[PENDING_ID_KEY] == pid
        assert pending_id_of(evt) == pid

    def test_stamp_is_idempotent(self):
        # A caller-minted id survives a later stamp so a shell can target
        # the message by an id it chose up front.
        evt = create_user_input_event("hi")
        evt.context[PENDING_ID_KEY] = "chosen"
        assert stamp_pending_id(evt) == "chosen"

    def test_pending_id_of_unstamped_is_none(self):
        assert pending_id_of(create_user_input_event("hi")) is None


class TestEditCancel:
    def _buffered(self, text):
        evt = create_user_input_event(text)
        pid = stamp_pending_id(evt)
        return evt, pid

    def test_edit_rewrites_matching_event(self):
        evt, pid = self._buffered("original")
        buffer = [evt]
        assert edit_pending(buffer, pid, "corrected") is True
        assert buffer[0].content == "corrected"

    def test_edit_unknown_id_is_noop(self):
        evt, _ = self._buffered("original")
        buffer = [evt]
        assert edit_pending(buffer, "ghost", "x") is False
        assert buffer[0].content == "original"

    def test_cancel_removes_matching_event(self):
        evt, pid = self._buffered("drop me")
        buffer = [evt]
        assert cancel_pending(buffer, pid) is True
        assert buffer == []

    def test_cancel_unknown_id_is_noop(self):
        evt, _ = self._buffered("keep me")
        buffer = [evt]
        assert cancel_pending(buffer, "ghost") is False
        assert buffer == [evt]

    def test_cancel_only_removes_the_targeted_event(self):
        a, pid_a = self._buffered("a")
        b, pid_b = self._buffered("b")
        buffer = [a, b]
        assert cancel_pending(buffer, pid_a) is True
        assert [e.content for e in buffer] == ["b"]
        # The survivor's id is intact.
        assert pending_id_of(buffer[0]) == pid_b
