"""Persistent identities reject corruption and cross-workspace rebinding."""

import json
import os
import threading
import time

import pytest

from kohakuterrarium.mcp_server.connection import ConnectionStore, write_json


def test_identity_reuse_import_and_validation(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    store = ConnectionStore(workspace, tmp_path / "state")
    old = tmp_path / "probe.json"
    old.write_text(
        json.dumps(
            {
                "workspace": store.workspace,
                "public_origin": "https://example.com",
                "secret": "a" * 43,
            }
        )
    )
    first = store.configure(
        public_origin="https://example.com", tunnel="external", import_connection=old
    )
    assert first.secret == "a" * 43
    assert store.configure().model_dump() == first.model_dump()
    assert store.configure(port=9900).port == 9900
    assert store.load().secret == first.secret
    changed = store.configure(public_origin="https://another.example")
    assert changed.secret == first.secret
    assert changed.public_origin == "https://another.example"
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="workspace"):
        ConnectionStore(other, tmp_path / "state").configure(import_connection=old)
    store.record_path.write_text("{broken")
    with pytest.raises(ValueError):
        store.configure()
    assert store.record_path.read_text() == "{broken"


def test_strict_connection_fields_and_origin(tmp_path):
    store = ConnectionStore(tmp_path, tmp_path / "state")
    for origin in (
        "http://example.com",
        "https://user:password@example.com",
        "https://example.com/mcp/x",
    ):
        with pytest.raises(ValueError):
            store.configure(public_origin=origin)
    with pytest.raises(ValueError, match="origin"):
        store.configure()
    record = store.configure(public_origin="https://example.com")
    assert record.tunnel == "ngrok" and len(record.secret) >= 43
    with pytest.raises(ValueError):
        store.configure(port=0)


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
