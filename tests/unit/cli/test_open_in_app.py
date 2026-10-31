"""kt open: hand paths to the desktop app the shell recorded."""

import json
import subprocess

import pytest

from kohakuterrarium.cli import open_in_app


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config").mkdir()
    return tmp_path


def _record(home, exe):
    (home / "config" / open_in_app.SHELL_RECORD).write_text(
        json.dumps({"exe": str(exe)}), encoding="utf-8"
    )


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda argv, **kwargs: calls.append((argv, kwargs))
    )
    return calls


def test_the_app_is_unknown_until_the_shell_has_recorded_itself(home):
    assert open_in_app.shell_executable() is None
    _record(home, home / "missing.exe")
    assert open_in_app.shell_executable() is None
    exe = home / "app.exe"
    exe.write_text("x")
    _record(home, exe)
    assert open_in_app.shell_executable() == exe


def test_a_corrupt_record_means_no_app(home):
    (home / "config" / open_in_app.SHELL_RECORD).write_text("{nope", encoding="utf-8")
    assert open_in_app.shell_executable() is None
    (home / "config" / open_in_app.SHELL_RECORD).write_text("[1]", encoding="utf-8")
    assert open_in_app.shell_executable() is None


def test_folders_and_session_files_are_handed_over_as_absolute_paths(home, launched):
    exe = home / "app.exe"
    exe.write_text("x")
    _record(home, exe)
    folder = home / "work"
    folder.mkdir()
    session = home / "chat.kohakutr"
    session.write_text("x")

    assert open_in_app.open_cli([str(folder), str(session)]) == 0
    argv, kwargs = launched[0]
    assert argv == [str(exe), str(folder.resolve()), str(session.resolve())]
    assert kwargs["stdin"] is subprocess.DEVNULL


def test_other_files_and_missing_paths_are_refused_before_anything_starts(
    home, launched
):
    exe = home / "app.exe"
    exe.write_text("x")
    _record(home, exe)
    notes = home / "notes.txt"
    notes.write_text("x")

    assert open_in_app.open_cli([str(notes)]) == 2
    assert open_in_app.open_cli([str(home / "nope")]) == 2
    assert launched == []


def test_without_the_app_nothing_is_launched_and_the_reason_is_printed(
    home, launched, capsys
):
    assert open_in_app.open_cli([str(home)]) == 1
    assert "desktop app" in capsys.readouterr().err
    assert launched == []


def test_the_command_is_registered_and_dispatches(home, launched):
    from kohakuterrarium.cli import _main

    args = _main._build_parser().parse_args(["open", str(home)])
    assert args.command == "open"
    assert _main.COMMANDS["open"] is open_in_app.open_dispatch
