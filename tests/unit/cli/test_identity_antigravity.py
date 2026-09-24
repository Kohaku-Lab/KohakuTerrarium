import json

import pytest

from kohakuterrarium.cli import _main
from kohakuterrarium.cli.config import config_cli
from kohakuterrarium.llm import antigravity_auth as auth


def test_status_command_is_offline_and_reports_missing_login(monkeypatch, capsys):
    monkeypatch.setattr(auth, "read_sources", lambda: [])
    args = _main._build_parser().parse_args(["config", "antigravity", "status"])
    assert config_cli(args) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "login_required"


def test_refresh_without_login_does_not_start_cli(monkeypatch, capsys):
    monkeypatch.setattr(auth, "read_sources", lambda: [])
    monkeypatch.setattr(
        auth, "run_agy_models", lambda: pytest.fail("must not launch interactive login")
    )
    args = _main._build_parser().parse_args(["config", "antigravity", "refresh"])
    assert config_cli(args) == 1
    assert "login_required" in capsys.readouterr().out
