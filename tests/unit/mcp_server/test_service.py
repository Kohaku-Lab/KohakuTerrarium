"""Lifecycle refuses configuration mutation while an instance owns its lock."""

import subprocess
import sys
import threading
import time

import pytest

from kohakuterrarium.mcp_server.connection import ConnectionStore, write_json
from kohakuterrarium.mcp_server.service import connection_url, start, status, stop
from kohakuterrarium.mcp_server.setup import SetupSession
from kohakuterrarium.utils.file_lock import FileLock


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


@pytest.mark.parametrize("diagnostic_race", [False, True])
def test_start_timeout_reaps_child_before_instance_lock_handoff(
    tmp_path, monkeypatch, diagnostic_race
):
    store = ConnectionStore(tmp_path, tmp_path / "state")
    store.configure(public_origin="https://example.invalid", tunnel="external")
    original = subprocess.Popen
    boot_gate, boot_marker = tmp_path / "boot-gate", tmp_path / "boot-marker"
    children = []
    diagnostic_threads = []
    held, released = threading.Event(), threading.Event()
    original_release = FileLock.release

    def release_probe(lock):
        if (
            threading.current_thread().name == "mcp-status-probe"
            and lock.path == store.instance_lock.path
        ):
            held.set()
            released.wait(5)
        original_release(lock)

    monkeypatch.setattr(FileLock, "release", release_probe)

    def delayed_start(command, **kwargs):
        child = original(
            [
                sys.executable,
                "-c",
                "import sys,time; from pathlib import Path\n"
                "gate, marker = map(Path, sys.argv[1:])\n"
                "deadline = time.monotonic() + 10\n"
                "while not gate.exists() and time.monotonic() < deadline: time.sleep(.01)\n"
                "if gate.exists(): marker.write_text('started')\n",
                str(boot_gate),
                str(boot_marker),
            ],
            **kwargs,
        )
        children.append(child)
        if diagnostic_race:
            diagnostic = threading.Thread(
                target=status, args=(store,), name="mcp-status-probe"
            )
            diagnostic_threads.append(diagnostic)
            diagnostic.start()
            assert held.wait(5)
            timer = threading.Timer(2, released.set)
            diagnostic_threads.append(timer)
            timer.start()
        return child

    monkeypatch.setattr(
        "kohakuterrarium.mcp_server.service.subprocess.Popen", delayed_start
    )
    try:
        result = start(store, wait=1)
        assert stop(store)["state"] == "stopped"
        assert (
            children[0].poll() is not None
        ), "unowned child can start after stop returned"
        assert not result["running"] and result["error"]
        boot_gate.touch()
        time.sleep(0.3)
        assert not boot_marker.exists(), "the interpreter outlived its launcher"
    finally:
        boot_gate.touch()
        released.set()
        for thread in diagnostic_threads:
            thread.join(timeout=5)
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
