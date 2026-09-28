"""Migration is explicit, preserves the source and never broadens its credential."""

import json
import hashlib
import os

import pytest

from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server.migration import migrate
from kohakuterrarium.utils.file_lock import FileLock


def test_stopped_legacy_migration_preserves_source_and_rekeys(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    identity = os.path.normcase(str(workspace.resolve()))
    directory = tmp_path / "legacy" / hashlib.sha256(identity.encode()).hexdigest()[:32]
    directory.mkdir(parents=True)
    record_path = directory / "connection.json"
    config = tmp_path / "tools.json"
    config.write_text(
        json.dumps({"workspace": str(workspace), "tools": [{"name": "read"}]})
    )
    old = {
        "version": 1,
        "workspace": identity,
        "public_origin": "https://example.com",
        "secret": "a" * 43,
        "port": 9123,
        "tunnel": "external",
        "ngrok_bin": "ngrok",
        "ngrok_config": None,
        "tools_config": str(config),
    }
    record_path.write_text(json.dumps(old), encoding="utf-8")
    before = record_path.read_bytes()
    target = EndpointStore(tmp_path / "home")
    with FileLock(directory / "instance.lock"):
        with pytest.raises(RuntimeError, match="Stop"):
            migrate(target, workspace, "project", tmp_path / "legacy")
    assert not target.record_path.exists()
    result = migrate(target, workspace, "project", tmp_path / "legacy")
    assert result["migrated"] and not result["running"]
    new = target.load()
    assert new.secret != old["secret"] and new.public_origin == old["public_origin"]
    assert new.port == old["port"] and new.tunnel == old["tunnel"]
    assert [t.name for t in target.tools(new).tools] == ["read"]
    assert target.registry.read()["project"].path == identity
    assert record_path.read_bytes() == before
    assert old["secret"] not in str(result) and new.secret not in str(result)
    with pytest.raises(ValueError, match="empty"):
        migrate(target, workspace, "other", tmp_path / "legacy")


@pytest.mark.parametrize("invalid", ["missing", "corrupt", "foreign", "version"])
def test_invalid_legacy_source_never_creates_endpoint(tmp_path, invalid):
    workspace = tmp_path / "project"
    workspace.mkdir()
    identity = os.path.normcase(str(workspace.resolve()))
    directory = tmp_path / "legacy" / hashlib.sha256(identity.encode()).hexdigest()[:32]
    directory.mkdir(parents=True)
    record_path = directory / "connection.json"
    if invalid == "corrupt":
        record_path.write_text("{broken", encoding="utf-8")
    elif invalid != "missing":
        record_path.write_text(
            json.dumps(
                {
                    "version": 7 if invalid == "version" else 1,
                    "workspace": "elsewhere" if invalid == "foreign" else identity,
                    "public_origin": "https://example.com",
                    "secret": "a" * 43,
                }
            ),
            encoding="utf-8",
        )
    before = record_path.read_bytes() if record_path.exists() else None
    target = EndpointStore(tmp_path / "home")
    with pytest.raises(ValueError):
        migrate(target, workspace, "project", tmp_path / "legacy")
    assert not target.record_path.exists() and not target.registry.read()
    assert (record_path.read_bytes() if record_path.exists() else None) == before
