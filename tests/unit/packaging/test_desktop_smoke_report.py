"""The installed-app self-check judges the report the shell writes."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[3] / "packaging" / "desktop" / "installed_smoke.py"
)


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location("installed_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(**overrides) -> dict:
    report = {
        "ok": True,
        "pageLoaded": True,
        "ipcFromAppPage": True,
        "unauthenticatedStatus": 401,
        "healthStatus": 200,
        "overlayWindow": {"opened": True, "pageLoaded": True, "ipc": True},
        "tray": {"installed": True, "badgeShown": True, "badgeCleared": True},
    }
    report.update(overrides)
    return report


def test_a_healthy_report_has_no_problems(smoke):
    assert smoke.check_report(_report()) == []


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"overlayWindow": None}, "could not open an overlay window"),
        ({"overlayWindow": {"opened": False}}, "could not open an overlay window"),
        (
            {"overlayWindow": {"opened": True, "pageLoaded": True, "ipc": False}},
            "did not run the app page",
        ),
        (
            {"overlayWindow": {"opened": True, "pageLoaded": False, "ipc": True}},
            "did not run the app page",
        ),
        ({"tray": None}, "did not create a tray icon"),
        ({"tray": {"installed": False}}, "did not create a tray icon"),
        (
            {"tray": {"installed": True, "badgeShown": True, "badgeCleared": False}},
            "badge could not be updated",
        ),
        ({"unauthenticatedStatus": 200}, "expected 401"),
        ({"ipcFromAppPage": False}, "could not call a native command"),
    ],
)
def test_each_failure_is_named(smoke, overrides, fragment):
    problems = smoke.check_report(_report(**overrides))
    assert any(fragment in problem for problem in problems), problems
