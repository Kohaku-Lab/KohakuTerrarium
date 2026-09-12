"""Unit tests for :mod:`kohakuterrarium.utils.fs_path`."""

from pathlib import Path

import pytest

from kohakuterrarium.utils.fs_path import coerce_fs_path


class TestCoerceFsPath:
    def test_plain_relative_path(self):
        assert coerce_fs_path("sub/file.txt") == Path("sub/file.txt")

    def test_local_file_uri(self, tmp_path):
        target = (tmp_path / "a b.txt").resolve()
        assert coerce_fs_path(target.as_uri()) == target

    def test_recovers_pathlib_single_slash_form(self, tmp_path):
        target = (tmp_path / "x").resolve()
        mangled = str(Path(target.as_uri()))
        assert mangled.startswith("file:")
        assert not mangled.startswith("file:///")
        assert coerce_fs_path(mangled) == target

    def test_remote_file_uri_rejected(self):
        with pytest.raises(ValueError, match="unsupported file URI"):
            coerce_fs_path("file://host/share/x.png")

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            coerce_fs_path("")

    def test_mkdir_uses_named_path_not_cwd_file_scheme(self, tmp_path, monkeypatch):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        named = tmp_path / "real" / "sessions"
        coerce_fs_path(named.resolve().as_uri()).mkdir(parents=True, exist_ok=True)
        assert named.is_dir()
        assert not (cwd / "file:").exists()
