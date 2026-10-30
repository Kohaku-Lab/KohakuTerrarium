"""Unit tests for :mod:`kohakuterrarium.session.vault.read`."""

from contextlib import closing

import pytest
from kohakuvault import KVault, TextVault

import kohakuterrarium.session.vault.read as vault_read
from kohakuterrarium.session.vault.handles import dispose, is_disposed
from kohakuterrarium.session.vault.read import (
    AbsentTable,
    open_read_table,
    open_read_table_or_absent,
    open_read_text,
    wal_path,
)


def _family(path):
    return sorted(p.name for p in path.parent.iterdir())


def _closed_file(path):
    with closing(KVault(str(path), table="meta")) as meta:
        meta.enable_auto_pack()
        meta.put_many([("a", 1), ("b", {"x": 2})])
    return path


class TestOpenReadTable:
    def test_checkpointed_file_opens_immutable_without_sidecars(self, tmp_path):
        path = _closed_file(tmp_path / "s.kohakutr")
        before = (_family(path), path.read_bytes())
        vault = open_read_table(path, "meta", snapshot=True)
        try:
            assert vault.get_many(["a", "b"]) == [1, {"x": 2}]
            with pytest.raises(Exception):
                vault["c"] = 3
        finally:
            dispose(vault)
        assert (_family(path), path.read_bytes()) == before

    def test_live_writer_rows_are_visible_after_commit(self, tmp_path):
        path = tmp_path / "live.kohakutr"
        writer = KVault(str(path), table="meta")
        writer.enable_auto_pack()
        try:
            writer["a"] = 1
            assert wal_path(path).exists()
            reader = open_read_table(path, "meta", snapshot=True)
            try:
                assert reader.get("a") == 1
                writer["b"] = 2
                assert reader.get("b") == 2
            finally:
                dispose(reader)
        finally:
            writer.close()

    def test_wal_shared_reader_sees_a_writer_that_starts_later(self, tmp_path):
        path = _closed_file(tmp_path / "s.kohakutr")
        reader = open_read_table(path, "meta", snapshot=False)
        try:
            writer = KVault(str(path), table="meta")
            writer.enable_auto_pack()
            try:
                writer.put_many([(f"k{i}", i) for i in range(300)])
                writer.checkpoint()
                assert reader.get("k299") == 299
                assert reader.get("a") == 1
            finally:
                writer.close()
            assert reader.get("k150") == 150
        finally:
            dispose(reader)

    def test_snapshot_reader_follows_wal_when_a_writer_is_live(self, tmp_path):
        path = _closed_file(tmp_path / "s.kohakutr")
        writer = KVault(str(path), table="meta")
        writer.enable_auto_pack()
        try:
            writer["late"] = "row"
            reader = open_read_table(path, "meta", snapshot=True)
            try:
                assert reader.get("late") == "row"
            finally:
                dispose(reader)
        finally:
            writer.close()

    def test_missing_table_is_none(self, tmp_path):
        path = _closed_file(tmp_path / "s.kohakutr")
        assert open_read_table(path, "events", snapshot=True) is None
        assert open_read_table(path, "events", snapshot=False) is None

    def test_other_open_errors_propagate(self, tmp_path):
        path = tmp_path / "garbage.kohakutr"
        path.write_bytes(b"not a database at all")
        with pytest.raises(Exception, match="not a database"):
            open_read_table(path, "meta", snapshot=True)

    def test_failed_setup_closes_handle(self, tmp_path, monkeypatch):
        path = _closed_file(tmp_path / "s.kohakutr")
        opened = []

        class Failing(KVault):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                opened.append(self)

            def enable_auto_pack(self, *args, **kwargs):
                raise RuntimeError("setup failed")

        monkeypatch.setattr(vault_read, "KVault", Failing)
        with pytest.raises(RuntimeError, match="setup failed"):
            open_read_table(path, "meta", snapshot=True)
        assert len(opened) == 1 and is_disposed(opened[0])

    def test_reader_options_reach_the_handle(self, tmp_path, monkeypatch):
        path = _closed_file(tmp_path / "s.kohakutr")
        seen = []
        real = vault_read.KVault

        def recording(target, **kwargs):
            seen.append(kwargs)
            return real(target, **kwargs)

        monkeypatch.setattr(vault_read, "KVault", recording)
        vault = open_read_table(
            path, "meta", snapshot=True, reader_idle_limit=1, cache_kb=256
        )
        try:
            assert vault.get("a") == 1
            assert seen == [
                {
                    "table": "meta",
                    "read_only": True,
                    "reader_idle_limit": 1,
                    "cache_kb": 256,
                }
            ]
            assert vault.reader_pool_stats()["idle_connections"] <= 1
        finally:
            dispose(vault)


