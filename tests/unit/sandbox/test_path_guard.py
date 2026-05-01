from pathlib import Path

from kohakuterrarium.utils.file_guard import PathBoundaryGuard


def test_path_guard_warns_once_for_outside_path(tmp_path: Path) -> None:
    guard = PathBoundaryGuard(tmp_path, mode="warn")
    outside = tmp_path.parent / "outside.txt"

    assert guard.check(str(outside)) is not None
    assert guard.check(str(outside)) is None


def test_path_guard_allows_inside_path(tmp_path: Path) -> None:
    guard = PathBoundaryGuard(tmp_path, mode="warn")

    assert guard.check(str(tmp_path / "file.txt")) is None
