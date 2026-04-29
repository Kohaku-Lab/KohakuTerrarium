"""Wave C — event log is primary; snapshot is a derived cache.

Writes state-bearing events via ``append_event``, replays them into a
conversation, closes + reopens the store, and verifies the replayed
conversation survives. Also checks that resume prefers a fresh
snapshot but falls back to replay when the snapshot is stale.
"""

import pytest

from kohakuterrarium.session.history import (
    dedupe_adjacent_duplicate_events,
    replay_conversation,
)
from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def session_path(tmp_path):
    path = tmp_path / "primary.kohakutr"
    s = SessionStore(path)
    s.init_meta(
        session_id="primary",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["agent"],
    )
    s.close()
    return path


def _seed_conversation_events(store: SessionStore) -> None:
    store.append_event("agent", "user_message", {"content": "ping"})
    # Assistant streams three chunks that should collapse.
    store.append_event("agent", "text_chunk", {"content": "pon", "chunk_seq": 0})
    store.append_event("agent", "text_chunk", {"content": "g", "chunk_seq": 1})
    # Assistant then tool calls.
    store.append_event(
        "agent",
        "assistant_tool_calls",
        {
            "tool_calls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ]
        },
    )
    store.append_event(
        "agent",
        "tool_result",
        {"name": "bash", "call_id": "tc1", "output": "ok"},
    )


class TestEventLogReconstructsConversation:
    def test_duplicate_adjacent_chunks_are_collapsed_for_legacy_replay(self):
        events = [
            {
                "type": "text_chunk",
                "content": "Root",
                "chunk_seq": 0,
                "event_id": 1,
                "ts": 1,
            },
            {
                "type": "text_chunk",
                "content": "Root",
                "chunk_seq": 0,
                "event_id": 2,
                "ts": 2,
            },
            {
                "type": "text_chunk",
                "content": " cause",
                "chunk_seq": 1,
                "event_id": 3,
                "ts": 3,
            },
            {
                "type": "text_chunk",
                "content": " cause",
                "chunk_seq": 1,
                "event_id": 4,
                "ts": 4,
            },
            {
                "type": "tool_call",
                "name": "read",
                "call_id": "job_1",
                "args": {},
                "event_id": 5,
                "ts": 5,
            },
            {
                "type": "tool_call",
                "name": "read",
                "call_id": "job_1",
                "args": {},
                "event_id": 6,
                "ts": 6,
            },
        ]

        deduped = dedupe_adjacent_duplicate_events(events)
        assert [e["event_id"] for e in deduped] == [1, 3, 5]

        msgs = replay_conversation(events)
        assert msgs[0]["content"] == "Root cause"

    def test_round_trip_matches_expected_shape(self, session_path):
        store = SessionStore(session_path)
        try:
            _seed_conversation_events(store)
            events = store.get_events("agent")
            msgs = replay_conversation(events)
            assert msgs == [
                {"role": "user", "content": "ping"},
                {
                    "role": "assistant",
                    "content": "pong",
                    "tool_calls": [
                        {
                            "id": "tc1",
                            "type": "function",
                            "function": {"name": "bash", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "content": "ok",
                    "tool_call_id": "tc1",
                    "name": "bash",
                },
            ]
        finally:
            store.close()

    def test_survives_close_and_reopen(self, session_path):
        store = SessionStore(session_path)
        _seed_conversation_events(store)
        store.close()

        reopened = SessionStore(session_path)
        try:
            events = reopened.get_events("agent")
            msgs = replay_conversation(events)
            assert msgs[0]["role"] == "user"
            assert msgs[1]["role"] == "assistant"
            assert msgs[1]["content"] == "pong"
            assert msgs[-1]["role"] == "tool"
        finally:
            reopened.close()


class TestResumeSnapshotCacheBehavior:
    def test_replay_used_when_snapshot_missing(self, session_path):
        """If the events table has state but no snapshot, replay fills in."""
        from kohakuterrarium.session.resume import (
            _load_conversation_with_replay_fallback,
        )

        store = SessionStore(session_path)
        try:
            _seed_conversation_events(store)
            # No save_conversation call — snapshot is absent.
            messages = _load_conversation_with_replay_fallback(store, "agent")
            assert messages is not None
            assert messages[0]["content"] == "ping"
            assert messages[1]["content"] == "pong"
        finally:
            store.close()

    def test_replay_used_when_snapshot_stale(self, session_path):
        """Snapshot older than latest event id triggers replay fallback."""
        from kohakuterrarium.session.resume import (
            _load_conversation_with_replay_fallback,
        )

        store = SessionStore(session_path)
        try:
            # Pre-seed a stale snapshot paired with a low event-id tag.
            store.save_conversation("agent", [{"role": "user", "content": "stale"}])
            store.state["agent:snapshot_event_id"] = 0
            _seed_conversation_events(store)
            messages = _load_conversation_with_replay_fallback(store, "agent")
            assert messages is not None
            # Stale snapshot said "stale"; replay must yield the real
            # conversation.
            assert any(m.get("content") == "pong" for m in messages)
        finally:
            store.close()

    def test_fresh_snapshot_preferred_when_up_to_date(self, session_path):
        """When snapshot_event_id >= last_event_id, the snapshot wins."""
        from kohakuterrarium.session.resume import (
            _load_conversation_with_replay_fallback,
        )

        store = SessionStore(session_path)
        try:
            _seed_conversation_events(store)
            events = store.get_events("agent")
            last_eid = max(e.get("event_id", 0) for e in events)
            fixed_snapshot = [{"role": "assistant", "content": "cached"}]
            store.save_conversation("agent", fixed_snapshot)
            store.state["agent:snapshot_event_id"] = last_eid
            messages = _load_conversation_with_replay_fallback(store, "agent")
            assert messages == fixed_snapshot
        finally:
            store.close()
