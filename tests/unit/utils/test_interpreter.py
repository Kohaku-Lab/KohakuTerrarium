"""Interpreter resolution for child processes."""

import sys

import pytest

from kohakuterrarium.launcher.interpreter_mode import FLAG, STUB_ENV
from kohakuterrarium.utils import interpreter


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (interpreter.PYTHON_ENV, STUB_ENV, "KT_LAUNCHER_EXEC"):
        monkeypatch.delenv(name, raising=False)


def test_regular_interpreter_is_used_as_is():
    assert interpreter.executable_is_stub() is False
    assert interpreter.python_command() == [sys.executable]


def test_stub_executable_is_addressed_through_its_flag(monkeypatch):
    monkeypatch.setenv(STUB_ENV, sys.executable)
    assert interpreter.executable_is_stub() is True
    assert interpreter.python_command() == [sys.executable, FLAG]


def test_stub_marker_for_a_different_executable_is_ignored(monkeypatch):
    monkeypatch.setenv(STUB_ENV, sys.executable + ".other")
    assert interpreter.executable_is_stub() is False
    assert interpreter.python_command() == [sys.executable]


def test_path_file_beside_the_executable_marks_a_stub(monkeypatch, tmp_path):
    exe = tmp_path / "App.exe"
    exe.write_text("", encoding="utf-8")
    (tmp_path / "python313._pth").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(exe))
    assert interpreter.executable_is_stub() is True
    assert interpreter.python_command() == [str(exe), FLAG]


def test_explicit_override_wins_over_stub_detection(monkeypatch):
    monkeypatch.setenv(STUB_ENV, sys.executable)
    monkeypatch.setenv(interpreter.PYTHON_ENV, "/opt/bundled/python")
    assert interpreter.python_command() == ["/opt/bundled/python"]


def test_launcher_marker_alone_is_a_briefcase_runtime_but_not_a_stub(monkeypatch):
    monkeypatch.setenv("KT_LAUNCHER_EXEC", "1")
    assert interpreter.is_briefcase_runtime() is True
    assert interpreter.executable_is_stub() is False
    assert interpreter.python_command() == [sys.executable]


def test_plain_runtime_is_not_briefcase():
    assert interpreter.is_briefcase_runtime() is False
