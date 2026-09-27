"""MCP CLI reports machine-readable failures without credential output."""

import argparse
import json
import io

import pytest

from kohakuterrarium.cli.mcp_serve import add_mcp_serve_subparser, mcp_serve_cli
from kohakuterrarium.mcp_server.connection import ConnectionStore
from kohakuterrarium.utils.file_lock import FileLock


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


@pytest.mark.parametrize("json_output", [False, True])
def test_rotate_is_explicit_noninteractive_and_never_prints_credentials(
    tmp_path, monkeypatch, capsys, json_output
):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    store = ConnectionStore(tmp_path, tmp_path / "state")
    original = store.configure(public_origin="https://example.com", tunnel="external")
    parser = argparse.ArgumentParser()
    add_mcp_serve_subparser(parser.add_subparsers())
    args = parser.parse_args(
        [
            "mcp-serve",
            "rotate",
            "--workspace",
            str(tmp_path),
            "--state-dir",
            str(tmp_path / "state"),
            *(["--json"] if json_output else []),
        ]
    )
    with FileLock(store.instance_lock.path):
        assert mcp_serve_cli(args) == 1
    failure = capsys.readouterr().out
    assert original.secret not in failure
    assert store.load() == original
    if json_output:
        assert "error" in json.loads(failure)
    assert mcp_serve_cli(args) == 0
    output = capsys.readouterr().out
    rotated = store.load()
    assert rotated.secret != original.secret
    assert original.secret not in output and rotated.secret not in output
    assert "/mcp/" not in output
    if json_output:
        result = json.loads(output)
        assert result["rotated"] is True
        assert result["state"] == "stopped" and result["running"] is False
    else:
        assert "start" in output and "url" in output
