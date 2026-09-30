"""Unified ingress settings and snapshots are isolated by KT configuration root."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.mcp_server.endpoint import EndpointStore
from kohakuterrarium.mcp_server import records
from kohakuterrarium.mcp_server.setup import SetupSession
from kohakuterrarium.mcp_server.service import connection_url, status
from kohakuterrarium.mcp_server.records import write_json
from kohakuterrarium.utils.file_lock import FileLock


@pytest.fixture
def owned_endpoint(tmp_path):
    store = EndpointStore(tmp_path / "home")
    record = store.configure(public_origin="https://example.com", tunnel="external")
    store.save_active("one", record)
    snapshot = {"run_id": "one", "instance_id": "instance", "state": "ready"}
    write_json(store.runtime_path, snapshot)
    with store.instance_lock:
        yield store, record, snapshot


@pytest.mark.parametrize(
    ("operation", "record_name"),
    [("runtime", "runtime"), ("url", "runtime"), ("url", "active")],
)
def test_transient_record_read_denial_preserves_identity(
    owned_endpoint, monkeypatch, operation, record_name
):
    store, record, snapshot = owned_endpoint
    path = getattr(store, f"{record_name}_path")
    before = path.read_bytes()
    open_file = Path.open
    denied = False

    def deny_once(candidate, *args, **kwargs):
        nonlocal denied
        if candidate == path and not denied:
            denied = True
            raise PermissionError("synthetic sharing violation")
        return open_file(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_once)
    if operation == "runtime":
        assert store.runtime() == snapshot
    else:
        assert connection_url(store) == record.url
    assert denied and path.read_bytes() == before


@pytest.mark.parametrize("record_name", ["runtime", "active"])
def test_read_recovery_revalidates_replaced_run_identity(
    owned_endpoint, monkeypatch, record_name
):
    store, record, snapshot = owned_endpoint
    path = getattr(store, f"{record_name}_path")
    open_file = Path.open
    denied = False

    def replace_then_deny(candidate, *args, **kwargs):
        nonlocal denied
        if candidate == path and not denied:
            denied = True
            if record_name == "runtime":
                write_json(path, {**snapshot, "run_id": "replacement"})
            else:
                store.save_active("replacement", record)
            raise PermissionError("synthetic sharing violation")
        return open_file(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", replace_then_deny)
    with pytest.raises(ValueError, match="Active configuration unavailable"):
        connection_url(store)
    assert denied


@pytest.mark.parametrize("record_name", ["runtime", "active"])
def test_permanent_record_denial_remains_bounded_and_unavailable(
    owned_endpoint, monkeypatch, record_name
):
    store, _, snapshot = owned_endpoint
    assert store.runtime() == snapshot
    path = getattr(store, f"{record_name}_path")
    open_file = Path.open
    elapsed = 0.0

    def advance(seconds):
        nonlocal elapsed
        elapsed += seconds

    def deny(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("permanent synthetic denial")
        return open_file(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny)
    monkeypatch.setattr(
        records, "time", SimpleNamespace(monotonic=lambda: elapsed, sleep=advance)
    )
    if record_name == "runtime":
        assert store.runtime() == {}
    else:
        with pytest.raises(ValueError, match="Active configuration unavailable"):
            store.load_active("one")
    assert 1 <= elapsed < 2


@pytest.mark.parametrize("record_name", ["runtime", "active"])
@pytest.mark.parametrize("payload", [None, "{broken", "[]", "null"])
def test_unavailable_state_is_not_replaced_with_a_previous_identity(
    owned_endpoint, record_name, payload
):
    store, _, snapshot = owned_endpoint
    assert store.runtime() == snapshot
    path = getattr(store, f"{record_name}_path")
    if payload is None:
        path.unlink()
    else:
        path.write_text(payload)
    if record_name == "runtime":
        assert store.runtime() == {}
    else:
        with pytest.raises(ValueError, match="Active configuration unavailable"):
            store.load_active("one")
    with pytest.raises(ValueError, match="Active configuration unavailable"):
        connection_url(store)


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
