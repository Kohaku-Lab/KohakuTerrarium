"""Read-only session snapshots use the real SQLite and KohakuVault codecs."""

from contextlib import closing

import pytest

from kohakuterrarium.session.readonly_view import SessionReadView
from kohakuterrarium.session.store import SessionStore


def test_view_reads_wal_and_keeps_one_snapshot(tmp_path):
    path = tmp_path / "snapshot %20 #.kohakutr"
    with closing(SessionStore(path)) as store:
        store.init_meta("sid", "agent", "", "", ["alice"])
        store.meta["nested"] = {"items": [None, True, 4.5, "汉字"]}
        store.append_event("discovered", "user_input", {"content": "new agent"})
        store.append_event("alice:attached:helper:1", "text", {"content": "private"})
        store.flush()
        with SessionReadView(path.as_uri()) as reader:
            meta = reader.load_meta()
            assert meta["agents"] == ["alice", "discovered"]
            assert meta["nested"] == {"items": [None, True, 4.5, "汉字"]}
            store.meta["nested"] = {"items": ["new value"]}
            assert reader.get("meta", "nested") == meta["nested"]
            assert reader.get("state", "missing", "default") == "default"
            assert (
                list(reader.items("events", prefix="discovered:e"))[0][1]["content"]
                == "new agent"
            )
            with pytest.raises(ValueError, match="unsupported session table"):
                reader.get("other", "key")
        with SessionReadView(path) as reader:
            assert reader.get("meta", "nested") == {"items": ["new value"]}


def test_missing_view_does_not_create_database(tmp_path):
    path = tmp_path / "missing.kohakutr"
    with pytest.raises(FileNotFoundError):
        SessionReadView(path)
    assert not path.exists()
