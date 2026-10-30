"""Unit tests for :mod:`kohakuterrarium.session.vault.io`."""

from contextlib import closing

import pytest
from kohakuvault import KVault

import kohakuterrarium.session.vault.io as vault_io
from kohakuterrarium.session.vault.io import (
    copy_rows,
    key_text,
    read_prefix,
    read_values,
    write_rows,
)


def _table(path, name="t") -> KVault:
    table = KVault(str(path), table=name)
    table.enable_auto_pack()
    return table


class _Poisoned:
    """Delegates to a KVault, failing reads of ``bad`` keys like a corrupt row."""

    def __init__(self, inner, bad):
        self.inner, self.bad, self.many_calls = inner, set(bad), 0

    def keys(self, *args, **kwargs):
        return self.inner.keys(*args, **kwargs)

    def get_many(self, keys, default=None):
        self.many_calls += 1
        if self.bad.intersection(key_text(key) for key in keys):
            raise ValueError("chunk holds a corrupt row")
        return self.inner.get_many(keys, default)

    def __getitem__(self, key):
        if key_text(key) in self.bad:
            raise ValueError(f"corrupt {key_text(key)}")
        return self.inner[key]


def test_key_text():
    assert key_text(b"a:e1") == "a:e1"
    assert key_text("a:e1") == "a:e1"
    assert key_text(b"\xff") == "�"


class TestReadValues:
    def test_present_keys_in_input_order_missing_skipped(self, tmp_path):
        with closing(_table(tmp_path / "s.db")) as table:
            table.put_many([("b", 2), ("a", 1), ("c", None)])
            rows = list(read_values(table, ["c", "absent", "a", "b"]))
            assert rows == [("c", None), ("a", 1), ("b", 2)]
            assert list(read_values(table, [])) == []

    def test_reads_in_chunks(self, tmp_path, monkeypatch):
        with closing(_table(tmp_path / "s.db")) as table:
            table.put_many([(f"k{i:02d}", i) for i in range(10)])
            calls = []
            real = KVault.get_many

            def many(vault, keys, *args, **kwargs):
                calls.append(len(keys))
                return real(vault, keys, *args, **kwargs)

            monkeypatch.setattr(vault_io, "READ_CHUNK", 4)
            monkeypatch.setattr(KVault, "get_many", many)
            keys = [f"k{i:02d}" for i in range(10)]
            assert [v for _, v in read_values(table, keys)] == list(range(10))
            assert calls == [4, 4, 2]

    def test_chunks_grow_from_first_to_full_size(self, tmp_path, monkeypatch):
        with closing(_table(tmp_path / "s.db")) as table:
            table.put_many([(f"k{i:03d}", i) for i in range(600)])
            calls = []
            real = KVault.get_many

            def many(vault, keys, *args, **kwargs):
                calls.append(len(keys))
                return real(vault, keys, *args, **kwargs)

            monkeypatch.setattr(KVault, "get_many", many)
            assert len(list(read_prefix(table))) == 600
            assert calls == [16, 32, 64, 128, 256, 104]

    def test_unreadable_row_skipped_and_reported(self, tmp_path):
        with closing(_table(tmp_path / "s.db")) as inner:
            inner.put_many([("a", 1), ("b", 2), ("c", 3)])
            table = _Poisoned(inner, {"b"})
            seen = []
            rows = read_values(
                table,
                ["a", "b", "c", "gone"],
                on_unreadable=lambda k, e: seen.append(k),
            )
            assert list(rows) == [("a", 1), ("c", 3)]
            assert seen == ["b"]

    def test_default_reporter_logs(self, tmp_path, caplog):
        with closing(_table(tmp_path / "s.db")) as inner:
            inner["b"] = 2
            rows = list(read_values(_Poisoned(inner, {"b"}), ["b"], what="probe"))
            assert rows == []
            assert "Failed to read probe" in caplog.text

    def test_strict_propagates(self, tmp_path):
        with closing(_table(tmp_path / "s.db")) as inner:
            inner.put_many([("a", 1), ("b", 2)])
            with pytest.raises(ValueError, match="corrupt row"):
                list(read_values(_Poisoned(inner, {"b"}), ["a", "b"], strict=True))

    def test_lazy_per_chunk(self, tmp_path, monkeypatch):
        with closing(_table(tmp_path / "s.db")) as inner:
            inner.put_many([(f"k{i}", i) for i in range(6)])
            table = _Poisoned(inner, set())
            monkeypatch.setattr(vault_io, "READ_CHUNK", 2)
            rows = read_values(table, [f"k{i}" for i in range(6)])
            assert next(rows) == ("k0", 0)
            assert table.many_calls == 1


class TestReadPrefix:
    def test_prefix_and_whole_table(self, tmp_path):
        with closing(_table(tmp_path / "s.db")) as table:
            table.put_many([("a:e1", 1), ("a:e2", 2), ("b:e1", 3)])
            assert [key_text(k) for k, _ in read_prefix(table, "a:")] == [
                "a:e1",
                "a:e2",
            ]
            assert [v for _, v in read_prefix(table)] == [1, 2, 3]

    def test_no_row_cap(self, tmp_path):
        with closing(_table(tmp_path / "s.db")) as table:
            table.put_many([(f"e{i:06d}", i) for i in range(12_345)])
            rows = list(read_prefix(table, "e"))
            assert len(rows) == 12_345
            assert (key_text(rows[-1][0]), rows[-1][1]) == ("e012344", 12_344)


class TestWriteAndCopy:
    def test_write_rows_is_one_batch(self, tmp_path, monkeypatch):
        with closing(_table(tmp_path / "s.db")) as table:
            assert write_rows(table, []) == 0
            batches = []
            real = KVault.put_many

            def put_many(vault, rows):
                batches.append(len(rows))
                return real(vault, rows)

            monkeypatch.setattr(KVault, "put_many", put_many)
            assert write_rows(table, [("a", 1), ("b", 2)]) == 2
            assert batches == [2]
            assert table.get_many(["a", "b"]) == [1, 2]

    def test_write_rows_falls_back_per_row(self, caplog):
        class Partial:
            def __init__(self):
                self.rows = {}

            def put_many(self, rows):
                raise ValueError("batch rejected")

            def __setitem__(self, key, value):
                if key == "bad":
                    raise ValueError("row rejected")
                self.rows[key] = value

        dst = Partial()
        assert write_rows(dst, [("a", 1), ("bad", 2), ("c", 3)], what="probe") == 2
        assert dst.rows == {"a": 1, "c": 3}
        assert "Failed to write probe" in caplog.text

    def test_copy_rows_all_and_selected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vault_io, "READ_CHUNK", 3)
        with (
            closing(_table(tmp_path / "s.db", "src")) as src,
            closing(_table(tmp_path / "s.db", "dst")) as dst,
            closing(_table(tmp_path / "s.db", "sel")) as sel,
        ):
            rows = [(f"k{i}", {"i": i}) for i in range(8)]
            src.put_many(rows)
            assert copy_rows(src, dst) == 8
            assert [(key_text(k), v) for k, v in dst.items()] == rows
            assert copy_rows(src, sel, ["k3", "missing", "k1"]) == 2
            assert sorted(key_text(k) for k in sel.keys()) == ["k1", "k3"]
