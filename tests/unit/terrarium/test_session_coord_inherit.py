"""Verify split/merge inherit the resumable subset of parent meta.

Pre-fix bug: ``split_session_store`` / ``merge_session_stores`` only
stamped ``session_id`` + ``parent_session_ids`` + ``split_at`` /
``merged_at`` on the new store(s).  The new file then had no
``config_type`` / ``config_path`` / ``config_snapshot`` / ``pwd`` —
so a resume off the split file failed with "Session has no
config_path or config_snapshot in metadata".
"""

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.session_coord import (
    merge_session_stores,
    split_session_store,
)


def test_split_inherits_resumable_meta(tmp_path):
    parent_path = tmp_path / "parent.kohakutr"
    parent = SessionStore(str(parent_path))
    parent.init_meta(
        session_id="g1",
        config_type="agent",
        config_path="/cfg/path",
        pwd="/work",
        agents=["alice"],
        config_snapshot={"name": "alice", "system_prompt": "hi"},
    )
    child_paths = [tmp_path / "child_a.kohakutr", tmp_path / "child_b.kohakutr"]
    children = split_session_store(parent, child_paths)
    try:
        for child in children:
            meta = child.load_meta()
            assert meta.get("config_type") == "agent"
            assert meta.get("config_path") == "/cfg/path"
            assert meta.get("config_snapshot", {}).get("name") == "alice"
            # Split bookkeeping is preserved.
            assert meta.get("split_at") is not None
            assert meta.get("parent_session_ids")
    finally:
        for c in children:
            c.close()
        parent.close()


def test_merge_inherits_resumable_meta_from_first_old_store(tmp_path):
    a = SessionStore(str(tmp_path / "a.kohakutr"))
    a.init_meta(
        session_id="g1",
        config_type="agent",
        config_path="/cfg/a",
        pwd="/work",
        agents=["alice"],
        config_snapshot={"name": "alice"},
    )
    b = SessionStore(str(tmp_path / "b.kohakutr"))
    b.init_meta(
        session_id="g2",
        config_type="agent",
        config_path="/cfg/b",
        pwd="/work",
        agents=["bob"],
        config_snapshot={"name": "bob"},
    )
    merged_path = tmp_path / "merged.kohakutr"
    merged = merge_session_stores([a, b], str(merged_path))
    try:
        meta = merged.load_meta()
        # First store's config wins for the resumable subset.
        assert meta.get("config_type") == "agent"
        assert meta.get("config_path") == "/cfg/a"
        assert meta.get("config_snapshot", {}).get("name") == "alice"
        assert meta.get("merged_at") is not None
        # Parent lineage covers BOTH old stores.
        parents = list(meta.get("parent_session_ids") or [])
        assert set(parents) == {"g1", "g2"}
    finally:
        merged.close()
        a.close()
        b.close()
