"""MCP CLI reports machine-readable failures without credential output."""

import argparse
import json
import io
import os
import subprocess
import sys

import pytest

from kohakuterrarium.cli.mcp_serve import add_mcp_serve_subparser, mcp_serve_cli
from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server.records import write_json
from kohakuterrarium.utils.file_lock import FileLock


def test_home_override_precedes_framework_logging(tmp_path):
    old, target = tmp_path / "inherited", tmp_path / "target"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "kohakuterrarium",
            "mcp-serve",
            "workspace",
            "list",
            "--home-dir",
            str(target),
            "--json",
        ],
        env={**os.environ, "KT_CONFIG_DIR": str(old)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["workspaces"] == []
    assert not list(old.rglob("*.log")), "bootstrap logged into another environment"
    assert list(target.rglob("*.log"))


def test_json_status_of_unconfigured_workspace(tmp_path, capsys):
    parser = argparse.ArgumentParser()
    add_mcp_serve_subparser(parser.add_subparsers())
    args = parser.parse_args(
        [
            "mcp-serve",
            "status",
            "--home-dir",
            str(tmp_path / "state"),
            "--json",
        ]
    )
    assert mcp_serve_cli(args) == 1
    assert "error" in json.loads(capsys.readouterr().out)


def test_status_prints_management_failure_separately_from_ingress(tmp_path, capsys):
    store = EndpointStore(tmp_path / "home")
    record = store.configure(public_origin="https://example.com", tunnel="external")
    store.save_active("run", record)
    write_json(
        store.runtime_path,
        {
            "run_id": "run",
            "state": "degraded",
            "local_ready": True,
            "public_ready": True,
            "management": {
                "protocol_version": 1,
                "state": "failed",
                "error": "Management consumer exited; restart",
            },
        },
    )
    parser = argparse.ArgumentParser()
    add_mcp_serve_subparser(parser.add_subparsers())
    args = parser.parse_args(["mcp-serve", "status", "--home-dir", str(store.home_dir)])
    with store.instance_lock:
        assert mcp_serve_cli(args) == 0
    output = capsys.readouterr().out
    assert "Management: failed" in output and "Management consumer exited" in output
    assert "local=True; public=True" in output
    assert record.secret not in output


def test_setup_script_contract_and_removed_start_flags(tmp_path, capsys):
    parser = argparse.ArgumentParser()
    add_mcp_serve_subparser(parser.add_subparsers())
    common = ["--home-dir", str(tmp_path / "state")]
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
            "--home-dir",
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
    store = EndpointStore(tmp_path / "state")
    original = store.configure(public_origin="https://example.com", tunnel="external")
    parser = argparse.ArgumentParser()
    add_mcp_serve_subparser(parser.add_subparsers())
    args = parser.parse_args(
        [
            "mcp-serve",
            "rotate",
            "--home-dir",
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
