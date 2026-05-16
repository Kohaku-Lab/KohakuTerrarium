"""Unit tests for :mod:`kohakuterrarium.studio.editors.yaml_manifest`."""

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from kohakuterrarium.studio.editors.yaml_manifest import (
    append_entry,
    ensure_list,
    entry_by_name,
    load_manifest,
    save_manifest,
)


class TestLoadManifest:
    def test_missing_returns_empty(self, tmp_path):
        out = load_manifest(tmp_path / "no-such.yaml")
        assert isinstance(out, CommentedMap)
        assert len(out) == 0

    def test_round_trip(self, tmp_path):
        path = tmp_path / "kohaku.yaml"
        path.write_text("name: test\nfoo: bar\n")
        out = load_manifest(path)
        assert out["name"] == "test"
        assert out["foo"] == "bar"

    def test_empty_file_returns_empty_map(self, tmp_path):
        path = tmp_path / "kohaku.yaml"
        path.write_text("")
        out = load_manifest(path)
        assert isinstance(out, CommentedMap)


class TestSaveManifest:
    def test_writes_dump(self, tmp_path):
        path = tmp_path / "kohaku.yaml"
        doc = CommentedMap()
        doc["name"] = "x"
        save_manifest(path, doc)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "name: x" in content

    def test_atomic_no_tmp_left(self, tmp_path):
        path = tmp_path / "kohaku.yaml"
        doc = CommentedMap()
        doc["foo"] = "bar"
        save_manifest(path, doc)
        # No .tmp sidecar should remain after a successful save.
        assert not (tmp_path / "kohaku.yaml.tmp").exists()


class TestEnsureList:
    def test_creates_list(self):
        doc = CommentedMap()
        seq = ensure_list(doc, "items")
        assert isinstance(seq, CommentedSeq)
        assert doc["items"] is seq

    def test_returns_existing_commented_seq(self):
        doc = CommentedMap()
        existing = CommentedSeq([1, 2])
        doc["items"] = existing
        seq = ensure_list(doc, "items")
        assert seq is existing

    def test_coerces_plain_list(self):
        doc = CommentedMap()
        doc["items"] = [1, 2]
        seq = ensure_list(doc, "items")
        assert isinstance(seq, CommentedSeq)
        assert list(seq) == [1, 2]


class TestEntryByName:
    def test_match(self):
        seq = CommentedSeq([{"name": "a", "x": 1}, {"name": "b", "x": 2}])
        out = entry_by_name(seq, "b")
        assert out["x"] == 2

    def test_no_match(self):
        seq = CommentedSeq([{"name": "a"}])
        assert entry_by_name(seq, "missing") is None

    def test_non_dict_entries_skipped(self):
        seq = CommentedSeq(["not a dict", {"name": "a"}])
        assert entry_by_name(seq, "a")["name"] == "a"


class TestAppendEntry:
    def test_appends_commented_map(self):
        seq = CommentedSeq()
        append_entry(seq, {"name": "x", "type": "plugin"})
        assert len(seq) == 1
        assert isinstance(seq[0], CommentedMap)
        assert seq[0]["name"] == "x"

    def test_preserves_key_order(self):
        seq = CommentedSeq()
        append_entry(seq, {"a": 1, "b": 2, "c": 3})
        keys = list(seq[0].keys())
        assert keys == ["a", "b", "c"]
