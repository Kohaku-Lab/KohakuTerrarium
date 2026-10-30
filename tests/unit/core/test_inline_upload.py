"""Tests for controller-owned upload materialization."""

from pathlib import Path

import pytest

from kohakuterrarium.core.controller.inline_upload import materialize_upload


class TestMaterializeUpload:
    def test_cached_copy_reused_only_while_bytes_match(self, tmp_path):
        first = materialize_upload(b"ORIGINAL", "note.txt", str(tmp_path), None)
        assert (
            materialize_upload(b"ORIGINAL", "note.txt", str(tmp_path), first) == first
        )
        Path(first).write_bytes(b"EDITED")
        second = materialize_upload(b"ORIGINAL", "note.txt", str(tmp_path), first)
        assert second != first
        assert Path(first).read_bytes() == b"EDITED"
        assert Path(second).read_bytes() == b"ORIGINAL"

    @pytest.mark.parametrize("state", ["missing", "directory"])
    def test_unusable_cache_path_is_not_overwritten(self, tmp_path, state):
        cached = tmp_path / "cached.txt"
        if state == "directory":
            cached.mkdir()
        rebuilt = Path(
            materialize_upload(b"bytes", "note.txt", str(tmp_path), str(cached))
        )
        assert rebuilt != cached
        assert rebuilt.read_bytes() == b"bytes"
        assert cached.is_dir() if state == "directory" else not cached.exists()

    def test_same_name_always_creates_unique_files_for_new_materialization(
        self, tmp_path
    ):
        first = materialize_upload(b"A", "note.txt", str(tmp_path), None)
        second = materialize_upload(b"B", "note.txt", str(tmp_path), None)
        assert first != second
        assert Path(first).read_bytes() == b"A"
        assert Path(second).read_bytes() == b"B"

    @pytest.mark.parametrize(
        "name",
        ["../nested/notes.txt", r"..\nested\notes.txt", "x" * 255 + ".txt", "???", ""],
    )
    def test_sanitized_name_stays_in_owned_directory(self, tmp_path, name):
        result = Path(materialize_upload(b"bytes", name, str(tmp_path), None))
        assert result.parent == tmp_path
        assert len(result.name.encode()) <= 255
        assert result.read_bytes() == b"bytes"

    def test_symlink_is_never_reused_or_overwritten(self, tmp_path):
        original = tmp_path / "editable.txt"
        original.write_bytes(b"ORIGINAL")
        cached = tmp_path / "alias.txt"
        try:
            cached.symlink_to(original)
        except OSError:
            pytest.skip("Creating symlinks requires platform permission")
        rebuilt = Path(
            materialize_upload(b"ORIGINAL", "note.txt", str(tmp_path), str(cached))
        )
        assert rebuilt != cached
        assert rebuilt.read_bytes() == b"ORIGINAL"
        assert cached.is_symlink()
        assert original.read_bytes() == b"ORIGINAL"
