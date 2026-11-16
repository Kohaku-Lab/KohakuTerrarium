"""Stage M1: all-branch memory indexing without changing live retrieval defaults."""

import numpy as np

from kohakuterrarium.session.memory.blocks import extract_batch
from kohakuterrarium.session.memory.embedding import BaseEmbedder
from kohakuterrarium.session.memory.index import SessionMemory


class CountingEmbedder(BaseEmbedder):
    dimensions = 4

    def __init__(self):
        self.batches = []

    def encode(self, texts):
        self.batches.append(tuple(texts))
        return np.ones((len(texts), 4), dtype=np.float32)

    def encode_one(self, text):
        return np.ones(4, dtype=np.float32)


def _branch_events():
    return [
        {
            "type": "user_input",
            "event_id": 1,
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
            "content": "alpha needle",
        },
        {
            "type": "user_input",
            "event_id": 2,
            "turn_index": 1,
            "branch_id": 2,
            "parent_branch_path": [],
            "content": "beta needle",
        },
        {
            "type": "text",
            "event_id": 3,
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
            "content": "alpha reply",
        },
        {
            "type": "text",
            "event_id": 4,
            "turn_index": 1,
            "branch_id": 2,
            "parent_branch_path": [],
            "content": "beta reply",
        },
        {
            "type": "processing_end",
            "event_id": 5,
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "type": "processing_end",
            "event_id": 6,
            "turn_index": 1,
            "branch_id": 2,
        },
        {
            "type": "user_input",
            "event_id": 7,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 2]],
            "content": "gamma needle",
        },
        {
            "type": "text",
            "event_id": 8,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 2]],
            "content": "gamma reply",
        },
    ]


def test_all_branch_extraction_preserves_interleaved_turn_and_source_identity():
    events = _branch_events()
    full, _ = extract_batch("alice", events, all_branches=True)
    assert [b.content for b in full] == [
        "alpha needle",
        "beta needle",
        "alpha reply",
        "beta reply",
        "gamma needle",
        "gamma reply",
    ]
    assert [(b.round_num, b.block_num) for b in full] == [
        (1, 0),
        (1, 0),
        (1, 1),
        (1, 1),
        (2, 0),
        (2, 1),
    ]
    assert [(b.event_id, b.event_position, b.part_index) for b in full] == [
        (1, 0, 0),
        (2, 1, 0),
        (3, 2, 0),
        (4, 3, 0),
        (7, 6, 0),
        (8, 7, 0),
    ]
    assert [(b.turn_index, b.branch_id) for b in full] == [
        (1, 1),
        (1, 2),
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 1),
    ]
    assert full[-1].parent_branch_path == ((1, 2),)

    checkpoint, incremental = None, []
    for end in range(1, len(events) + 1):
        part, checkpoint = extract_batch(
            "alice", events[:end], checkpoint, all_branches=True
        )
        incremental.extend(part)
    assert incremental == full
    assert extract_batch("alice", events, checkpoint, all_branches=True)[0] == []


def test_all_branch_append_never_reencodes_existing_sibling(tmp_path):
    events = _branch_events()
    initial = [events[0], events[2], events[4]]
    append = [events[1], events[3], events[5], events[6], events[7]]
    # A branch can be regenerated later in the append-only event stream.
    memory = SessionMemory(
        str(tmp_path / "all.kohakutr"),
        CountingEmbedder(),
        index_all_branches=True,
    )
    try:
        embedder = memory._embedder
        assert memory.index_events("alice", initial) == 2
        generation = memory._generation("alice")
        assert embedder.batches == [("alpha needle", "alpha reply")]
        assert memory.index_events("alice", initial + append) == 4
        assert memory._generation("alice") == generation
        assert embedder.batches == [
            ("alpha needle", "alpha reply"),
            ("beta needle", "beta reply", "gamma needle", "gamma reply"),
        ]
        assert memory.get_stats()["fts_blocks"] == 6
        assert memory.get_stats()["vec_blocks"] == 6
        assert {r.content for r in memory.search("needle", mode="fts")} == {
            "alpha needle",
            "beta needle",
            "gamma needle",
        }
        assert memory.index_events("alice", initial + append) == 0
        assert len(embedder.batches) == 2
    finally:
        memory.close()


