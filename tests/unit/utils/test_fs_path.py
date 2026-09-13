"""Unit tests for :mod:`kohakuterrarium.utils.fs_path`."""

import os
from pathlib import Path, PureWindowsPath

import pytest

from kohakuterrarium.utils.fs_path import coerce_fs_path


class TestCoerceFsPath:
    def test_plain_relative_path(self):
        assert coerce_fs_path("sub/file.txt") == Path("sub/file.txt")

    def test_local_file_uri(self, tmp_path):
        target = (tmp_path / "a b.txt").resolve()
        assert coerce_fs_path(target.as_uri()) == target

    @pytest.mark.parametrize(
        "name", ["image%20name.png", "image%25name.png", "image%2Fname.png"]
    )
    def test_file_uri_preserves_literal_percent_escapes(self, tmp_path, name):
        target = (tmp_path / name).resolve()
        assert coerce_fs_path(target.as_uri()) == target

    @pytest.mark.skipif(os.name != "nt", reason="UNC paths require Windows")
    @pytest.mark.parametrize(
        "name", ["image%20name.png", "image%25name.png", "image%2Fname.png"]
    )
    def test_remote_file_uri_preserves_literal_percent_escapes(self, name):
        target = Path(r"\\host\share") / name
        assert coerce_fs_path(target.as_uri()) == target

    def test_recovers_pathlib_single_slash_form(self, tmp_path):
        target = (tmp_path / "x").resolve()
        mangled = str(Path(target.as_uri()))
        assert mangled.startswith("file:")
        assert not mangled.startswith("file:///")
        assert coerce_fs_path(mangled) == target

    def test_windows_drive_triple_slash_uri(self):
        got = coerce_fs_path("file:///C:/Users/me/x.png")
        assert got.name == "x.png"
        assert "Users" in got.parts
        assert got.as_posix().endswith("C:/Users/me/x.png")

    def test_windows_pathlib_mangled_backslash_form(self):
        got = coerce_fs_path(r"file:\C:\Users\me\x.png")
        assert got.name == "x.png"
        assert "Users" in got.parts
        assert got.as_posix().endswith("C:/Users/me/x.png")

    @pytest.mark.parametrize("name", ["a b.png", "日本語.png", "image%20name.png"])
    def test_windows_pathlib_form_decodes_once(self, name):
        target = PureWindowsPath("C:/Users/me") / name
        mangled = str(PureWindowsPath(target.as_uri()))
        assert coerce_fs_path(mangled).as_posix() == target.as_posix()

    @pytest.mark.skipif(os.name != "nt", reason="UNC paths require Windows")
    def test_windows_pathlib_form_preserves_unc_anchor(self):
        target = Path(r"\\server\share\a b%20.png")
        assert coerce_fs_path(Path(target.as_uri())) == target

    def test_remote_file_uri(self):
        raw = "file://host/share/x.png"
        if os.name == "nt":
            assert coerce_fs_path(raw) == Path(r"\\host\share\x.png")
        else:
            with pytest.raises(ValueError, match="unsupported file URI"):
                coerce_fs_path(raw)

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
