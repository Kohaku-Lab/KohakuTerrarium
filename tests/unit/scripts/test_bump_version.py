import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

TRACKED = (
    Path("pyproject.toml"),
    Path("src/kohakuterrarium/__init__.py"),
    Path("src/kohakuterrarium-frontend/package.json"),
    Path("src/kohakuterrarium-frontend/package-lock.json"),
)


@pytest.fixture
def fake_repo(repo_root, tmp_path):
    """A checkout holding only the version-carrying files, copied verbatim."""
    for relative in TRACKED:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo_root / relative, target)
    return tmp_path


@pytest.fixture
def bump(scripts_dir, fake_repo):
    """Run bump_version.py against the fixture checkout."""

    def _bump(version: str):
        return subprocess.run(
            [
                sys.executable,
                str(scripts_dir / "bump_version.py"),
                version,
                "--repo-root",
                str(fake_repo),
            ],
            capture_output=True,
            text=True,
        )

    return _bump


def _pyproject(fake_repo: Path) -> dict:
    return tomllib.loads((fake_repo / "pyproject.toml").read_text(encoding="utf-8"))


def _lock(fake_repo: Path) -> dict:
    return json.loads(
        (fake_repo / "src/kohakuterrarium-frontend/package-lock.json").read_text(
            encoding="utf-8"
        )
    )


def test_stamps_every_version_carrying_file(fake_repo, bump):
    result = bump("2.1.1")

    assert result.returncode == 0, result.stderr
    config = _pyproject(fake_repo)
    assert config["project"]["version"] == "2.1.1"
    assert config["tool"]["briefcase"]["version"] == "2.1.1"
    android = config["tool"]["briefcase"]["app"]["kohakuterrarium"]["android"]
    assert android["version_code"] == "20101000"

    init_py = (fake_repo / "src/kohakuterrarium/__init__.py").read_text(
        encoding="utf-8"
    )
    assert '__version__ = "2.1.1"' in init_py

    package = json.loads(
        (fake_repo / "src/kohakuterrarium-frontend/package.json").read_text(
            encoding="utf-8"
        )
    )
    assert package["version"] == "2.1.1"

    lock = _lock(fake_repo)
    assert lock["version"] == "2.1.1"
    assert lock["packages"][""]["version"] == "2.1.1"


def test_version_code_encodes_two_digit_minor_and_patch(fake_repo, bump):
    bump("3.12.34")

    android = _pyproject(fake_repo)["tool"]["briefcase"]["app"]["kohakuterrarium"][
        "android"
    ]
    assert android["version_code"] == "31234000"


def test_lockfile_dependency_versions_are_untouched(fake_repo, bump):
    def dependency_versions() -> dict:
        return {
            name: entry.get("version")
            for name, entry in _lock(fake_repo)["packages"].items()
            if name != ""
        }

    before = dependency_versions()
    bump("2.1.1")

    assert dependency_versions() == before


def test_lockfile_keeps_npm_formatting(fake_repo, bump):
    lock_path = fake_repo / "src/kohakuterrarium-frontend/package-lock.json"
    before = lock_path.read_text(encoding="utf-8")

    bump("2.1.1")

    after = lock_path.read_text(encoding="utf-8")
    assert after.endswith("}\n")
    assert after.count("\n") == before.count("\n")


def test_non_release_versions_are_rejected(fake_repo, bump):
    original = (fake_repo / "pyproject.toml").read_text(encoding="utf-8")

    result = bump("2.1.1.dev3")

    assert result.returncode != 0
    assert "not a release version" in result.stderr
    assert (fake_repo / "pyproject.toml").read_text(encoding="utf-8") == original


def test_repo_version_files_agree_with_pyproject(repo_root):
    config = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    version = config["project"]["version"]
    init_py = (repo_root / "src/kohakuterrarium/__init__.py").read_text(
        encoding="utf-8"
    )
    package = json.loads(
        (repo_root / "src/kohakuterrarium-frontend/package.json").read_text(
            encoding="utf-8"
        )
    )

    assert config["tool"]["briefcase"]["version"] == version
    assert f'__version__ = "{version}"' in init_py
    assert package["version"] == version
