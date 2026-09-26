"""Lifecycle refuses configuration mutation while an instance owns its lock."""

import pytest

from kohakuterrarium.mcp_server.connection import ConnectionStore, write_json
from kohakuterrarium.mcp_server.service import start, status, stop


def test_existing_instance_and_stale_records(tmp_path):
    store = ConnectionStore(tmp_path, tmp_path / "state")
    record = store.configure(public_origin="https://example.com")
    write_json(
        store.runtime_path,
        {
            "run_id": "old",
            "state": "ready",
            "public_ready": True,
            "tunnel_state": "online",
        },
    )
    assert status(store)["state"] == "stopped"
    assert status(store)["tunnel_state"] == "stopped"
    with store.instance_lock:
        assert start(store)["run_id"] == "old"
        with pytest.raises(ValueError, match="running"):
            start(store, port=9999)
        assert store.load().port == record.port
    assert stop(store)["state"] == "stopped"
    assert store.load().secret == record.secret


def test_corrupt_runtime_with_live_lock_reports_unknown_health(tmp_path):
    store = ConnectionStore(tmp_path, tmp_path / "state")
    store.configure(public_origin="https://example.com", tunnel="external")
    store.runtime_path.write_text("{broken")
    with store.instance_lock:
        snapshot = status(store)
        assert snapshot["state"] == "unresponsive"
        assert snapshot["running"] and not snapshot["public_ready"]
        with pytest.raises(RuntimeError, match="no process was signalled"):
            stop(store)