def test_selected_index_migrates_to_all_branches_and_rebuilds_once(tmp_path):
    path = str(tmp_path / "upgrade.kohakutr")
    rows = [
        {
            "type": "user_input",
            "content": "old needle",
            "event_id": 1,
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "type": "user_input",
            "content": "new needle",
            "event_id": 2,
            "turn_index": 1,
            "branch_id": 2,
        },
    ]
    previous = SessionMemory(path, CountingEmbedder())
    try:
        previous.index_events("alice", rows)
        previous_generation = previous._generation("alice")
        assert [r.content for r in previous.search("needle", mode="fts")] == [
            "new needle"
        ]
        assert previous._state.get("alice:index_format", 1) == 1
    finally:
        previous.close()

    upgraded_embedder = CountingEmbedder()
    upgraded = SessionMemory(path, upgraded_embedder, index_all_branches=True)
    try:
        assert upgraded.index_events("alice", rows) == 2
        assert upgraded._state["alice:index_format"] == 2
        assert upgraded._generation("alice") > previous_generation
        for mode in ("fts", "semantic", "hybrid"):
            assert {r.content for r in upgraded.search("needle", mode=mode)} == {
                "old needle",
                "new needle",
            }
        assert upgraded.index_events("alice", rows) == 0
        assert upgraded.get_stats()["fts_blocks"] == 2
        assert upgraded.get_stats()["vec_blocks"] == 2
        assert upgraded_embedder.batches == [("old needle", "new needle")]
    finally:
        upgraded.close()


def test_all_branch_inherits_explicit_lineage_for_unstamped_sibling_output():
    rows = [
        {
            "type": "user_input",
            "event_id": 1,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 2]],
            "content": "start",
        },
        {
            "type": "text",
            "event_id": 2,
            "turn_index": 2,
            "branch_id": 1,
            "content": "next",
        },
    ]
    first, checkpoint = extract_batch("alice", rows[:1], all_branches=True)
    rest, _ = extract_batch("alice", rows, checkpoint, all_branches=True)
    assert first[0].parent_branch_path == ((1, 2),)
    assert rest[0].parent_branch_path == ((1, 2),)
    assert rest[0].block_num == 1


def test_extraction_scope_mismatch_rejects_reused_checkpoint():
    import pytest

    from kohakuterrarium.session.memory.blocks import RebuildRequired

    rows = [{"type": "user_input", "content": "needle", "event_id": 1}]
    _, selected = extract_batch("alice", rows)
    _, full = extract_batch("alice", rows, all_branches=True)
    with pytest.raises(RebuildRequired, match="scope changed"):
        extract_batch("alice", rows, selected, all_branches=True)
    with pytest.raises(RebuildRequired, match="scope changed"):
        extract_batch("alice", rows, full)


def test_all_branch_checkpoint_reopens_with_stable_identity(tmp_path):
    path = str(tmp_path / "reopen.kohakutr")
    rows = [
        {
            "type": "user_input",
            "content": "first needle",
            "event_id": 1,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 2]],
        },
    ]
    first = SessionMemory(path, index_all_branches=True)
    try:
        assert first.index_events("alice", rows) == 1
    finally:
        first.close()

    later = SessionMemory(path, index_all_branches=True)
    try:
        rows.append(
            {
                "type": "text",
                "content": "second needle",
                "event_id": 2,
                "turn_index": 2,
                "branch_id": 1,
            }
        )
        assert later.index_events("alice", rows) == 1
        found = {r.content: r for r in later.search("needle", mode="fts")}
        assert found["first needle"].event_position == 0
        assert found["second needle"].event_position == 1
        assert found["second needle"].round_num == 2
        assert found["second needle"].block_num == 1
        assert found["second needle"].parent_branch_path == ((1, 2),)
    finally:
        later.close()
