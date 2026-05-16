"""Behavior tests for :mod:`kohakuterrarium.studio.attach.pty_router`.

Only the platform-agnostic resolver helpers are exercised here.
``pty_session`` itself dispatches into ``pty_posix`` / ``pty_windows``
(the ConPTY / Unix-pty backends carved out in ``tests/README.md`` as
platform-specific), so it is not unit-tested — the dispatch *decision*
is asserted via a stubbed backend instead.

Contract:

* ``_find_shell`` returns a real, resolvable shell path for the current
  platform, falling back to a sentinel only when nothing is on PATH;
* ``_session_cwd`` reads the creature's executor working dir and falls
  back to the server CWD when the executor doesn't advertise one;
* ``pty_session`` routes to the Windows backend on win32 and the POSIX
  backend otherwise.
"""

import os
import shutil
import sys
from types import SimpleNamespace

import pytest

from kohakuterrarium.studio.attach import pty_router


class TestFindShell:
    def test_returns_an_existing_executable(self):
        shell = pty_router._find_shell()
        # Whatever the platform, the returned path must be a usable shell:
        # either an absolute path that exists, or a bare name resolvable
        # on PATH (cmd.exe / sh sentinel fallbacks).
        assert shell
        resolvable = (
            (os.path.isabs(shell) and os.path.exists(shell))
            or shutil.which(shell) is not None
            or shell in ("sh", "cmd.exe")
        )
        assert resolvable

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell lookup")
    def test_posix_prefers_a_real_shell_on_path(self):
        shell = pty_router._find_shell()
        # On POSIX one of bash/sh/zsh must exist and be returned.
        assert os.path.basename(shell) in ("bash", "sh", "zsh")

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only fallback path")
    def test_posix_falls_back_to_sh_when_path_empty(self, monkeypatch):
        monkeypatch.setattr(pty_router.shutil, "which", lambda _name: None)
        # No shell on PATH → documented 'sh' sentinel.
        assert pty_router._find_shell() == "sh"


class TestSessionCwd:
    def test_reads_executor_working_dir(self, tmp_path):
        holder = SimpleNamespace(
            agent=SimpleNamespace(executor=SimpleNamespace(_working_dir=str(tmp_path)))
        )
        assert pty_router._session_cwd(holder) == str(tmp_path)

    def test_falls_back_to_server_cwd_without_working_dir(self):
        # Executor exists but exposes no _working_dir attribute.
        holder = SimpleNamespace(agent=SimpleNamespace(executor=SimpleNamespace()))
        assert pty_router._session_cwd(holder) == os.getcwd()

    def test_falls_back_when_agent_has_no_executor(self):
        holder = SimpleNamespace(agent=SimpleNamespace())
        assert pty_router._session_cwd(holder) == os.getcwd()

    def test_none_working_dir_falls_back(self):
        holder = SimpleNamespace(
            agent=SimpleNamespace(executor=SimpleNamespace(_working_dir=None))
        )
        assert pty_router._session_cwd(holder) == os.getcwd()


class TestPtySessionDispatch:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX dispatch branch")
    async def test_posix_routes_to_posix_backend(self, monkeypatch):
        from kohakuterrarium.studio.attach import pty_posix

        called = {}

        async def _fake(ws, cwd):
            called["ws"] = ws
            called["cwd"] = cwd

        monkeypatch.setattr(pty_posix, "pty_session", _fake)
        await pty_router.pty_session("WS", "/tmp/work")
        # The router handed the WS + cwd straight to the POSIX backend.
        assert called == {"ws": "WS", "cwd": "/tmp/work"}
