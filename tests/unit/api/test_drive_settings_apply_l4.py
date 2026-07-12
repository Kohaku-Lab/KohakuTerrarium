"""R1-30: an L4 Drive-settings apply must be honest across pooled engines.

Drive settings are shared per config home, but under L4 each user has a separate
pooled engine. Applying live to only the request engine leaves the others on
stale policy while the response claims ``applied_live``. The fix evicts the other
pooled engines (they rebuild from the new settings on next use) and reports the
honest cross-engine scope.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.db import (
    _reset_migration_state_for_tests,
    ensure_migrated,
)
from kohakuterrarium.api.auth.engine_pool import EnginePool
from kohakuterrarium.api.deps import set_service
from kohakuterrarium.api.routes.identity import settings as settings_mod
from kohakuterrarium.studio.identity import drive_settings as ds
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.drive.errors import DriveValidationError

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("KT_SESSION_DIR", raising=False)
    _reset_migration_state_for_tests()
    ensure_migrated()
    set_service(None)
    # Persist an enabled Drive settings file so pooled engines build with Drive.
    ds.save_settings(
        ds.DriveSettings(
            runtime=DriveRuntimeConfig(enabled=True),
            registrations={"generic": ds.RegistrationSetting(enabled=True)},
        )
    )

    app = FastAPI()
    app.state.engine_pool = EnginePool(
        max_active=8, idle_timeout_s=0, drive_resolver=ds.resolve_drive_kwargs
    )
    app.state.auth_config = AuthConfig(multi_user="optional", bcrypt_rounds=4)
    app.include_router(settings_mod.router, prefix="/api/settings")
    yield app
    app.state.engine_pool.evict_all()
    set_service(None)
    _reset_migration_state_for_tests()


def test_apply_evicts_other_pooled_engines_and_reports_scope(app):
    pool: EnginePool = app.state.engine_pool
    # Two other active users each hold a pooled engine on the old settings.
    pool.get_or_create(1)
    pool.get_or_create(2)

    with TestClient(app, raise_server_exceptions=False) as client:
        # An anonymous request (optional L4) applies against its own engine.
        r = client.post("/api/settings/drives/apply")
    assert r.status_code == 200, r.text
    body = r.json()
    # Honest cross-engine scope: the two other users were evicted for reload.
    assert "pooled_scope" in body
    evicted = set(body["pooled_scope"]["evicted_for_reload"])
    assert evicted == {1, 2}

    # The other users' engines are gone; each rebuilds from the new settings.
    live = set(pool.live_user_ids())
    assert 1 not in live and 2 not in live


def test_restart_required_apply_evicts_nothing_and_claims_no_live_scope(app):
    pool: EnginePool = app.state.engine_pool
    # Two other users plus the request (anonymous) engine, all Drive-enabled.
    pool.get_or_create(1)
    pool.get_or_create(2)
    pool.get_or_create(None)

    # Disabling the runtime cannot be applied to a live Drive-enabled engine, so
    # apply reports restart_required and no running engine is updated (R1-30).
    ds.save_settings(
        ds.DriveSettings(
            runtime=DriveRuntimeConfig(enabled=False),
            registrations={"generic": ds.RegistrationSetting(enabled=True)},
        )
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/api/settings/drives/apply")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "restart_required"
    scope = body["pooled_scope"]
    assert scope["applied_live_engine"] is None
    assert scope["evicted_for_reload"] == []
    # Nothing was torn down: a restart-required change evicts no pooled engine.
    live = set(pool.live_user_ids())
    assert 1 in live and 2 in live


def test_rejected_apply_evicts_nothing(app, monkeypatch):
    pool: EnginePool = app.state.engine_pool
    pool.get_or_create(1)
    pool.get_or_create(2)

    def _boom(*_a, **_k):
        raise DriveValidationError("bad settings")

    monkeypatch.setattr(ds, "resolve_runtime", _boom)

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/api/settings/drives/apply")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "rejected"
    scope = body["pooled_scope"]
    assert scope["applied_live_engine"] is None
    assert scope["evicted_for_reload"] == []
    # A rejected apply changed nothing, so it evicts nothing.
    live = set(pool.live_user_ids())
    assert 1 in live and 2 in live
