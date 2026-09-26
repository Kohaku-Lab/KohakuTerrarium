"""MCP CLI reports machine-readable failures without credential output."""

import argparse
import json
import io

import pytest

from kohakuterrarium.cli.mcp_serve import add_mcp_serve_subparser, mcp_serve_cli


def test_json_status_of_unconfigured_workspace(tmp_path, capsys):
    parser = argparse.ArgumentParser()
    add_mcp_serve_subparser(parser.add_subparsers())
    args = parser.parse_args(
        [
            "mcp-serve",
            "status",
            "--workspace",
            str(tmp_path),
            "--state-dir",
            str(tmp_path / "state"),
            "--json",
        ]
    )
    assert mcp_serve_cli(args) == 1
    assert "error" in json.loads(capsys.readouterr().out)


def test_setup_script_contract_and_removed_start_flags(tmp_path, capsys):
    parser = argparse.ArgumentParser()
    add_mcp_serve_subparser(parser.add_subparsers())
    common = ["--workspace", str(tmp_path), "--state-dir", str(tmp_path / "state")]
    args = parser.parse_args(
        [
            "mcp-serve",
            "setup",
            *common,
            "--non-interactive",
            "--json",
            "--mode",
            "external",
            "--origin",
            "https://example.com",
        ]
    )
    assert mcp_serve_cli(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "configured" and not result["running"]
    assert "secret" not in json.dumps(result)
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["mcp-serve", "start", "--public-origin", "https://example.com"]
        )


def test_setup_non_tty_missing_input_never_prompts(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    parser = argparse.ArgumentParser()
    add_mcp_serve_subparser(parser.add_subparsers())
    args = parser.parse_args(
        [
            "mcp-serve",
            "setup",
            "--workspace",
            str(tmp_path),
            "--state-dir",
            str(tmp_path / "state"),
            "--json",
        ]
    )
    assert mcp_serve_cli(args) == 1
    assert "origin" in json.loads(capsys.readouterr().out)["error"]
