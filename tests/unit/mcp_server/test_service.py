"""Lifecycle refuses configuration mutation while an instance owns its lock."""

import pytest

from kohakuterrarium.mcp_server.connection import ConnectionStore, write_json
from kohakuterrarium.mcp_server.service import connection_url, start, status, stop
from kohakuterrarium.mcp_server.setup import SetupSession


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
        with pytest.raises(TypeError):
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


def test_running_snapshot_survives_reconfiguration(tmp_path):
    store = ConnectionStore(tmp_path, tmp_path / "state")
    original = store.configure(public_origin="https://old.example", tunnel="external")
    run_id = "a" * 32
    store.save_active(run_id, original)
    write_json(
        store.runtime_path, {"run_id": run_id, "state": "ready", "public_ready": True}
    )
    with store.instance_lock:
        session = SetupSession.open(store)
        session.save(session.prepare(public_origin="https://new.example", port=9000))
        snapshot = start(store)
        assert snapshot["public_origin"] == original.public_origin
        assert snapshot["port"] == original.port
        assert snapshot["restart_required"]
        assert set(snapshot["pending_changes"]) == {"public_origin", "port"}
        assert snapshot["configured"]["public_origin"] == "https://new.example"
        assert original.secret not in str(snapshot)
        assert connection_url(store) == original.url
        assert connection_url(store, configured=True) == store.load().url
        assert store.load_active(run_id) == original
        with pytest.raises(ValueError, match="identity"):
            store.load_active("b" * 32)
    assert connection_url(store) == store.load().url


def test_unconfigured_start_does_not_write_configuration(tmp_path):
    store = ConnectionStore(tmp_path, tmp_path / "state")
    with pytest.raises(ValueError, match="setup"):
        start(store)
    assert not store.record_path.exists()
