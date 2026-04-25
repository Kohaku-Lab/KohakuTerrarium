"""Pin: nested branching across turns replays correctly via the
backend ``replay_conversation`` / ``select_live_event_ids`` helpers
that CLI, TUI, programmatic, and migration paths all share.

The bug: with a follow-up turn made under a regen of an earlier
turn, switching the earlier turn between branches used to keep the
follow-up visible regardless of which branch was selected. The
correct behavior is to hide the follow-up when its
``parent_branch_path`` doesn't match the user's current selection.
"""

from kohakuterrarium.session.history import (
    collect_branch_metadata,
    replay_conversation,
    select_live_event_ids,
)
from kohakuterrarium.session.store import SessionStore


def _seed_two_turn_session(tmp_path) -> SessionStore:
    path = tmp_path / "s.kohakutr.v2"
    store = SessionStore(str(path))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    # Turn 1, branch 1 (initial)
    store.append_event(
        "alice", "user_message", {"content": "hi"}, turn_index=1, branch_id=1
    )
    store.append_event(
        "alice",
        "text_chunk",
        {"content": "1a", "chunk_seq": 0},
        turn_index=1,
        branch_id=1,
    )
    store.append_event("alice", "processing_end", {}, turn_index=1, branch_id=1)
    # Turn 1, branch 2 (regen — same parent path as branch 1: empty).
    store.append_event(
        "alice", "user_message", {"content": "hi"}, turn_index=1, branch_id=2
    )
    store.append_event(
        "alice",
        "text_chunk",
        {"content": "2a", "chunk_seq": 0},
        turn_index=1,
        branch_id=2,
    )
    store.append_event("alice", "processing_end", {}, turn_index=1, branch_id=2)
    # Turn 2, branch 1 — created under turn 1 branch 2's subtree.
    store.append_event(
        "alice",
        "user_message",
        {"content": "next"},
        turn_index=2,
        branch_id=1,
        parent_branch_path=[(1, 2)],
    )
    store.append_event(
        "alice",
        "text_chunk",
        {"content": "next-a", "chunk_seq": 0},
        turn_index=2,
        branch_id=1,
        parent_branch_path=[(1, 2)],
    )
    store.append_event(
        "alice",
        "processing_end",
        {},
        turn_index=2,
        branch_id=1,
        parent_branch_path=[(1, 2)],
    )
    return store


def test_default_view_shows_latest_subtree(tmp_path):
    store = _seed_two_turn_session(tmp_path)
    events = store.get_events("alice")
    msgs = replay_conversation(events)
    # Turn 1 → latest = 2; turn 2 → latest = 1; full path is live.
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "hi"),
        ("assistant", "2a"),
        ("user", "next"),
        ("assistant", "next-a"),
    ]
    store.close(update_status=False)


def test_switching_to_branch_1_hides_follow_up(tmp_path):
    """Branch 1 of turn 1 has no descendants; the follow-up turn
    belongs to branch 2's subtree and must NOT render here."""
    store = _seed_two_turn_session(tmp_path)
    events = store.get_events("alice")
    msgs = replay_conversation(events, branch_view={1: 1})
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "hi"),
        ("assistant", "1a"),
    ]
    store.close(update_status=False)


def test_select_live_event_ids_excludes_orphaned_followups(tmp_path):
    """The CLI/TUI scrollback readers all run through this helper.
    Switching to branch 1 must drop turn 2 entirely, not just the
    assistant text — otherwise the user sees half a turn."""
    store = _seed_two_turn_session(tmp_path)
    events = store.get_events("alice")
    live = select_live_event_ids(events, branch_view={1: 1})
    for evt in events:
        eid = evt.get("event_id")
        ti = evt.get("turn_index")
        if not isinstance(eid, int):
            continue
        if ti == 2:
            assert eid not in live, f"turn-2 event {eid} should be hidden"
        elif ti == 1 and evt.get("branch_id") == 1:
            assert eid in live
    store.close(update_status=False)


