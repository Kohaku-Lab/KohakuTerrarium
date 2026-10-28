"""Request-local metadata selection for the local preflight suffix only."""

from collections import Counter
from contextlib import closing
from functools import partial
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import kohakuterrarium.api.routes.persistence.preflight_metadata as preflight_metadata
import kohakuterrarium.api.routes.persistence.resume as route
import kohakuterrarium.api.routes.persistence.resume_cluster as cluster
import kohakuterrarium.session.migrations as migrations
import kohakuterrarium.session.readonly as readonly_mod
import kohakuterrarium.session.version as version
from kohakuterrarium.api.app import create_app
from kohakuterrarium.api.deps import get_service_factory
from kohakuterrarium.api.routes.persistence.preflight_metadata import (
    local_metadata_reader,
)
from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.config_serde import pack_agent_config
from kohakuterrarium.session.metadata_candidate import (
    MetadataReadError,
    read_metadata_candidate,
)
from kohakuterrarium.session.memory import SessionMemory
from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.session.resource_gate import GATE, ResourceReleaseError, try_fast
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.vault_handles import dispose
from kohakuterrarium.terrarium.graph_manifest import MANIFEST_KEY


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    root.mkdir()
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("KT_SESSION_DIR", str(root))
    path = root / "saved.kohakutr"
    with closing(SessionStore(path)) as store:
        store.init_meta("saved", "agent", "", str(tmp_path), ["alice"])
    counts = Counter()

    def copied(path, label):
        counts[label] += 1
        return read_session_meta(path)

    strict_read = version.read_strict_session_meta

    def counted_strict(path, *, keys=None):
        assert keys == ("format_version",)
        counts["strict"] += 1
        return strict_read(path, keys=keys)

    def selected(path):
        result = read_metadata_candidate(path, eligible=True)
        counts[result.reason] += 1
        return result.meta

    def observed_candidate(path, *, eligible=False):
        result = read_metadata_candidate(path, eligible=eligible)
        counts[result.reason] += 1
        return result

    monkeypatch.setattr(
        preflight_metadata, "read_metadata_candidate", observed_candidate
    )
    monkeypatch.setattr(
        cluster, "read_session_meta", partial(copied, label="classification")
    )
    monkeypatch.setattr(version, "read_strict_session_meta", counted_strict)
    monkeypatch.setattr(
        migrations, "read_session_meta", partial(copied, label="version")
    )
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield app, client, path, counts, selected
    finally:
        client.close()


@pytest.mark.parametrize("selected", [False, True])
def test_preexisting_uncertainty_rejects_repeated_requests_before_local_reader(
    setup, selected
):
    app, client, path, counts, reader = setup
    if selected:
        app.dependency_overrides[local_metadata_reader] = lambda: reader

    def forbidden_runtime():
        raise AssertionError("quarantined request must not create a runtime")

    app.dependency_overrides[get_service_factory] = lambda: forbidden_runtime
    lease = try_fast(path, eligible=True).lease
    assert lease is not None
    lease.finish(source_released=False)
    for _ in range(2):
        response = client.post("/api/sessions/saved/resume/preflight")
        assert response.status_code == 503
        assert response.json() == {
            "detail": {
                "code": "session_metadata_resource_unavailable",
                "message": "Session metadata resources are unavailable",
            }
        }
        assert str(path) not in response.text
        assert GATE.snapshot(path).new_uncertain
        assert not counts
    missing = client.post("/api/sessions/missing/resume/preflight")
    assert missing.status_code == 404


def test_prerequisite_nonresource_failure_keeps_original_http_status(
    setup, monkeypatch
):
    _, client, _, counts, _ = setup

    def denied(*args):
        raise HTTPException(409, detail="existing conflict")

    monkeypatch.setattr(route, "canonical_resume_path", denied)
    response = client.post("/api/sessions/saved/resume/preflight")
    assert response.status_code == 409
    assert response.json() == {"detail": "existing conflict"}
    assert not counts


@pytest.mark.parametrize("manifest", [False, True])
def test_default_best_effort_matches_legacy_without_dependency_override(
    setup, manifest
):
    app, client, path, counts, selected = setup
    if manifest:
        with closing(SessionStore(path)) as store:
            store.meta[MANIFEST_KEY] = {
                "kind": "kohakuterrarium.live_graph",
                "version": 1,
                "revision": 1,
                "graph_id": "saved",
                "creatures": [
                    {
                        "creature_id": "alice",
                        "name": "alice",
                        "config_snapshot": pack_agent_config(AgentConfig(name="alice")),
                        "source_ref": None,
                        "pwd": str(path.parent),
                        "is_privileged": False,
                        "parent_creature_id": None,
                    }
                ],
                "channels": [],
                "listen": [],
                "send": [],
            }
    before = path.read_bytes()
    app.dependency_overrides[local_metadata_reader] = lambda: None
    baseline = client.post("/api/sessions/saved/resume/preflight")
    assert baseline.status_code == 200
    counts.clear()

    app.dependency_overrides.pop(local_metadata_reader)
    response = client.post("/api/sessions/saved/resume/preflight")
    assert response.status_code == 200
    assert response.json() == baseline.json()
    assert counts == {"classification": 2, "version": 1, "ok": 1 if manifest else 2}
    counts.clear()
    normal = client.post("/api/sessions/saved/resume/preflight")
    assert normal.json() == baseline.json()
    assert counts == {"classification": 2, "version": 1, "ok": 1 if manifest else 2}
    counts.clear()
    replaced = client.post(
        "/api/sessions/saved/resume/preflight",
        json={"pwd": str(path.parent)},
    )
    assert replaced.status_code == 200
    assert replaced.json()["ready"] is True
    assert counts == {"classification": 2, "version": 1, "ok": 2}
    assert path.read_bytes() == before
    assert not GATE.snapshot(path).registered


