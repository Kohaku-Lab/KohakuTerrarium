"""Unit tests for ``utils/paths.py`` — the workspace safety layer."""

from pathlib import Path

import pytest

from kohakuterrarium.api.studio.utils.paths import (
    UnsafePath,
    ensure_in_root,
    sanitize_name,
)


class TestSanitizeName:
    def test_accepts_simple_name(self):
        assert sanitize_name("my_tool") == "my_tool"
        assert sanitize_name("SomeCreature") == "SomeCreature"

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            sanitize_name("")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError):
            sanitize_name(None)  # type: ignore[arg-type]

    def test_rejects_whitespace_wrap(self):
        with pytest.raises(ValueError):
            sanitize_name("  my_tool")
        with pytest.raises(ValueError):
            sanitize_name("my_tool  ")

    def test_rejects_leading_dot(self):
        with pytest.raises(ValueError):
            sanitize_name(".hidden")

    @pytest.mark.parametrize(
        "bad",
        [
            "sub/path",
            "sub\\path",
            "../escape",
            "a/../b",
        ],
    )
    def test_rejects_path_separators_and_parent(self, bad):
        with pytest.raises(ValueError):
            sanitize_name(bad)

    @pytest.mark.parametrize(
        "reserved",
        [
            "CON",
            "con",
            "prn",
            "AUX",
            "nul",
            "com1",
            "lpt9",
        ],
    )
    def test_rejects_windows_reserved(self, reserved):
        with pytest.raises(ValueError):
            sanitize_name(reserved)


class TestEnsureInRoot:
    def test_accepts_relative_inside(self, tmp_path: Path):
        target = ensure_in_root(tmp_path, "prompts/system.md")
        assert target == (tmp_path / "prompts" / "system.md").resolve()

    def test_accepts_nested_without_escape(self, tmp_path: Path):
        target = ensure_in_root(tmp_path, "a/b/c.txt")
        assert target.is_relative_to(tmp_path.resolve())

    def test_rejects_escape(self, tmp_path: Path):
        with pytest.raises(UnsafePath):
            ensure_in_root(tmp_path, "../escape")

    def test_rejects_absolute(self, tmp_path: Path):
        with pytest.raises(UnsafePath):
            ensure_in_root(tmp_path, str(tmp_path / "x.txt"))

    def test_rejects_empty(self, tmp_path: Path):
        with pytest.raises(UnsafePath):
            ensure_in_root(tmp_path, "")

    def test_accepts_forward_slashes_on_windows(self, tmp_path: Path):
        # Using forward-slash form on any platform
        target = ensure_in_root(tmp_path, "a/b/c.txt")
        assert target.parent.parent.name == "a"
