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
        "menu": {"built": True, "topLevel": 6, "expected": 6},
        "popoutWindow": {"opened": True, "pageLoaded": True, "ipc": True},
        "tray": {
            "installed": True,
            "badgeShown": True,
            "badgeCleared": True,
            "menuShown": True,
            "menuCleared": True,
        },
        "backendRestart": {
            "restarted": True,
            "pidChanged": True,
            "sameToken": True,
            "healthStatus": 200,
            "memoryMeasured": True,
        },
        "diagnostics": {
            "planted": True,
            "written": True,
            "hasInfo": True,
            "hasLog": True,
            "leaked": False,
        },
        "runtimeRepair": {
            "repaired": True,
            "pointerRewritten": True,
            "sameVersion": True,
            "pidChanged": True,
            "healthStatus": 200,
        },
        "summonShortcut": {"registered": True, "released": True},
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
        ({"menu": None}, "menu could not be built"),
        ({"menu": {"built": True, "topLevel": 3, "expected": 6}}, "menu could not"),
        ({"popoutWindow": None}, "could not open a session window"),
        (
            {"popoutWindow": {"opened": True, "pageLoaded": True, "ipc": False}},
            "session window did not run",
        ),
        ({"tray": None}, "did not create a tray icon"),
        ({"tray": {"installed": False}}, "did not create a tray icon"),
        (
            {"tray": {"installed": True, "badgeShown": True, "badgeCleared": False}},
            "badge could not be updated",
        ),
        (
            {
                "tray": {
                    "installed": True,
                    "badgeShown": True,
                    "badgeCleared": True,
                    "menuShown": True,
                    "menuCleared": False,
                }
            },
            "tray menu could not be rebuilt",
        ),
        ({"runtimeRepair": None}, "runtime could not be repaired"),
        (
            {
                "runtimeRepair": {
                    "repaired": True,
                    "pointerRewritten": False,
                    "sameVersion": True,
                    "pidChanged": True,
                    "healthStatus": 200,
                }
            },
            "repaired runtime is not healthy",
        ),
        ({"diagnostics": None}, "diagnostics bundle could not be built"),
        (
            {
                "diagnostics": {
                    "planted": True,
                    "written": True,
                    "hasInfo": True,
                    "hasLog": True,
                    "leaked": True,
                }
            },
            "kept a credential",
        ),
        (
            {
                "diagnostics": {
                    "planted": True,
                    "written": True,
                    "hasInfo": True,
                    "hasLog": False,
                    "leaked": False,
                }
            },
            "kept a credential or lost the log",
        ),
        ({"backendRestart": None}, "backend could not be restarted"),
        (
            {"backendRestart": {"restarted": False}},
            "backend could not be restarted",
        ),
        (
            {
                "backendRestart": {
                    "restarted": True,
                    "pidChanged": False,
                    "sameToken": True,
                    "healthStatus": 200,
                    "memoryMeasured": True,
                }
            },
            "restarted backend is not healthy",
        ),
        (
            {
                "backendRestart": {
                    "restarted": True,
                    "pidChanged": True,
                    "sameToken": False,
                    "healthStatus": 200,
                    "memoryMeasured": True,
                }
            },
            "restarted backend is not healthy",
        ),
        ({"summonShortcut": None}, "could not be registered"),
        (
            {"summonShortcut": {"registered": True, "released": False}},
            "could not be registered",
        ),
        ({"unauthenticatedStatus": 200}, "expected 401"),
        ({"ipcFromAppPage": False}, "could not call a native command"),
    ],
)
def test_each_failure_is_named(smoke, overrides, fragment):
    problems = smoke.check_report(_report(**overrides))
    assert any(fragment in problem for problem in problems), problems
