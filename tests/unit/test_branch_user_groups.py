"""Pin: ``collect_user_groups`` cleanly separates regen siblings
(same user content) from edit siblings (different user content).

This is the helper CLI / TUI / programmatic surfaces use to decide
whether a turn's alternatives belong on the user-bubble navigator,
the assistant-bubble navigator, or both. The frontend has the same
logic in JS; backend parity matters because ``/branch`` listing,
session lister metadata, and any future programmatic inspection all
go through this function.
"""

from kohakuterrarium.session.history import collect_user_groups
from kohakuterrarium.session.store import SessionStore


def _add_branch(store, branch_id, user_content, assistant_text):
    store.append_event(
        "alice",
        "user_message",
        {"content": user_content},
        turn_index=1,
        branch_id=branch_id,
    )
    store.append_event(
        "alice",
        "text_chunk",
        {"content": assistant_text, "chunk_seq": 0},
        turn_index=1,
        branch_id=branch_id,
    )
    store.append_event("alice", "processing_end", {}, turn_index=1, branch_id=branch_id)


def test_regen_only_collapses_into_one_user_group(tmp_path):
    """All branches share user content → one group, with all branches
    sitting under it as assistant-level regens."""
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    _add_branch(store, 1, "hi", "a1")
    _add_branch(store, 2, "hi", "a2")
    _add_branch(store, 3, "hi", "a3")

    groups = collect_user_groups(store.get_events("alice"))
    assert 1 in groups
    assert len(groups[1]["groups"]) == 1
    assert sorted(groups[1]["groups"][0]["branches"]) == [1, 2, 3]
    store.close(update_status=False)


def test_edit_only_splits_into_per_branch_groups(tmp_path):
    """Every branch has unique user content → one group per branch."""
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    _add_branch(store, 1, "hi", "a1")
    _add_branch(store, 2, "hello", "a2")
    _add_branch(store, 3, "hey", "a3")

    groups = collect_user_groups(store.get_events("alice"))
    assert len(groups[1]["groups"]) == 3
    contents = sorted(g["content"] for g in groups[1]["groups"])
    assert contents == ["hello", "hey", "hi"]
    store.close(update_status=False)


def test_mixed_edit_plus_regen_groups_correctly(tmp_path):
    """Two distinct user contents, one with two regens of the assistant."""
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    _add_branch(store, 1, "hi", "a1")
    _add_branch(store, 2, "hi", "a2")  # regen
    _add_branch(store, 3, "hello", "a3")  # edit
    _add_branch(store, 4, "hello", "a4")  # regen of edit

    groups = collect_user_groups(store.get_events("alice"))
    by_content = {g["content"]: g["branches"] for g in groups[1]["groups"]}
    assert sorted(by_content["hi"]) == [1, 2]
    assert sorted(by_content["hello"]) == [3, 4]
    # Default selection lands on the latest branch (4) → group "hello".
    selected_idx = groups[1]["selected_group_idx"]
    assert groups[1]["groups"][selected_idx]["content"] == "hello"
    store.close(update_status=False)


def test_branch_view_override_changes_selected_group(tmp_path):
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    _add_branch(store, 1, "hi", "a1")
    _add_branch(store, 2, "hello", "a2")

    groups = collect_user_groups(store.get_events("alice"), branch_view={1: 1})
    sel_idx = groups[1]["selected_group_idx"]
    assert groups[1]["groups"][sel_idx]["content"] == "hi"
    store.close(update_status=False)
