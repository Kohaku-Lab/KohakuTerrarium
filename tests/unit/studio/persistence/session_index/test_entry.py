"""Unit tests for ``session_index.entry`` — every code path."""

from pathlib import Path


from kohakuterrarium.studio.persistence.session_index.entry import (
    SCHEMA_VERSION,
    SessionIndexEntry,
)


def _touch(tmp_path: Path, name: str, size: int = 100) -> Path:
    p = tmp_path / name
    p.write_bytes(b"x" * size)
    return p


class TestFromMeta:
    def test_minimal_meta(self, tmp_path):
        path = _touch(tmp_path, "alice.kohakutr.v2")
        e = SessionIndexEntry.from_meta(
            path=path, meta={}, preview="", has_vector_index=False
        )
        # Defaults / coercions
        assert e.filename == "alice.kohakutr.v2"
        assert e.name == "alice"
        assert e.config_type == "unknown"
        assert e.format_version == 1
        assert e.agents == []
        assert e.parent_session_id is None
        assert e.fork_point is None
        assert e.migrated_from_version is None
        assert e.has_vector_index is False
        # File fingerprint pulled from stat()
        assert e.file_size == 100
        assert e.file_mtime > 0

    def test_full_meta(self, tmp_path):
        path = _touch(tmp_path, "bob.kohakutr.v2")
        meta = {
            "config_type": "terrarium",
            "config_path": "/p/cfg.yaml",
            "agents": ["bob", "carol"],
            "pwd": "/work",
            "status": "running",
            "last_active": "2026-05-01T00:00:00",
            "created_at": "2026-04-30T00:00:00",
            "format_version": 2,
            "on_node": "worker-1",
            "terrarium_name": "research_team",
            "lineage": {
                "fork": {"parent_session_id": "alice", "fork_point": 3},
                "migration": {"source_version": 1},
            },
            "forked_children": [
                {"session_id": "child1"},
                {"session_id": "child2"},
                "raw-string-id",
            ],
        }
        e = SessionIndexEntry.from_meta(
            path=path,
            meta=meta,
            preview="hello world",
            has_vector_index=True,
        )
        assert e.config_type == "terrarium"
        assert e.agents == ["bob", "carol"]
        assert e.node_id == "worker-1"
        assert e.terrarium_name == "research_team"
        assert e.preview == "hello world"
        assert e.has_vector_index is True
        assert e.parent_session_id == "alice"
        assert e.fork_point == 3
        assert e.migrated_from_version == 1
        # Mixed dict / string children — both pass through.
        assert e.forked_children == ["child1", "child2", "raw-string-id"]

    def test_lineage_non_dict_skipped(self, tmp_path):
        # Production data has been seen with ``lineage: []`` (legacy
        # rows).  Must not crash and must yield default None values.
        path = _touch(tmp_path, "x.kohakutr")
        e = SessionIndexEntry.from_meta(
            path=path,
            meta={"lineage": []},
            preview="",
            has_vector_index=False,
        )
        assert e.parent_session_id is None
        assert e.fork_point is None
        assert e.migrated_from_version is None

    def test_lineage_dict_missing_fork(self, tmp_path):
        path = _touch(tmp_path, "x.kohakutr")
        e = SessionIndexEntry.from_meta(
            path=path,
            meta={"lineage": {"migration": {"source_version": 7}}},
            preview="",
            has_vector_index=False,
        )
        assert e.parent_session_id is None
        assert e.fork_point is None
        assert e.migrated_from_version == 7

    def test_forked_children_with_none_entry(self, tmp_path):
        path = _touch(tmp_path, "x.kohakutr")
        e = SessionIndexEntry.from_meta(
            path=path,
            meta={"forked_children": [None, {"session_id": "c1"}, None, "raw"]},
            preview="",
            has_vector_index=False,
        )
        # None entries are dropped; dicts and strings survive.
        assert e.forked_children == ["c1", "raw"]

    def test_explicit_fingerprint_skips_stat(self, tmp_path):
        # When file_mtime + file_size are passed, we don't stat.
        # Verify by using a path that doesn't exist.
        path = tmp_path / "ghost.kohakutr"
        e = SessionIndexEntry.from_meta(
            path=path,
            meta={},
            preview="",
            has_vector_index=False,
            file_mtime=42.0,
            file_size=999,
        )
        assert e.file_mtime == 42.0
        assert e.file_size == 999

    def test_partial_fingerprint_falls_back_to_stat(self, tmp_path):
        # If only one of mtime/size is supplied, the other comes
        # from stat().
        path = _touch(tmp_path, "y.kohakutr", size=256)
        e = SessionIndexEntry.from_meta(
            path=path,
            meta={},
            preview="",
            has_vector_index=False,
            file_mtime=1.5,  # explicit
        )
        assert e.file_mtime == 1.5
        assert e.file_size == 256  # from stat

        e2 = SessionIndexEntry.from_meta(
            path=path,
            meta={},
            preview="",
            has_vector_index=False,
            file_size=10,  # explicit
        )
        assert e2.file_size == 10
        assert e2.file_mtime > 0  # from stat


class TestSerialization:
    def _sample(self) -> SessionIndexEntry:
        return SessionIndexEntry(
            filename="alice.kohakutr.v2",
            name="alice",
            file_mtime=1.0,
            file_size=42,
            preview="hello",
            config_path="/cfg",
            agents=["a1", "a2"],
            pwd="/work",
            config_type="agent",
            status="paused",
            last_active="2026-01-01",
            created_at="2026-01-01",
            format_version=2,
            node_id="",
            terrarium_name="",
            has_vector_index=True,
        )

    def test_to_search_columns_keys_match_schema(self):
        from kohakuterrarium.studio.persistence.session_index.store import (
            SEARCH_COLUMNS,
        )

        cols = self._sample().to_search_columns()
        assert set(cols.keys()) == set(SEARCH_COLUMNS)
        # ``agents`` is joined as a single string.
        assert cols["agents"] == "a1 a2"

    def test_to_listing_dict_strips_internal(self):
        e = self._sample()
        e._search_rowid = 7
        d = e.to_listing_dict()
        assert "_search_rowid" not in d
        assert d["name"] == "alice"

    def test_to_dict_keeps_internal(self):
        e = self._sample()
        e._search_rowid = 13
        d = e.to_dict()
        assert d["_search_rowid"] == 13

    def test_from_dict_roundtrip(self):
        e = self._sample()
        e._search_rowid = 99
        d = e.to_dict()
        e2 = SessionIndexEntry.from_dict(d)
        assert e2.filename == e.filename
        assert e2.agents == e.agents
        assert e2._search_rowid == 99

    def test_from_dict_tolerates_missing_optional_fields(self):
        # An older schema row missing the optional fields must still
        # rehydrate with sensible defaults.
        bare = {
            "filename": "x.kohakutr",
            "name": "x",
            "file_mtime": 1.0,
            "file_size": 1,
            "preview": "",
            "config_path": "",
            "agents": [],
            "pwd": "",
            "config_type": "agent",
            "status": "",
            "last_active": "",
            "created_at": "",
            "format_version": 1,
            "node_id": "",
        }
        e = SessionIndexEntry.from_dict(bare)
        assert e.terrarium_name == ""
        assert e.has_vector_index is False
        assert e.forked_children == []
        assert e._search_rowid == 0

    def test_fingerprint(self):
        e = self._sample()
        assert e.fingerprint() == (1.0, 42)


def test_schema_version_is_an_int():
    assert isinstance(SCHEMA_VERSION, int)
    assert SCHEMA_VERSION >= 1
