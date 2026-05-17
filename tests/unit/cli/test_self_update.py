"""``kt self-update`` install-kind detection + dispatch."""

import argparse
import sys
from pathlib import Path

import pytest

from kohakuterrarium.cli import self_update as _u


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _make_args(**kw):
    defaults = {
        "dry_run": False,
        "source": None,
        "spec": None,
        "check_only": False,
    }
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class TestDetectInstallKind:
    def test_editable_install_detected(self, monkeypatch):
        # `pip show kohakuterrarium` is run as a subprocess — fake it
        # so the test stays hermetic.
        class _Result:
            stdout = "Name: kohakuterrarium\nEditable project location: /me/checkout\n"

        monkeypatch.setattr(_u.subprocess, "run", lambda *a, **kw: _Result())
        # Force the wrapper-marker probe to MISS by pointing it at a
        # path that doesn't exist.
        monkeypatch.setattr(
            _u, "wrapper_marker_path", lambda: Path("/nonexistent/marker")
        )
        # pipx env not set.
        monkeypatch.delenv("PIPX_HOME", raising=False)
        assert _u._detect_install_kind() == "editable"

    def test_non_editable_user_install(self, monkeypatch, tmp_path):
        # pip show returns nothing relevant; not under /usr/bin.
        class _Result:
            stdout = "Name: kohakuterrarium\nVersion: 1.5.0\n"

        monkeypatch.setattr(_u.subprocess, "run", lambda *a, **kw: _Result())
        monkeypatch.setattr(
            _u, "wrapper_marker_path", lambda: tmp_path / "nope" / "marker"
        )
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.setattr(sys, "executable", "/home/me/.venv/bin/python")
        assert _u._detect_install_kind() == "user"

    def test_pipx_via_envvar(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PIPX_HOME", "/home/me/.local/pipx")
        monkeypatch.setattr(
            _u, "wrapper_marker_path", lambda: tmp_path / "nope" / "marker"
        )
        assert _u._detect_install_kind() == "pipx"

    def test_system_install_detected(self, monkeypatch, tmp_path):
        class _Result:
            stdout = ""

        monkeypatch.setattr(_u.subprocess, "run", lambda *a, **kw: _Result())
        monkeypatch.setattr(
            _u, "wrapper_marker_path", lambda: tmp_path / "nope" / "marker"
        )
        monkeypatch.delenv("PIPX_HOME", raising=False)
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3.13")
        assert _u._detect_install_kind() == "system"


class TestRefusalPaths:
    def test_editable_refuses_with_hint(self, monkeypatch, capsys):
        monkeypatch.setattr(_u, "_detect_install_kind", lambda: "editable")
        rc = _u.self_update_cli(_make_args())
        out = capsys.readouterr().out
        assert rc == 2
        assert "managed by 'editable'" in out
        assert "git pull" in out

    def test_system_refuses_with_hint(self, monkeypatch, capsys):
        monkeypatch.setattr(_u, "_detect_install_kind", lambda: "system")
        rc = _u.self_update_cli(_make_args())
        out = capsys.readouterr().out
        assert rc == 2
        assert "system package manager" in out


class TestDryRun:
    def test_dry_run_in_wrapper_prints_and_does_not_install(self, monkeypatch, capsys):
        monkeypatch.setattr(_u, "_detect_install_kind", lambda: "wrapper")

        def _should_not_be_called():
            raise AssertionError("dry-run must not call run_update()")

        monkeypatch.setattr(_u, "run_update", lambda: _should_not_be_called())
        rc = _u.self_update_cli(_make_args(dry_run=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert "--dry-run" in out
        assert "pip install" in out

    def test_dry_run_user_install_does_not_invoke_pip(self, monkeypatch, capsys):
        monkeypatch.setattr(_u, "_detect_install_kind", lambda: "user")
        called = {"pip": False}

        def _fake_run(*a, **kw):
            called["pip"] = True

        monkeypatch.setattr(_u.subprocess, "run", _fake_run)
        rc = _u.self_update_cli(_make_args(dry_run=True))
        assert rc == 0
        assert called["pip"] is False
