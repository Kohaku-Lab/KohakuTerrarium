"""Atomic record writes tolerate transient reader contention."""

import json
import os
import threading
import time
from pathlib import Path

import pytest

from kohakuterrarium.mcp_server.records import read_json, write_json


@pytest.mark.skipif(
    os.name != "nt", reason="Windows denies replace while a normal read handle is open"
)
def test_atomic_update_tolerates_brief_windows_reader(tmp_path):
    path = tmp_path / "runtime.json"
    write_json(path, {"before": True})
    failures = []

    def publish():
        try:
            write_json(path, {"after": True})
        except OSError as exc:
            failures.append(type(exc).__name__)

    with path.open("rb"):
        writer = threading.Thread(target=publish)
        writer.start()
        time.sleep(0.15)
    writer.join(timeout=3)
    assert not writer.is_alive() and not failures
    assert json.loads(path.read_text()) == {"after": True}


@pytest.mark.skipif(os.name != "nt", reason="Windows byte locks deny concurrent reads")
def test_record_read_recovers_after_windows_byte_lock_release(tmp_path, monkeypatch):
    msvcrt = pytest.importorskip("msvcrt")
    path = tmp_path / "runtime.json"
    expected = {"run_id": "owned", "instance_id": "current"}
    write_json(path, expected)
    denied = threading.Event()
    results, failures = [], []
    read_text = Path.read_text

    def observe_read(candidate, *args, **kwargs):
        try:
            return read_text(candidate, *args, **kwargs)
        except PermissionError:
            if candidate == path:
                denied.set()
            raise

    def read():
        try:
            results.append(read_json(path))
        except Exception as exc:
            failures.append(exc)

    monkeypatch.setattr(Path, "read_text", observe_read)
    with path.open("r+b") as held:
        msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)
        reader = threading.Thread(target=read)
        reader.start()
        try:
            assert denied.wait(2), "Reader did not encounter the held byte lock"
        finally:
            held.seek(0)
            msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
            reader.join(timeout=3)
    assert not reader.is_alive() and not failures
    assert results == [expected]
