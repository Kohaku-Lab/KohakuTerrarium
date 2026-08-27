import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

NIGHTLY_VERSION = "2.0.0.dev20260601030000+abcdef0"


@pytest.fixture
def pyproject(repo_root, tmp_path):
    """A throwaway copy of the real pyproject.toml."""
    path = tmp_path / "pyproject.toml"
    path.write_text(
        (repo_root / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return path


@pytest.fixture
def wheel_dir(tmp_path):
    """Factory placing wheel filenames into a find-links directory."""

    def _make(*names: str) -> Path:
        wheels = tmp_path / "wheels"
        wheels.mkdir(exist_ok=True)
        for name in names:
            (wheels / name).write_bytes(b"")
        return wheels

    return _make


@pytest.fixture
def pin(scripts_dir, pyproject):
    """Run pin_briefcase_local_wheel.py against the throwaway pyproject."""

    def _pin(wheels: Path, *args: str):
        return subprocess.run(
            [
                sys.executable,
                str(scripts_dir / "pin_briefcase_local_wheel.py"),
                "--pyproject",
                str(pyproject),
                "--wheel-dir",
                str(wheels),
                *args,
            ],
            capture_output=True,
            text=True,
        )

    return _pin


def _desktop_requires(pyproject: Path) -> dict[str, list[str]]:
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    app = config["tool"]["briefcase"]["app"]["kohakuterrarium"]
    return {
        name: [r for r in (app[name].get("requires") or []) if "desktop" in r]
        for name in ("macOS", "windows", "linux")
    }


def test_pins_every_platform_to_the_local_wheel_version(pyproject, wheel_dir, pin):
    wheels = wheel_dir(f"kohakuterrarium-{NIGHTLY_VERSION}-py3-none-any.whl")

    result = pin(wheels)

    assert result.returncode == 0, result.stderr
    for platform, entries in _desktop_requires(pyproject).items():
        assert entries == [
            f"KohakuTerrarium[desktop]=={NIGHTLY_VERSION}"
        ], f"{platform} not pinned"


def test_the_launcher_pywebview_pin_is_left_alone(pyproject, wheel_dir, pin):
    pin(wheel_dir(f"kohakuterrarium-{NIGHTLY_VERSION}-py3-none-any.whl"))

    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    app = config["tool"]["briefcase"]["app"]["kohakuterrarium"]
    assert "pywebview==6.2.1" in app["requires"]


def test_rerunning_repins_instead_of_stacking_specifiers(pyproject, wheel_dir, pin):
    wheels = wheel_dir(f"kohakuterrarium-{NIGHTLY_VERSION}-py3-none-any.whl")

    pin(wheels)
    result = pin(wheels, "--version", "2.1.1")

    assert result.returncode == 0, result.stderr
    assert _desktop_requires(pyproject)["windows"] == [
        "KohakuTerrarium[desktop]==2.1.1"
    ]


def test_explicit_version_wins_over_wheel_discovery(pyproject, wheel_dir, pin):
    result = pin(wheel_dir(), "--version", "2.1.1")

    assert result.returncode == 0, result.stderr
    assert _desktop_requires(pyproject)["macOS"] == ["KohakuTerrarium[desktop]==2.1.1"]


def test_missing_wheel_fails_loudly(pyproject, wheel_dir, pin):
    result = pin(wheel_dir("proxy_tools-0.1.0-py3-none-any.whl"))

    assert result.returncode != 0
    assert "no kohakuterrarium wheel" in result.stderr
    assert "KohakuTerrarium[desktop]==" not in pyproject.read_text(encoding="utf-8")


def test_conflicting_wheel_versions_fail_loudly(pyproject, wheel_dir, pin):
    result = pin(
        wheel_dir(
            "kohakuterrarium-2.1.0-py3-none-any.whl",
            "kohakuterrarium-2.1.1-py3-none-any.whl",
        )
    )

    assert result.returncode != 0
    assert "multiple kohakuterrarium versions" in result.stderr
    assert "KohakuTerrarium[desktop]==" not in pyproject.read_text(encoding="utf-8")


def test_pyproject_without_the_requirement_fails_loudly(
    scripts_dir, wheel_dir, tmp_path
):
    bare = tmp_path / "bare.toml"
    bare.write_text('[project]\nname = "x"\n', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(scripts_dir / "pin_briefcase_local_wheel.py"),
            "--pyproject",
            str(bare),
            "--wheel-dir",
            str(wheel_dir("kohakuterrarium-2.1.1-py3-none-any.whl")),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "no KohakuTerrarium[desktop] requirement" in result.stderr


def test_repo_pyproject_ships_the_unpinned_requirement(repo_root):
    config = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    app = config["tool"]["briefcase"]["app"]["kohakuterrarium"]

    for name in ("macOS", "windows", "linux"):
        assert "KohakuTerrarium[desktop]" in app[name]["requires"]
