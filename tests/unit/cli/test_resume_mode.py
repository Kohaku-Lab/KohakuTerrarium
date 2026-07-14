"""Unit tests for resume-mode routing + the kt-cli / kt-tui resume verb.

Pins two bugs:

- ``cli.resume._run`` must route ``io_mode`` to the RIGHT engine
  launcher — ``cli`` / ``plain`` mount the rich inline CLI, everything
  else the full-screen TUI. The old ``_run`` ignored ``io_mode`` and
  always called ``run_engine_with_tui``; the ``cli``/``plain`` cases
  below fail against that behaviour.
- ``kt-cli resume`` / ``kt-tui resume`` dispatch to ``resume_cli`` with
  the surface's fixed mode, and do NOT fall through to ``resolve_then_run``.

The engine + launchers are stubbed so the tests observe routing, not a
real resume.
"""

import asyncio
import sys
import types

import pytest

from kohakuterrarium.cli import entry_cli, entry_tui
from kohakuterrarium.cli import resume as resume_mod


class _FakeEngine:
    def __init__(self):
        self._topology = types.SimpleNamespace(graphs={"g1": object()})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeStore:
    def __init__(self, path):
        self.path = path
        self.closed = False

    def close(self, *a, **k):
        self.closed = True


class _FakeTerrarium:
    @staticmethod
    async def resume(store, *, pwd=None, llm=None):
        return _FakeEngine()


def _install_run_fakes(monkeypatch) -> list:
    """Stub the engine + both launchers; return the call-record list."""
    calls: list = []
    monkeypatch.setattr(resume_mod, "SessionStore", _FakeStore)
    monkeypatch.setattr(resume_mod, "Terrarium", _FakeTerrarium)
    monkeypatch.setattr(resume_mod, "_pick_focus", lambda engine, gid: "focus-1")

    async def fake_rich(engine, focus, store):
        calls.append(("rich", focus, store))

    async def fake_tui(engine, focus, store):
        calls.append(("tui", focus, store))

    monkeypatch.setattr(resume_mod, "run_engine_with_rich_cli", fake_rich)
    monkeypatch.setattr(resume_mod, "run_engine_with_tui", fake_tui)
    return calls


class TestResumeRunRouting:
    @pytest.mark.parametrize("io_mode", ["cli", "plain"])
    def test_cli_and_plain_route_to_rich(self, monkeypatch, io_mode):
        calls = _install_run_fakes(monkeypatch)
        rc = asyncio.run(resume_mod._run("s.kohakutr", None, None, io_mode))
        assert rc == 0
        assert [(name, focus) for name, focus, _ in calls] == [("rich", "focus-1")]

    @pytest.mark.parametrize("io_mode", ["tui", "none", None])
    def test_tui_and_default_route_to_tui(self, monkeypatch, io_mode):
        calls = _install_run_fakes(monkeypatch)
        rc = asyncio.run(resume_mod._run("s.kohakutr", None, None, io_mode))
        assert rc == 0
        assert [(name, focus) for name, focus, _ in calls] == [("tui", "focus-1")]

    def test_store_closed_after_run(self, monkeypatch):
        calls = _install_run_fakes(monkeypatch)
        asyncio.run(resume_mod._run("s.kohakutr", None, None, "cli"))
        _, _, store = calls[0]
        assert store.closed is True


def _install_entry_fakes(monkeypatch, module) -> dict:
    """Stub a front-door module's side-effecting deps; return the sink."""
    seen: dict = {}
    monkeypatch.setattr(module, "configure_utf8_stdio", lambda **_k: None)

    def fake_resume(query, pwd, log_level, **kw):
        seen["resume"] = dict(query=query, pwd=pwd, log_level=log_level, **kw)
        return 0

    def fake_run(agent_path, **kw):
        seen["run"] = dict(agent_path=agent_path, **kw)
        return 0

    monkeypatch.setattr(module, "resume_cli", fake_resume)
    monkeypatch.setattr(module, "resolve_then_run", fake_run)
    return seen


class TestFrontDoorResumeVerb:
    def test_kt_cli_resume_dispatches_cli_mode(self, monkeypatch):
        seen = _install_entry_fakes(monkeypatch, entry_cli)
        monkeypatch.setattr(sys, "argv", ["kt-cli", "resume", "mysess", "--last"])
        assert entry_cli.main() == 0
        assert "run" not in seen
        assert seen["resume"]["query"] == "mysess"
        assert seen["resume"]["last"] is True
        assert seen["resume"]["io_mode"] == "cli"

    def test_kt_tui_resume_dispatches_tui_mode(self, monkeypatch):
        seen = _install_entry_fakes(monkeypatch, entry_tui)
        monkeypatch.setattr(sys, "argv", ["kt-tui", "resume", "sess"])
        assert entry_tui.main() == 0
        assert "run" not in seen
        assert seen["resume"]["query"] == "sess"
        assert seen["resume"]["last"] is False
        assert seen["resume"]["io_mode"] == "tui"

    def test_kt_cli_without_resume_still_runs(self, monkeypatch):
        seen = _install_entry_fakes(monkeypatch, entry_cli)
        monkeypatch.setattr(sys, "argv", ["kt-cli", "foo"])
        assert entry_cli.main() == 0
        assert "resume" not in seen
        assert seen["run"]["agent_path"] == "foo"
        assert seen["run"]["io_mode"] == "cli"

    def test_kt_tui_without_resume_still_runs(self, monkeypatch):
        seen = _install_entry_fakes(monkeypatch, entry_tui)
        monkeypatch.setattr(sys, "argv", ["kt-tui"])
        assert entry_tui.main() == 0
        assert "resume" not in seen
        assert seen["run"]["agent_path"] is None
        assert seen["run"]["io_mode"] == "tui"
