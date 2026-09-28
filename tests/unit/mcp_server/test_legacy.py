"""Legacy records are immutable migration inputs, never active endpoint settings."""

import hashlib
import json
import os

import pytest
from pydantic import ValidationError

from kohakuterrarium.mcp_server.legacy import LegacyConnectionSource


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_legacy_source_reads_existing_identity_without_rewriting(tmp_path, encoding):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = os.path.normcase(str(workspace.resolve()))
    root = tmp_path / "old-state"
    directory = root / hashlib.sha256(identity.encode()).hexdigest()[:32]
    directory.mkdir(parents=True)
    path = directory / "connection.json"
    data = {
        "workspace": identity,
        "public_origin": "https://example.com",
        "secret": "a" * 43,
    }
    path.write_text(json.dumps(data), encoding=encoding)
    before = path.read_bytes()
    source = LegacyConnectionSource(workspace, root)
    record = source.load()
    assert record.workspace == identity and record.version == 1
    assert record.port == 8765 and record.tunnel == "ngrok"
    with pytest.raises(ValidationError):
        record.secret = "b" * 43
    assert path.read_bytes() == before
    assert source.command_lock.path == directory / "command.lock"
    assert source.instance_lock.path == directory / "instance.lock"
    with pytest.raises(ValueError, match="directory"):
        LegacyConnectionSource(tmp_path / "missing", root)
