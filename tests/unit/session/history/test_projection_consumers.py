"""Shared physical event projection across Replay-compatible history consumers."""

from types import SimpleNamespace

from kohakuterrarium.builtins.outputs.stdout import _group_resume_events as stdout_turns
from kohakuterrarium.builtins.tui.resume import _group_into_turns as tui_turns
from kohakuterrarium.core.agent.history.message_history import (
    live_user_turns,
    user_message_content_for_turn,
)
from kohakuterrarium.session.history.replay import (
    collect_user_groups,
    project_live_event_stream,
    select_live_event_ids,
)
from kohakuterrarium.session.readonly.reader import SessionReader
from kohakuterrarium.session.resume.branch import backfill_turn_metadata
from kohakuterrarium.terrarium.engine.cli.output import (
    _group_resume_events as engine_turns,
)


def _mirrored():
    """Same (turn, branch) *and* logical IDs under two root ancestries."""

    def event(eid, kind, content, turn, branch, path):
        return {
            "event_id": eid,
            "type": kind,
            "content": content,
            "turn_index": turn,
            "branch_id": branch,
            "parent_branch_path": path,
        }

    return [
        event(1, "user_input", "old root", 1, 1, []),
        event(11, "user_message", "old root", 1, 1, []),
        event(20, "user_input", "old child", 2, 1, [[1, 1]]),
        event(21, "user_message", "old child", 2, 1, [[1, 1]]),
        event(30, "text", "old reply", 2, 1, [[1, 1]]),
        event(2, "user_input", "new root", 1, 2, []),
        event(12, "user_message", "new root", 1, 2, []),
        event(20, "user_input", "new child", 2, 1, [[1, 2]]),
        event(21, "user_message", "new child", 2, 1, [[1, 2]]),
        event(30, "text", "new reply", 2, 1, [[1, 2]]),
    ]


def test_physical_projection_does_not_resurrect_old_mirror_from_reused_ids():
    rows = _mirrored()
    projected = project_live_event_stream(rows)
    assert [r["content"] for r in projected if r["type"] != "user_message"] == [
        "new root",
        "new child",
        "new reply",
    ]
    # The old public ID-set API cannot disambiguate these physical copies.
    assert {20, 21, 30}.issubset(select_live_event_ids(rows))
    assert [
        r["content"]
        for r in project_live_event_stream(rows, branch_view={1: 1, 2: 1})
        if r["type"] == "user_input"
    ] == ["old root", "old child"]


def test_reader_and_resume_surfaces_agree_on_mirrored_rounds():
    rows = _mirrored()
    reader = object.__new__(SessionReader)
    reader.events = lambda agent=None: rows
    turns = reader.turns("alice")
    assert [turn.user_text for turn in turns] == ["new root", "new child"]
    assert turns[-1].assistant_text == "new reply"

    for group in (stdout_turns, engine_turns):
        turns = group(rows)
        assert [turn["user"] for turn in turns] == ["new root", "new child"]
        assert turns[-1]["text"] == "new reply"

    tui = tui_turns(rows)
    assert [turn["input"] for turn in tui] == ["new root", "new child"]
    assert tui[-1]["steps"][-1] == ("text", "new reply")


def test_edit_lookup_resume_backfill_and_group_metadata_share_lineage():
    rows = _mirrored()
    agent = SimpleNamespace(
        config=SimpleNamespace(name="alice"),
        session_store=SimpleNamespace(get_events=lambda name: rows),
    )
    assert live_user_turns(agent) == [1, 2]
    assert user_message_content_for_turn(agent, 2) == "new child"
    assert (
        user_message_content_for_turn(agent, 2, branch_view={1: 1, 2: 1}) == "old child"
    )

    selected_groups = collect_user_groups(rows)
    assert [g["content"] for g in selected_groups[2]["groups"]] == ["new child"]
    old_groups = collect_user_groups(rows, branch_view={1: 1, 2: 1})
    assert [g["content"] for g in old_groups[2]["groups"]] == ["old child"]

    snapshot = [
        {"role": "user", "content": "new root"},
        {"role": "user", "content": "new child"},
    ]
    backfilled = backfill_turn_metadata(snapshot, rows)
    assert [
        (m["metadata"]["turn_index"], m["metadata"]["branch_id"]) for m in backfilled
    ] == [
        (1, 2),
        (2, 1),
    ]


def test_lossy_id_keyed_parent_cache_cannot_reproject_mirrors():
    from kohakuterrarium.session.history.replay import index_parent_paths

    rows = _mirrored()
    lossy = index_parent_paths(rows)
    # The original logical-ID cache cannot represent both physical copies
    # of child 2:1. Source selection must disregard that cache when IDs
    # collide, including when it was explicitly supplied by an old caller.
    assert project_live_event_stream(
        rows, branch_view={1: 1, 2: 1}, parent_paths=lossy
    ) == project_live_event_stream(rows, branch_view={1: 1, 2: 1})
    assert project_live_event_stream(
        rows, branch_view={1: 2, 2: 1}, parent_paths=lossy
    ) == project_live_event_stream(rows, branch_view={1: 2, 2: 1})
