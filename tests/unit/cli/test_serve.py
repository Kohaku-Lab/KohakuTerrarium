"""Tests for managed web-daemon CLI lifecycle."""

import argparse
from types import SimpleNamespace
from unittest.mock import mock_open

import pytest

from kohakuterrarium.cli import serve


class TestServeLifecycle:
    def test_restart_preserves_home_dir(self, monkeypatch) -> None:
        started = []
        monkeypatch.setattr(serve, "serve_stop_cli", lambda _args: 0)
        monkeypatch.setattr(
            serve, "serve_start_cli", lambda args: started.append(args) or 0
        )
        args = argparse.Namespace(
            timeout=5.0,
            host="127.0.0.1",
            port=8001,
            dev=False,
            log_level="INFO",
            mode="standalone",
            lab_bind="",
            lab_token="",
            home_dir="C:/kt-home",
            foreground=False,
        )

        assert serve.serve_restart_cli(args) == 0
        assert started[0].home_dir == "C:/kt-home"

    def test_spawn_closes_parent_log_handle_on_success_and_failure(
        self, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.setattr(serve, "RUN_DIR", tmp_path)
        monkeypatch.setattr(serve, "LOG_PATH", tmp_path / "daemon.log")

        opened = mock_open()
        monkeypatch.setattr("builtins.open", opened)
        monkeypatch.setattr(
            serve.subprocess,
            "Popen",
            lambda *_args, **_kwargs: SimpleNamespace(pid=123),
        )

        assert serve._spawn_server_process("127.0.0.1", 8001, False, "INFO") == 123
        assert opened.return_value.close.call_count == 1

        opened.reset_mock()

        def _fail(*_args, **_kwargs):
            raise OSError("spawn failed")

        monkeypatch.setattr(serve.subprocess, "Popen", _fail)

        with pytest.raises(OSError, match="spawn failed"):
            serve._spawn_server_process("127.0.0.1", 8001, False, "INFO")
        assert opened.return_value.close.call_count == 1
