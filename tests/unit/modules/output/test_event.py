"""Unit tests for :mod:`kohakuterrarium.modules.output.event`.

Behavior-first: OutputEvent defaults, UIReply timeout/superseded
predicates against the reserved action ids.
"""

from kohakuterrarium.modules.output.event import (
    ACTION_SUPERSEDED,
    ACTION_TIMEOUT,
    OutputEvent,
    UIReply,
)


class TestOutputEventDefaults:
    def test_minimal_event_has_chat_surface_and_not_interactive(self):
        ev = OutputEvent(type="text")
        assert ev.surface == "chat"
        assert ev.interactive is False
        assert ev.content == ""
        assert ev.payload == {}
        assert ev.timeout_s is None

    def test_payload_and_content_retained(self):
        ev = OutputEvent(type="tool_start", content="bash", payload={"job_id": "j1"})
        assert ev.content == "bash"
        assert ev.payload["job_id"] == "j1"


class TestUIReplyPredicates:
    def test_is_timeout_true_only_for_timeout_action(self):
        reply = UIReply(event_id="e1", action_id=ACTION_TIMEOUT)
        assert reply.is_timeout is True
        assert reply.is_superseded is False

    def test_is_superseded_true_only_for_superseded_action(self):
        reply = UIReply(event_id="e1", action_id=ACTION_SUPERSEDED)
        assert reply.is_superseded is True
        assert reply.is_timeout is False

    def test_ordinary_action_is_neither(self):
        reply = UIReply(event_id="e1", action_id="confirm", values={"ok": True})
        assert reply.is_timeout is False
        assert reply.is_superseded is False
        assert reply.values == {"ok": True}
