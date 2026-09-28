"""Owned child cleanup is driven by pipe lifetime, not a saved PID."""

import os
import sys
import threading
import time

from kohakuterrarium.mcp_server.endpoint import Endpoint
from kohakuterrarium.mcp_server.tunnel import ngrok_command, run_owned


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
