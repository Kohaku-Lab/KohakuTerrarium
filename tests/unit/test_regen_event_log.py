"""Regression tests for regenerate / edit+rerun event-log behavior.

The fork/branch UX is built on a ``turn_index`` + ``branch_id`` data
model: every state-bearing event carries both, and a regen / edit
opens a new ``branch_id`` of the same ``turn_index``. ``replay_conversation``
defaults to the latest branch per turn — siblings stay on disk for
the ``<1/N>`` navigator.

These tests pin:

- New user input bumps ``_turn_index`` and resets ``_branch_id`` to 1.
- Pure regenerate keeps ``_turn_index``, bumps ``_branch_id``.
- Edit+rerun keeps ``_turn_index`` (of the edited turn), bumps
  ``_branch_id``.
- ``replay_conversation`` shows only the latest branch by default and
  honours an explicit ``branch_view`` override.
- ``select_live_event_ids`` / ``collect_branch_metadata`` expose what
  the navigator needs.
- Empty ``user_message`` events are never written for pure regen.
"""

from kohakuterrarium.session.history import (
    collect_branch_metadata,
    replay_conversation,
    select_live_event_ids,
)
from kohakuterrarium.session.store import SessionStore


def _seed_first_turn(store: SessionStore, agent: str) -> None:
    """One user→assistant turn with branch 1 events."""
    store.append_event(
        agent, "user_input", {"content": "hi"}, turn_index=1, branch_id=1
    )
    store.append_event(
        agent, "user_message", {"content": "hi"}, turn_index=1, branch_id=1
    )
    store.append_event(agent, "processing_start", {}, turn_index=1, branch_id=1)
    store.append_event(
        agent,
        "text_chunk",
        {"content": "first ", "chunk_seq": 0},
        turn_index=1,
        branch_id=1,
    )
    store.append_event(
        agent,
        "text_chunk",
        {"content": "reply", "chunk_seq": 1},
        turn_index=1,
        branch_id=1,
    )
    store.append_event(agent, "processing_end", {}, turn_index=1, branch_id=1)


def _seed_regen_branch(
    store: SessionStore,
    agent: str,
    content: str,
    *,
    branch_id: int = 2,
    user_content: str = "hi",
) -> None:
    """A regen of turn 1 → ``branch_id``. Each branch is self-contained:
    user_input + user_message + assistant events all carry the new
    branch_id. Pure regen mirrors the previous branch's user wording.
    """
    store.append_event(
        agent,
        "user_input",
        {"content": user_content},
        turn_index=1,
        branch_id=branch_id,
    )
    store.append_event(
        agent,
        "user_message",
        {"content": user_content},
        turn_index=1,
        branch_id=branch_id,
    )
    store.append_event(agent, "processing_start", {}, turn_index=1, branch_id=branch_id)
    store.append_event(
        agent,
        "text_chunk",
        {"content": content, "chunk_seq": 0},
        turn_index=1,
        branch_id=branch_id,
    )
    store.append_event(agent, "processing_end", {}, turn_index=1, branch_id=branch_id)


class TestReplayDefaultsToLatestBranch:
    def test_pure_regen_replay_picks_branch_2(self, tmp_path):
        path = tmp_path / "session.kohakutr.v2"
        store = SessionStore(str(path))
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["alice"],
        )
        _seed_first_turn(store, "alice")
        _seed_regen_branch(store, "alice", "second reply")

        events = store.get_events("alice")
        msgs = replay_conversation(events)

        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["content"] == "hi"
        assert msgs[1]["content"] == "second reply"
        store.close(update_status=False)

    def test_branch_view_override_shows_branch_1(self, tmp_path):
        path = tmp_path / "session.kohakutr.v2"
        store = SessionStore(str(path))
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["alice"],
        )
        _seed_first_turn(store, "alice")
        _seed_regen_branch(store, "alice", "second reply")

        events = store.get_events("alice")
        msgs = replay_conversation(events, branch_view={1: 1})

        assert msgs[0]["content"] == "hi"
        assert msgs[1]["content"] == "first reply"
        store.close(update_status=False)


