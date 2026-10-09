"""Unit tests for :mod:`kohakuterrarium.api.boot_restore` and the restore-state routes."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import kohakuterrarium.session.run_state as rs
from kohakuterrarium.api import boot_restore
from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.engine_pool import EnginePool, _user_session_dir
from kohakuterrarium.api.deps import _session_dir, get_service_legacy
from kohakuterrarium.api.routes.persistence import restore_state
from kohakuterrarium.studio.sessions.live import live_sessions


def test_auto_resume_is_on_unless_turned_off(monkeypatch):
    monkeypatch.delenv("KT_AUTO_RESUME", raising=False)
    assert boot_restore.auto_resume_enabled() is True
    for off in ("0", "false", "OFF", " no "):
        monkeypatch.setenv("KT_AUTO_RESUME", off)
        assert boot_restore.auto_resume_enabled() is False
    monkeypatch.setenv("KT_AUTO_RESUME", "1")
    assert boot_restore.auto_resume_enabled() is True


def test_resolver_maps_the_shared_dir_and_rejects_others(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "shared"))
    app = SimpleNamespace(
        state=SimpleNamespace(engine_pool=None, auth_config=AuthConfig())
    )
    resolve = boot_restore.service_resolver(app)
    assert resolve(_session_dir()) is get_service_legacy()
    assert resolve(str(tmp_path / "elsewhere")) is None
    assert resolve("") is None


def test_resolver_maps_user_dirs_to_pool_engines_under_isolation(tmp_path):
    pool = EnginePool()
    auth = AuthConfig(multi_user="optional")
    app = SimpleNamespace(state=SimpleNamespace(engine_pool=pool, auth_config=auth))
    resolve = boot_restore.service_resolver(app)
    service = resolve(str(_user_session_dir(7)))
    assert service.engine is pool.get_or_create(7)
    assert resolve(str(_user_session_dir(None))).engine is pool.get_or_create(None)
    assert resolve(str(tmp_path / "users" / "x" / "sessions")) is None
    pool.evict_all()


async def test_restore_on_boot_records_outcomes_and_survives_errors(monkeypatch):
    app = SimpleNamespace(
        state=SimpleNamespace(engine_pool=None, auth_config=AuthConfig())
    )

    async def _boom(_resolver):
        raise RuntimeError("restore exploded")

    monkeypatch.setattr(boot_restore, "restore_live_sessions", _boom)
    assert await boot_restore.restore_on_boot(app) == []
    assert app.state.restore_outcomes == []

    async def _two(_resolver):
        return [{"path": "a", "status": "restored"}, {"path": "b", "status": "failed"}]

    monkeypatch.setattr(boot_restore, "restore_live_sessions", _two)
    outcomes = await boot_restore.restore_on_boot(app)
    assert [o["status"] for o in outcomes] == ["restored", "failed"]
    assert app.state.restore_outcomes is outcomes


def _client(tmp_path):
    app = FastAPI()
    app.include_router(restore_state.router, prefix="/api/sessions")
    app.state.engine_pool = None
    app.state.auth_config = AuthConfig()
    app.state.restore_outcomes = [
        {"path": str(tmp_path / "x.kohakutr"), "status": "failed"}
    ]
    app.state.restore_task = None
    return TestClient(app), app


def test_restore_state_lists_rows_and_dismiss_drops_one(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_AUTO_RESUME", "1")
    registry = live_sessions()
    registry.add(
        tmp_path / "a.kohakutr", session_id="a", session_dir="d", boot_id="earlier"
    )
    registry.update(tmp_path / "a.kohakutr", claimed_by=rs.BOOT_ID)
    registry.add(
        tmp_path / "b.kohakutr", session_id="b", session_dir="d", boot_id="earlier"
    )
    registry.update(
        tmp_path / "b.kohakutr", claimed_by=rs.BOOT_ID, failed="pwd missing"
    )
    client, _app = _client(tmp_path)
    state = client.get("/api/sessions/restore-state").json()
    assert (state["enabled"], state["running"]) == (True, False)
    assert [
        (r["session_id"], r["restored_this_boot"], r["failed"]) for r in state["rows"]
    ] == [
        ("a", True, None),
        ("b", False, "pwd missing"),
    ]
    assert client.post(
        "/api/sessions/restore-state/dismiss",
        json={"path": str(tmp_path / "b.kohakutr")},
    ).json() == {"removed": True}
    assert [
        r["session_id"]
        for r in client.get("/api/sessions/restore-state").json()["rows"]
    ] == ["a"]


def test_retry_needs_a_row_and_a_hosting_service_then_records_the_outcome(
    tmp_path, monkeypatch
):
    client, app = _client(tmp_path)
    missing = client.post(
        "/api/sessions/restore-state/retry",
        json={"path": str(tmp_path / "none.kohakutr")},
    )
    assert missing.status_code == 404
    registry = live_sessions()
    registry.add(
        tmp_path / "x.kohakutr",
        session_id="x",
        session_dir=str(tmp_path / "foreign"),
        boot_id="earlier",
    )
    assert (
        client.post(
            "/api/sessions/restore-state/retry",
            json={"path": str(tmp_path / "x.kohakutr")},
        ).status_code
        == 409
    )
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "foreign"))
    retried = client.post(
        "/api/sessions/restore-state/retry", json={"path": str(tmp_path / "x.kohakutr")}
    ).json()
    assert retried["status"] == "missing"
    assert app.state.restore_outcomes == [retried]
