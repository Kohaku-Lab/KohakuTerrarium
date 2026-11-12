"""Deferred result restoration under alternate session histories."""

import copy

from kohakuterrarium.core.agent.history.message_history import replay_with_settled_tools
from kohakuterrarium.core.agent.turn.deferred import record_deferred, restore_deferred
from kohakuterrarium.core.agent.turn.handlers import AgentHandlersMixin
from kohakuterrarium.core.conversation.conversation import Conversation
from types import SimpleNamespace
from kohakuterrarium.core.events import create_tool_complete_event
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.history.replay import replay_conversation


def test_completion_activity_on_sibling_does_not_synthesize_tool_round():
    events = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "call",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 2,
            "type": "tool_call",
            "call_id": "job",
            "name": "block",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 3,
            "type": "user_message",
            "content": "sibling",
            "turn_index": 2,
            "branch_id": 2,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 4,
            "type": "tool_result",
            "call_id": "job",
            "output": "late",
            "defer_controller": True,
            "turn_index": 2,
            "branch_id": 2,
            "parent_branch_path": [[1, 1]],
        },
        {
            "event_id": 5,
            "type": "deferred_job_result",
            "job_id": "job",
            "text_mode": True,
            "content": "late",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 6,
            "type": "user_message",
            "content": "other",
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
    ]
    messages = replay_with_settled_tools(
        events, branch_view={1: 1, 2: 1}, resumable=True
    )
    assert not any(m.get("tool_calls") or m["role"] == "tool" for m in messages)
    assert "late" not in str(messages)
    events = copy.deepcopy(events)
    events[4]["text_mode"] = False
    events[3]["defer_controller"] = False
    native = replay_with_settled_tools(events, branch_view={1: 1, 2: 1}, resumable=True)
    assert any(m.get("tool_calls") for m in native)
    assert any(m["role"] == "tool" and m["content"] == "late" for m in native)


def test_undelivered_results_survive_compaction_and_sibling_selection():
    events = [
        {
            "type": "deferred_job_result",
            "event_id": 5,
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
            "job_id": "job",
            "content": "late",
            "context": {},
        },
        {
            "type": "deferred_job_feedback",
            "event_id": 7,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
            "job_ids": ["job"],
        },
        {
            "type": "compact_replace",
            "event_id": 9,
            "turn_index": 2,
            "branch_id": 2,
            "parent_branch_path": [[1, 1]],
            "replaced_from_event_id": 1,
            "replaced_to_event_id": 8,
            "summary": "summary",
        },
    ]
    assert not restore_deferred(events, {1: 1, 2: 1})
    restored = restore_deferred(events, {1: 1, 2: 2})
    assert len(restored) == 1
    assert restored[0][0].content == "late"


def test_durable_result_deduplicates_and_keeps_structured_content(tmp_path):
    store = SessionStore(tmp_path / "deferred.kohakutr")
    event = create_tool_complete_event(
        "job",
        [
            {"type": "text", "text": "partial"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
        ],
        error="failed",
        interrupted=True,
    )
    try:
        record_deferred(store, "agent", event, {1: 1})
        assert store._unflushed_event_count == 0
        record_deferred(store, "agent", event, {1: 1})
        records = store.get_events("agent")
        assert len(records) == 1
        restored = restore_deferred(records, {1: 1})
        assert len(restored) == 1
        assert restored[0][0].is_multimodal()
        assert restored[0][0].context["error"] == "failed"
        assert restored[0][0].context["interrupted"] is True
    finally:
        store.close(update_status=False)


def test_invisible_deferred_result_does_not_split_streamed_text():
    events = [
        {
            "type": "text",
            "event_id": 1,
            "turn_index": 1,
            "branch_id": 1,
            "content": "before",
        },
        {
            "type": "deferred_job_result",
            "event_id": 2,
            "turn_index": 1,
            "branch_id": 1,
            "job_id": "job",
        },
        {
            "type": "text",
            "event_id": 3,
            "turn_index": 1,
            "branch_id": 1,
            "content": "after",
        },
    ]
    assert replay_conversation(events) == [
        {"role": "assistant", "content": "beforeafter"}
    ]


def test_native_deferred_result_prevents_resume_synthetic():
    events = [
        {
            "event_id": 1,
            "type": "tool_call",
            "call_id": "job",
            "name": "block",
        },
        {
            "event_id": 2,
            "type": "deferred_job_result",
            "job_id": "job",
            "text_mode": False,
            "content": "late",
        },
    ]
    normalized = replay_with_settled_tools(events, branch_view={}, resumable=True)
    assert not any(evt.get("_synthetic_resume") for evt in normalized)


def test_deferred_feedback_multiple_same_turn_keep_event_order():
    events = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "call",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 2,
            "type": "deferred_job_feedback",
            "content": "first",
            "metadata": {"delivery_turn_index": 1, "delivery_branch_id": 1},
        },
        {
            "event_id": 3,
            "type": "deferred_job_feedback",
            "content": "second",
            "metadata": {"delivery_turn_index": 1, "delivery_branch_id": 1},
        },
    ]
    messages = replay_conversation(events, include_metadata=True)
    assert [
        message["content"] for message in messages if message["role"] == "user"
    ] == [
        "call",
        "first",
        "second",
    ]


def test_feedback_without_new_user_preserves_event_order():
    events = [
        {
            "event_id": 1,
            "type": "user_message",
            "content": "call",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 2,
            "type": "tool_call",
            "call_id": "job",
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "event_id": 3,
            "type": "deferred_job_feedback",
            "content": "late",
            "turn_index": 1,
            "branch_id": 1,
            "metadata": {"delivery_turn_index": 1, "delivery_branch_id": 1},
        },
    ]
    messages = replay_conversation(events, include_metadata=True)
    assert [m["content"] for m in messages if m["role"] == "user"] == ["call", "late"]


def test_cutoff_excludes_already_buffered_deferred_feedback(tmp_path):
    store = SessionStore(tmp_path / "cutoff.kohakutr")
    result = create_tool_complete_event("job", "late")
    try:
        record_deferred(store, "agent", result, {1: 1})
        cutoff = store.get_events("agent")[-1]["event_id"]
        conversation = Conversation()
        controller = SimpleNamespace(conversation=conversation, _interrupted=False)
        host = SimpleNamespace(
            session_store=store,
            config=SimpleNamespace(name="agent"),
            _deferred_job_results=[(result, {1: 1})],
            _parent_branch_path=[],
            _turn_index=1,
            _branch_id=1,
            _context_cutoff_event_id=cutoff,
            _turn_cancelled_jobs=set(),
            _turn_usage_accum={},
            trigger_manager=SimpleNamespace(set_context_all=lambda _: None),
            _termination_checker=None,
            _message_origin=lambda _: {},
        )
        AgentHandlersMixin._prepare_processing_cycle(
            host, create_tool_complete_event("other", ""), controller
        )
        assert not conversation.get_messages()
    finally:
        store.close(update_status=False)
