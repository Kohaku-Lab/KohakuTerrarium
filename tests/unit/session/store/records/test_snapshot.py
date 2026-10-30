from pathlib import Path
from kohakuterrarium.session.store.records.snapshot import (
    load_branch_snapshot,
    load_records,
)
from kohakuterrarium.session.store import SessionStore


def test_save_load_branch_snapshot_lifecycle(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "s.kohakutr"))
    msgs_a = [
        {
            "role": "user",
            "content": "u1",
            "metadata": {"turn_index": 1, "branch_id": 1},
        },
        {"role": "assistant", "content": "a1"},
        {
            "role": "user",
            "content": "u2",
            "metadata": {"turn_index": 2, "branch_id": 1},
        },
    ]
    store.save_branch_snapshot(
        "alice",
        msgs_a,
        branch={"turn_index": 2, "branch_id": 1, "parent_branch_path": [[1, 1]]},
        watermark=20,
        compact_state={"compact_count": 1, "last_compact_time": 100.0},
    )
    msgs_b = [
        {
            "role": "user",
            "content": "u1",
            "metadata": {"turn_index": 1, "branch_id": 1},
        },
        {
            "role": "user",
            "content": "u2_b",
            "metadata": {"turn_index": 2, "branch_id": 2},
        },
    ]
    store.save_branch_snapshot(
        "alice",
        msgs_b,
        branch={"turn_index": 2, "branch_id": 2, "parent_branch_path": [[1, 1]]},
        watermark=25,
        compact_state={"compact_count": 1, "last_compact_time": 100.0},
    )

    records = load_records(store, "alice")
    assert [r["path"] for r in records] == [[[1, 1], [2, 1]], [[1, 1], [2, 2]]]

    records[0]["messages"][0]["content"] = "mutated"
    assert load_records(store, "alice")[0]["messages"][0]["content"] == "u1"

    snap_a = load_branch_snapshot(store, "alice", {1: 1, 2: 1})
    assert snap_a is not None and len(snap_a["messages"]) == 3
    assert snap_a["path"] == [[1, 1], [2, 1]]

    b_a3 = {"turn_index": 3, "branch_id": 1, "parent_branch_path": [[1, 1], [2, 1]]}
    store.save_branch_snapshot("alice", msgs_a, branch=b_a3, watermark=30)
    recs3 = load_records(store, "alice")
    assert [r["path"] for r in recs3] == [[[1, 1], [2, 2]], [[1, 1], [2, 1], [3, 1]]]

    store.save_conversation("alice", [{"role": "user", "content": "legacy"}])
    assert load_records(store, "alice") == []
    store.close()
