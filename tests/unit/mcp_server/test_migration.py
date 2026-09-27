"""Migration is explicit, preserves the source and never broadens its credential."""

import json

import pytest

from kohakuterrarium.mcp_server.connection import ConnectionStore
from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server.migration import migrate
from kohakuterrarium.utils.file_lock import FileLock


def test_stopped_legacy_migration_preserves_source_and_rekeys(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    legacy = ConnectionStore(workspace, tmp_path / "legacy")
    config = tmp_path / "tools.json"
    config.write_text(
        json.dumps({"workspace": str(workspace), "tools": [{"name": "read"}]})
    )
    old = legacy.configure(
        public_origin="https://example.com", tunnel="external", tools_config=config
    )
    before = legacy.record_path.read_bytes()
    target = EndpointStore(tmp_path / "home")
    with FileLock(legacy.instance_lock.path):
        with pytest.raises(RuntimeError, match="Stop"):
            migrate(target, workspace, "project", tmp_path / "legacy")
    assert not target.record_path.exists()
    result = migrate(target, workspace, "project", tmp_path / "legacy")
    assert result["migrated"] and not result["running"]
    new = target.load()
    assert new.secret != old.secret and new.public_origin == old.public_origin
    assert [t.name for t in target.tools(new).tools] == ["read"]
    assert target.registry.read()["project"].path == old.workspace
    assert legacy.record_path.read_bytes() == before
    assert old.secret not in str(result) and new.secret not in str(result)
    with pytest.raises(ValueError, match="empty"):
        migrate(target, workspace, "other", tmp_path / "legacy")