def test_branch_metadata_filters_to_live_subtree(tmp_path):
    """Per-turn ``branches`` list must reflect only branches whose
    parent path is consistent with the prior selections — not the
    full population of branches in the database."""
    store = _seed_two_turn_session(tmp_path)
    events = store.get_events("alice")
    # Default view: turn 1 has both branches available.
    meta = collect_branch_metadata(events)
    assert sorted(meta[1]["branches"]) == [1, 2]
    # Default view of turn 2: only branch 1 exists, under branch 2.
    assert meta[2]["branches"] == [1]

    # Override turn 1 to branch 1: turn 2 disappears from the metadata
    # because no branch of turn 2 has a path compatible with (1, 1).
    meta = collect_branch_metadata(events, branch_view={1: 1})
    assert 2 not in meta
    store.close(update_status=False)


def _seed_nested_session(tmp_path) -> SessionStore:
    """Turn 1 has branches 1+2; turn 2 has its own branches 1+2 under turn 1 branch 2."""
    path = tmp_path / "s.kohakutr.v2"
    store = SessionStore(str(path))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    # Turn 1 branch 1
    store.append_event(
        "alice", "user_message", {"content": "hi"}, turn_index=1, branch_id=1
    )
    store.append_event(
        "alice",
        "text_chunk",
        {"content": "1a", "chunk_seq": 0},
        turn_index=1,
        branch_id=1,
    )
    store.append_event("alice", "processing_end", {}, turn_index=1, branch_id=1)
    # Turn 1 branch 2
    store.append_event(
        "alice", "user_message", {"content": "hi"}, turn_index=1, branch_id=2
    )
    store.append_event(
        "alice",
        "text_chunk",
        {"content": "2a", "chunk_seq": 0},
        turn_index=1,
        branch_id=2,
    )
    store.append_event("alice", "processing_end", {}, turn_index=1, branch_id=2)
    # Turn 2 branch 1, parent (1, 2)
    for content in ("n1", "n1-a"):
        et = "user_message" if content == "n1" else "text_chunk"
        data = {"content": content}
        if et == "text_chunk":
            data["chunk_seq"] = 0
        store.append_event(
            "alice",
            et,
            data,
            turn_index=2,
            branch_id=1,
            parent_branch_path=[(1, 2)],
        )
    store.append_event(
        "alice",
        "processing_end",
        {},
        turn_index=2,
        branch_id=1,
        parent_branch_path=[(1, 2)],
    )
    # Turn 2 branch 2, also parent (1, 2)
    for content in ("n1", "n2-a"):
        et = "user_message" if content == "n1" else "text_chunk"
        data = {"content": content}
        if et == "text_chunk":
            data["chunk_seq"] = 0
        store.append_event(
            "alice",
            et,
            data,
            turn_index=2,
            branch_id=2,
            parent_branch_path=[(1, 2)],
        )
    store.append_event(
        "alice",
        "processing_end",
        {},
        turn_index=2,
        branch_id=2,
        parent_branch_path=[(1, 2)],
    )
    return store


def test_nested_branch_default_picks_latest_at_every_level(tmp_path):
    store = _seed_nested_session(tmp_path)
    msgs = replay_conversation(store.get_events("alice"))
    contents = [m["content"] for m in msgs]
    assert "2a" in contents
    assert "n2-a" in contents
    assert "1a" not in contents
    assert "n1-a" not in contents
    store.close(update_status=False)


def test_nested_branch_user_picks_turn2_branch1(tmp_path):
    store = _seed_nested_session(tmp_path)
    msgs = replay_conversation(store.get_events("alice"), branch_view={1: 2, 2: 1})
    contents = [m["content"] for m in msgs]
    assert "2a" in contents
    assert "n1-a" in contents
    assert "n2-a" not in contents
    store.close(update_status=False)


def test_nested_branch_switch_to_turn1_branch1_hides_subtree(tmp_path):
    store = _seed_nested_session(tmp_path)
    msgs = replay_conversation(store.get_events("alice"), branch_view={1: 1})
    contents = [m["content"] for m in msgs]
    assert "1a" in contents
    assert "n1-a" not in contents
    assert "n2-a" not in contents
    store.close(update_status=False)
