"""Unified ingress settings and snapshots are isolated by KT configuration root."""

import json

import pytest

from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server.setup import SetupSession
from kohakuterrarium.mcp_server.service import status
from kohakuterrarium.mcp_server.records import write_json
from kohakuterrarium.utils.file_lock import FileLock


def test_empty_endpoint_setup_snapshot_and_isolation(tmp_path):
    store = EndpointStore(tmp_path / "home")
    session = SetupSession.open(store)
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps({"tools": [{"name": "read"}]}))
    record = session.prepare(
        public_origin="https://example.com", tunnel="external", tools_config=tools
    )
    session.save(record)
    assert store.registry.read() == {}
    store.save_active("one", record)
    tools.write_text(json.dumps({"tools": [{"name": "python"}]}))
    write_json(store.runtime_path, {"run_id": "one", "state": "ready"})
    with FileLock(store.instance_lock.path):
        assert status(store)["restart_required"]
        tools.write_text("tools: [\n", encoding="utf-8")
        invalid = status(store)
        assert invalid["state"] == "ready"
        assert invalid["configured"]["tools_revision"] == "invalid"
        assert invalid["active"]["tools_revision"] != "invalid"
        assert invalid["restart_required"]
    assert [t.name for t in store.active_tools("one").tools] == ["read"]
    assert store.load().url == record.url
    other = EndpointStore(tmp_path / "other")
    with pytest.raises(ValueError, match="setup"):
        other.load()
    assert other.directory != store.directory


def test_identity_reuse_partial_update_and_corruption(tmp_path):
    store = EndpointStore(tmp_path / "home")
    first = store.configure(public_origin="https://example.com", tunnel="external")
    assert store.configure().model_dump() == first.model_dump()
    assert store.configure(port=9900).port == 9900
    changed = store.configure(public_origin="https://another.example")
    assert changed.secret == first.secret
    assert changed.public_origin == "https://another.example"
    store.record_path.write_text("{broken")
    with pytest.raises(ValueError, match="Invalid saved"):
        store.configure()
    assert store.record_path.read_text() == "{broken"


def test_strict_endpoint_fields_origin_and_environment(tmp_path):
    store = EndpointStore(tmp_path / "home")
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
    other = EndpointStore(tmp_path / "other")
    write_json(other.record_path, record.model_dump())
    before = other.record_path.read_bytes()
    with pytest.raises(ValueError, match="Invalid saved"):
        other.configure()
    assert other.record_path.read_bytes() == before
