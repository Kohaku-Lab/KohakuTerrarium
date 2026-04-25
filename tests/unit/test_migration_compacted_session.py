"""End-to-end migration tests for compacted v1 sessions.

Pins the user-reported bug: after compaction in v1, the snapshot only
held [system, summary, recent...] — the migrator that derived events
from the snapshot lost the entire pre-compact history. The new
migrator drives off the v1 *event log* so pre-compact messages
survive, and converts ``compact_complete`` to ``compact_replace`` so
the summary renders as a compact bubble (not a plain assistant
message).
"""

from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.session.migrations.v1_to_v2 import migrate
from kohakuterrarium.session.store import SessionStore


def _build_v1_compacted(path, agent: str = "alice"):
    """A v1 session with a compact_complete event in its event log."""
    store = SessionStore(path)
    store.init_meta(
        session_id="v1-compacted",
        config_type="agent",
        config_path="x",
        pwd=str(path.parent),
        agents=[agent],
    )
    store.meta["format_version"] = 1

    # Pre-compact history (3 user→assistant turns).
    for i in range(1, 4):
        store.append_event(agent, "user_input", {"content": f"Q{i}"})
        store.append_event(agent, "processing_start", {})
        store.append_event(agent, "text", {"content": f"A{i}"})
        store.append_event(agent, "processing_end", {})

    # Compaction summarizes the first 3 turns.
    store.append_event(
        agent,
        "compact_complete",
        {
            "summary": "Summary of turns 1-3.",
            "round": 1,
            "messages_compacted": 6,
        },
    )

    # One post-compact turn.
    store.append_event(agent, "user_input", {"content": "Q4"})
    store.append_event(agent, "processing_start", {})
    store.append_event(agent, "text", {"content": "A4"})
    store.append_event(agent, "processing_end", {})

    # v1 snapshot is post-compact (mimics live behaviour).
    store.save_conversation(
        agent,
        [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "Summary of turns 1-3."},
            {"role": "user", "content": "Q4"},
            {"role": "assistant", "content": "A4"},
        ],
    )
    store.close(update_status=False)


class TestCompactedV1MigrationPreservesHistory:
    def test_pre_compact_messages_survive_migration(self, tmp_path):
        src = tmp_path / "v1.kohakutr"
        dst = tmp_path / "v2.kohakutr.v2"
        _build_v1_compacted(src)

        migrate(str(src), str(dst))

        store = SessionStore(str(dst))
        try:
            events = store.get_events("alice")
            # Pre-compact user inputs Q1 / Q2 / Q3 must survive as
            # user_message events with the new turn_index metadata.
            user_msgs = [e for e in events if e.get("type") == "user_message"]
            user_contents = [e.get("content") for e in user_msgs]
            assert "Q1" in user_contents
            assert "Q2" in user_contents
            assert "Q3" in user_contents
            assert "Q4" in user_contents
        finally:
            store.close(update_status=False)

    def test_compact_complete_becomes_compact_replace(self, tmp_path):
        src = tmp_path / "v1.kohakutr"
        dst = tmp_path / "v2.kohakutr.v2"
        _build_v1_compacted(src)
        migrate(str(src), str(dst))

        store = SessionStore(str(dst))
        try:
            events = store.get_events("alice")
            replace_evts = [e for e in events if e.get("type") == "compact_replace"]
            assert len(replace_evts) == 1
            evt = replace_evts[0]
            assert evt.get("summary_text") == "Summary of turns 1-3."
        finally:
            store.close(update_status=False)

    def test_summary_replays_as_compact_role_not_assistant(self, tmp_path):
        """The compact summary text must NOT appear as a plain
        assistant message after migration — that was the user-reported
        bug. ``replay_conversation`` should now produce one assistant
        message per real assistant turn plus a single compact summary,
        but the summary content lands inside the ``compact_replace``
        event, not as a duplicate text turn."""
        src = tmp_path / "v1.kohakutr"
        dst = tmp_path / "v2.kohakutr.v2"
        _build_v1_compacted(src)
        migrate(str(src), str(dst))

        store = SessionStore(str(dst))
        try:
            events = store.get_events("alice")
            replayed = replay_conversation(events)
            # No assistant message should literally equal the summary
            # text — that text is owned by compact_replace and the
            # frontend renders it as a compact bubble.
            assert all(
                m.get("content") != "Summary of turns 1-3."
                or m.get("role") != "assistant"
                or any(
                    e.get("type") == "compact_replace"
                    and e.get("summary_text") == "Summary of turns 1-3."
                    for e in events
                )
                for m in replayed
            )
        finally:
            store.close(update_status=False)


class TestPlainV1MigrationRoundtrip:
    def test_uncompacted_v1_session_replays_in_full(self, tmp_path):
        src = tmp_path / "v1.kohakutr"
        dst = tmp_path / "v2.kohakutr.v2"

        store = SessionStore(src)
        store.init_meta(
            session_id="v1-plain",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["alice"],
        )
        store.meta["format_version"] = 1
        store.append_event("alice", "user_input", {"content": "hi"})
        store.append_event("alice", "processing_start", {})
        store.append_event("alice", "text", {"content": "hello"})
        store.append_event("alice", "processing_end", {})
        store.save_conversation(
            "alice",
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        )
        store.close(update_status=False)

        migrate(str(src), str(dst))

        store2 = SessionStore(str(dst))
        try:
            events = store2.get_events("alice")
            replayed = replay_conversation(events)
            roles = [m.get("role") for m in replayed]
            assert roles == ["user", "assistant"]
            assert replayed[0].get("content") == "hi"
            assert replayed[1].get("content") == "hello"
        finally:
            store2.close(update_status=False)
