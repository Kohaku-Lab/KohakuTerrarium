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
        assert started[0].no_resume is False
        args.no_resume = True
        assert serve.serve_restart_cli(args) == 0
        assert started[1].no_resume is True

    @pytest.mark.parametrize(
        ("flag", "yaml_value", "expected"),
        [
            (False, None, None),
            (True, None, "0"),
            (False, "false", "0"),
            (False, "true", None),
        ],
    )
    def test_no_resume_reaches_the_server_through_the_environment(
        self, monkeypatch, tmp_path, flag, yaml_value, expected
    ) -> None:
        monkeypatch.delenv("KT_AUTO_RESUME", raising=False)
        if yaml_value is not None:
            (tmp_path / "host.yaml").write_text(
                f"auto_resume: {yaml_value}\n", encoding="utf-8"
            )
            monkeypatch.setenv("KT_CONFIG_FILE", str(tmp_path / "host.yaml"))
        ran = []
        monkeypatch.setattr(serve, "run_server_internal", lambda a: ran.append(a) or 0)
        monkeypatch.setattr(serve, "enable_stderr_logging", lambda _level: None)
        parser = argparse.ArgumentParser()
        serve.add_serve_subparser(parser.add_subparsers(dest="command"))
        argv = ["serve", "start", "-f"] + (["--no-resume"] if flag else [])
        assert serve.serve_start_cli(parser.parse_args(argv)) == 0
        assert ran and serve.os.environ.get("KT_AUTO_RESUME") == expected

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
