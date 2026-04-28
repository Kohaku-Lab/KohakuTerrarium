"""Validation around the studio sessions ``_normalize_pwd`` helper.

Originally exercised ``serving.manager._normalize_pwd``; that module
was deleted in Phase 3 of the studio cleanup. The studio sessions
lifecycle module now owns the same helper with identical semantics.
"""

from pathlib import Path

import pytest

from kohakuterrarium.studio.sessions import lifecycle as lifecycle_mod


def test_normalize_pwd_rejects_missing_directory(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ValueError, match="does not exist"):
        lifecycle_mod._normalize_pwd(str(missing))


def test_normalize_pwd_rejects_file_path(tmp_path: Path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        lifecycle_mod._normalize_pwd(str(file_path))


def test_normalize_pwd_returns_resolved_directory(tmp_path: Path):
    child = tmp_path / "child"
    child.mkdir()
    assert lifecycle_mod._normalize_pwd(str(child)) == str(child.resolve())