@pytest.mark.parametrize("owner_kind", ["store", "memory"])
def test_default_reader_falls_back_for_live_resources_then_returns_to_fast(
    setup, owner_kind
):
    app, client, path, counts, _ = setup
    with closing(
        SessionStore(path) if owner_kind == "store" else SessionMemory(str(path))
    ):
        assert counts == {"strict": 1}
        counts.clear()
        response = client.post("/api/sessions/saved/resume/preflight")
        assert response.status_code == 200 and response.json()["ready"]
        assert counts == {"classification": 2, "version": 1, "legacy_enrolled": 2}
    counts.clear()
    response = client.post("/api/sessions/saved/resume/preflight")
    assert response.status_code == 200 and response.json()["ready"]
    assert counts == {"classification": 2, "version": 1, "ok": 2}
    assert not GATE.snapshot(path).registered


@pytest.mark.parametrize("fault", ["source", "decoder"])
def test_real_cleanup_failure_reaches_http_without_retry(setup, monkeypatch, fault):
    app, client, path, counts, _ = setup
    held = []

    def unreleased(handle):
        held.append(handle)
        raise OSError("source release failed")

    def undecodable(key):
        raise readonly_mod.MetaDecodeError(f"undecodable key {key!r}")

    def faulted_candidate(path, *, eligible=False):
        assert eligible
        counts["selected"] += 1
        with monkeypatch.context() as scoped:
            if fault == "source":
                scoped.setattr(readonly_mod, "dispose", unreleased)
            else:
                scoped.setattr(readonly_mod, "_strict_key", undecodable)
            return read_metadata_candidate(path, eligible=eligible)

    def service():
        counts["service"] += 1
        raise AssertionError("local preflight must not create a runtime")

    app.dependency_overrides[get_service_factory] = lambda: service
    monkeypatch.setattr(
        preflight_metadata, "read_metadata_candidate", faulted_candidate
    )
    try:
        result = client.post("/api/sessions/saved/resume/preflight")
        assert result.status_code == (503 if fault == "source" else 500)
        assert result.json()["detail"]["code"] == (
            "session_metadata_resource_unavailable"
            if fault == "source"
            else "session_metadata_read_failed"
        )
        assert "ready" not in result.json()
        assert counts == {"classification": 2, "version": 1, "selected": 1}
        if fault == "source":
            assert GATE.snapshot(path).new_uncertain
        else:
            assert not GATE.snapshot(path).registered
    finally:
        for handle in held:
            dispose(handle)


@pytest.mark.parametrize("fail_on", [1, 2])
@pytest.mark.parametrize(
    "stage,status,code",
    [
        ("resource", 503, "session_metadata_resource_unavailable"),
        ("sql", 500, "session_metadata_read_failed"),
        ("decode", 500, "session_metadata_read_failed"),
        ("gate", 503, "session_metadata_resource_unavailable"),
    ],
)
def test_failure_is_structured_and_never_ready(setup, stage, status, code, fail_on):
    app, client, path, counts, _ = setup

    def fail(path):
        counts["attempts"] += 1
        if counts["attempts"] < fail_on:
            return read_session_meta(path)
        if stage == "gate":
            raise ResourceReleaseError("secret filesystem path")
        raise MetadataReadError(stage, OSError("secret payload"))

    app.dependency_overrides[local_metadata_reader] = lambda: fail
    result = client.post("/api/sessions/saved/resume/preflight")
    assert result.status_code == status
    assert result.json()["detail"]["code"] == code
    assert "secret" not in result.text and "ready" not in result.json()
    assert counts == {"classification": 2, "version": 1, "attempts": fail_on}


def test_remote_and_cluster_never_invoke_local_reader(setup, monkeypatch):
    app, client, path, counts, _ = setup

    def fail(*args, **kwargs):
        raise AssertionError("local metadata reader reached from remote branch")

    monkeypatch.setattr(preflight_metadata, "read_metadata_candidate", fail)
    app.dependency_overrides[get_service_factory] = lambda: lambda: SimpleNamespace(
        host=object()
    )
    calls = []

    async def remote(host, path, node, **kwargs):
        calls.append(node)
        return {"legacy": True, "ready": True, "members": [], "gaps": []}

    monkeypatch.setattr(route, "_worker_workspace_preflight", remote)
    result = client.post(
        "/api/sessions/saved/resume/preflight", json={"on_node": "worker"}
    )
    assert result.status_code == 200 and result.json()["ready"]
    assert calls == ["worker"]
    assert counts == {"classification": 2}
    counts.clear()
    missing = client.post("/api/sessions/missing/resume/preflight")
    assert missing.status_code == 404
    assert not counts
    calls.clear()
    result = client.post(
        "/api/sessions/saved/resume/preflight",
        json={"members": [{"sid": "saved", "on_node": "worker"}]},
    )
    assert result.status_code == 200 and result.json()["ready"], result.text
    assert calls == ["worker"]
    assert counts == {"classification": 2}
