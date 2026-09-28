"""Atomic record writes tolerate transient reader contention."""

import json
import os
import threading
import time

import pytest

from kohakuterrarium.mcp_server.records import write_json


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