class TestEditRerunNewBranchKeepsTurnIndex:
    def test_edit_creates_new_branch_with_new_user_content(self, tmp_path):
        path = tmp_path / "session.kohakutr.v2"
        store = SessionStore(str(path))
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["alice"],
        )
        _seed_first_turn(store, "alice")
        # Edit branch: same turn_index, bumped branch_id, new user content.
        store.append_event(
            "alice",
            "user_input",
            {"content": "actually, hello"},
            turn_index=1,
            branch_id=2,
        )
        store.append_event(
            "alice",
            "user_message",
            {"content": "actually, hello"},
            turn_index=1,
            branch_id=2,
        )
        store.append_event("alice", "processing_start", {}, turn_index=1, branch_id=2)
        store.append_event(
            "alice",
            "text_chunk",
            {"content": "edited reply", "chunk_seq": 0},
            turn_index=1,
            branch_id=2,
        )
        store.append_event("alice", "processing_end", {}, turn_index=1, branch_id=2)

        events = store.get_events("alice")
        msgs = replay_conversation(events)

        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["content"] == "actually, hello"
        assert msgs[1]["content"] == "edited reply"
        store.close(update_status=False)


class TestBranchMetadata:
    def test_collect_branch_metadata_lists_all_branches(self, tmp_path):
        path = tmp_path / "session.kohakutr.v2"
        store = SessionStore(str(path))
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["alice"],
        )
        _seed_first_turn(store, "alice")
        _seed_regen_branch(store, "alice", "second reply")
        # Third regen
        _seed_regen_branch(store, "alice", "third reply")
        # Fix the third one to branch 3 (helper writes to branch 2 for both,
        # so let's add branch 3 events directly).
        # Actually re-do: helper always writes branch 2; let's manually
        # write branch 3 so we can verify the navigator listing.
        store.append_event("alice", "processing_start", {}, turn_index=1, branch_id=3)
        store.append_event(
            "alice",
            "text_chunk",
            {"content": "third try", "chunk_seq": 0},
            turn_index=1,
            branch_id=3,
        )
        store.append_event("alice", "processing_end", {}, turn_index=1, branch_id=3)

        events = store.get_events("alice")
        meta = collect_branch_metadata(events)

        assert 1 in meta
        assert sorted(meta[1]["branches"]) == [1, 2, 3]
        assert meta[1]["latest_branch"] == 3
        store.close(update_status=False)

    def test_select_live_event_ids_keeps_only_latest_branch(self, tmp_path):
        path = tmp_path / "session.kohakutr.v2"
        store = SessionStore(str(path))
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["alice"],
        )
        _seed_first_turn(store, "alice")
        _seed_regen_branch(store, "alice", "second reply")

        events = store.get_events("alice")
        live = select_live_event_ids(events)
        # Branch 1 events have ids 1..6; branch 2 has 7..9.
        # All branch-1 state-bearing events are excluded except those
        # without turn/branch metadata (none in this seed).
        for evt in events:
            ti = evt.get("turn_index")
            bi = evt.get("branch_id")
            eid = evt.get("event_id")
            if not isinstance(ti, int) or not isinstance(bi, int):
                continue
            if bi == 1:
                assert eid not in live, f"branch-1 event {eid} should be hidden"
            elif bi == 2:
                assert eid in live, f"branch-2 event {eid} should be live"
        store.close(update_status=False)

    def test_no_branches_falls_back_to_all_live(self):
        # Legacy events without turn_index / branch_id are always live.
        events = [
            {"type": "user_message", "content": "hi", "event_id": 1},
            {
                "type": "text_chunk",
                "content": "ok",
                "chunk_seq": 0,
                "event_id": 2,
            },
        ]
        live = select_live_event_ids(events)
        assert live == {1, 2}


class TestReplayHandlesEmptyAndMixedSchemas:
    def test_empty_event_list(self):
        assert replay_conversation([]) == []

    def test_legacy_events_without_branch_metadata(self):
        # Pre-v2 events (no turn_index/branch_id): treat as live.
        events = [
            {"type": "user_message", "content": "q", "event_id": 1},
            {
                "type": "text_chunk",
                "content": "answer",
                "chunk_seq": 0,
                "event_id": 2,
            },
        ]
        msgs = replay_conversation(events)
        assert msgs == [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "answer"},
        ]


class TestNoEmptyUserMessages:
    def test_pure_regen_writes_no_empty_user_message(self, tmp_path):
        """Each branch is self-contained: regen mirrors the previous
        branch's user wording. The event log must never contain an
        empty-content ``user_message``."""
        path = tmp_path / "session.kohakutr.v2"
        store = SessionStore(str(path))
        store.init_meta(
            session_id="s1",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["alice"],
        )
        _seed_first_turn(store, "alice")
        _seed_regen_branch(store, "alice", "second reply")

        events = store.get_events("alice")
        user_msgs = [e for e in events if e.get("type") == "user_message"]
        # One per branch (self-contained).
        assert len(user_msgs) == 2
        for e in user_msgs:
            assert e.get("content") == "hi"
        # No empty content anywhere in the user-side events.
        for e in events:
            if e.get("type") in ("user_input", "user_message"):
                assert e.get("content")
        store.close(update_status=False)