class TestOpenReadText:
    def test_checkpointed_file_is_searched_immutably(self, tmp_path):
        path = tmp_path / "s.kohakutr"
        fts = TextVault(str(path), table="fts")
        fts.enable_auto_pack()
        fts.insert("persisted needle text", {"k": 1})
        dispose(fts)
        before = (_family(path), path.read_bytes())
        reader = open_read_text(path, "fts", snapshot=True)
        try:
            assert [meta for _, _, meta in reader.search("needle")] == [{"k": 1}]
            with pytest.raises(Exception, match="readonly"):
                reader.insert("written through a reader", {})
        finally:
            dispose(reader)
        assert (_family(path), path.read_bytes()) == before

    def test_live_writer_rows_are_visible(self, tmp_path):
        path = tmp_path / "live.kohakutr"
        writer = TextVault(str(path), table="fts")
        writer.enable_auto_pack()
        try:
            writer.insert("first needle", {"k": 1})
            reader = open_read_text(path, "fts", snapshot=True)
            try:
                writer.insert("second needle", {"k": 2})
                assert sorted(m["k"] for _, _, m in reader.search("needle")) == [1, 2]
            finally:
                dispose(reader)
        finally:
            dispose(writer)

    def test_missing_table_is_none_and_never_created(self, tmp_path):
        path = _closed_file(tmp_path / "s.kohakutr")
        before = path.read_bytes()
        assert open_read_text(path, "fts", snapshot=True) is None
        assert path.read_bytes() == before

    def test_other_errors_propagate(self, tmp_path):
        path = tmp_path / "garbage.kohakutr"
        path.write_bytes(b"not a database at all")
        with pytest.raises(Exception, match="not a database"):
            open_read_text(path, "fts", snapshot=True)


class TestAbsentTable:
    def test_reads_empty_and_rejects_writes(self, tmp_path):
        path = _closed_file(tmp_path / "s.kohakutr")
        table = open_read_table_or_absent(path, "events", snapshot=True)
        assert isinstance(table, AbsentTable) and table.name == "events"
        assert table.get("k", "d") == "d"
        assert "k" not in table and len(table) == 0
        with pytest.raises(KeyError):
            table["k"]
        assert list(table.keys()) == list(table.items()) == list(table.values()) == []
        assert table.get_many(["a", "b"], 0) == [0, 0]
        assert table.flush_cache() == 0
        assert table.checkpoint() is None and table.enable_auto_pack() is None
        for write in (
            lambda: table.put("k", 1),
            lambda: table.__setitem__("k", 1),
            lambda: table.put_many([("k", 1)]),
            lambda: table.delete("k"),
            lambda: table.update({"k": 1}),
            lambda: table.clear(),
        ):
            with pytest.raises(PermissionError):
                write()
        assert not is_disposed(table)
        dispose(table)
        assert is_disposed(table)

    def test_present_table_is_a_real_handle(self, tmp_path):
        path = _closed_file(tmp_path / "s.kohakutr")
        table = open_read_table_or_absent(path, "meta", snapshot=False)
        try:
            assert isinstance(table, KVault) and table.get("a") == 1
        finally:
            dispose(table)
