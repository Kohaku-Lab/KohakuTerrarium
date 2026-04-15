from pathlib import Path

import pytest

from kohakuterrarium.serving import manager as manager_mod


def test_normalize_pwd_rejects_missing_directory(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(ValueError, match="does not exist"):
        manager_mod._normalize_pwd(str(missing))


def test_normalize_pwd_rejects_file_path(tmp_path: Path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        manager_mod._normalize_pwd(str(file_path))


def test_normalize_pwd_returns_resolved_directory(tmp_path: Path):
    child = tmp_path / "child"
    child.mkdir()
    assert manager_mod._normalize_pwd(str(child)) == str(child.resolve())
