"""Owned child cleanup is driven by pipe lifetime, not a saved PID."""

import json
import os
import sys
import threading
import time

import pytest

from kohakuterrarium.mcp_server import tunnel
from kohakuterrarium.mcp_server.endpoint import Endpoint
from kohakuterrarium.mcp_server.tunnel import (
    cloudflare_command,
    cloudflare_environment,
    ngrok_command,
    run_owned,
)


def test_ngrok_command_contains_no_mcp_credential():
    record = Endpoint(
        home_dir="home",
        public_origin="https://example.com",
        secret="a" * 43,
        ngrok_config="private.yml",
        port=9000,
    )
    command = ngrok_command(record)
    assert record.secret not in " ".join(command)
    assert "http://127.0.0.1:9000" in command
    assert command[-2:] == ["--config", "private.yml"]


def test_cloudflare_config_routes_active_port_without_secrets(tmp_path):
    record = Endpoint(
        home_dir="home",
        public_origin="https://mcp.example.com",
        secret="s" * 43,
        port=9123,
        tunnel="cloudflare",
        cloudflared_bin="cloudflared",
        cloudflare_tunnel_id="f28d5968-1e07-4a79-8055-0c7e58f38341",
        cloudflare_credentials="/private/credentials.json",
    )
    path = tmp_path / "generated.json"
    command = cloudflare_command(record, path)
    config = json.loads(path.read_text())
    assert config["ingress"] == [
        {"hostname": "mcp.example.com", "service": "http://127.0.0.1:9123"},
        {"service": "http_status:404"},
    ]
    assert config["credentials-file"] == record.cloudflare_credentials
    assert command == [
        "cloudflared",
        "tunnel",
        "--no-autoupdate",
        "--config",
        str(path),
        "run",
        record.cloudflare_tunnel_id,
    ]
    assert record.secret not in str(config) + str(command)


def test_cloudflare_does_not_inherit_other_tunnel_identity():
    env = cloudflare_environment(
        {
            "TUNNEL_TOKEN": "wrong",
            "TUNNEL_TOKEN_FILE": "wrong-file",
            "TUNNEL_CRED_CONTENTS": "wrong",
            "TUNNEL_CRED_FILE": "wrong-file",
            "HTTPS_PROXY": "http://proxy",
        }
    )
    assert env == {"HTTPS_PROXY": "http://proxy"}


@pytest.mark.parametrize("stage", ["configuration", "process"])
def test_guardian_reports_os_errors_without_sensitive_details(
    tmp_path, monkeypatch, capsys, stage
):
    record = Endpoint(
        home_dir=str(tmp_path),
        public_origin="https://example.com",
        secret="s" * 43,
        tunnel="cloudflare",
    )
    monkeypatch.setattr(tunnel.EndpointStore, "load_active", lambda *args: record)
    monkeypatch.setattr(
        sys, "argv", ["tunnel", "--home-dir", str(tmp_path), "--run-id", "test"]
    )

    def fail(*args, **kwargs):
        raise PermissionError("sensitive-path-or-token")

    monkeypatch.setattr(
        tunnel, "write_json" if stage == "configuration" else "run_owned", fail
    )
    assert tunnel.main() == 1
    error = capsys.readouterr().err
    assert stage in error and "PermissionError" in error
    assert "sensitive-path-or-token" not in error and record.secret not in error


def test_parent_pipe_eof_reaps_owned_child(tmp_path):
    marker = tmp_path / "started"
    reader, writer = os.pipe()
    completed = []
    with os.fdopen(reader, "rb") as parent_input:
        command = [
            sys.executable,
            "-c",
            "from pathlib import Path; import time; "
            f"Path({str(marker)!r}).write_text('started'); time.sleep(60)",
        ]
        worker = threading.Thread(
            target=lambda: completed.append(run_owned(command, parent_input))
        )
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.read_text() == "started"
        finally:
            os.close(writer)
            worker.join(timeout=8)
        assert not worker.is_alive() and completed == [0]
