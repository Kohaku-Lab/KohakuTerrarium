"""Unified ingress settings and snapshots are isolated by KT configuration root."""

import json

import pytest

from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server.setup import SetupSession
from kohakuterrarium.mcp_server.service import status
from kohakuterrarium.mcp_server.connection import write_json
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
    assert [t.name for t in store.active_tools("one").tools] == ["read"]
    assert store.load().url == record.url
    other = EndpointStore(tmp_path / "other")
    with pytest.raises(ValueError, match="setup"):
        other.load()
    assert other.directory != store.directory
