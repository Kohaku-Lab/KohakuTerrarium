"""Unit tests for :mod:`kohakuterrarium.api.deps`."""

import pytest

from kohakuterrarium.api import deps as mod
from kohakuterrarium.api.deps import (
    _DEFAULT_SESSION_DIR,
    _session_dir,
    get_engine,
    get_service_legacy as get_service,
    set_service,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    set_service(None)
    yield
    set_service(None)


# ── _session_dir ─────────────────────────────────────────────────


class TestSessionDir:
    def test_default(self, monkeypatch):
        # ``_session_dir`` now derives from ``config_dir() / sessions``
        # when ``KT_SESSION_DIR`` is unset.  Verify the HOME-derived
        # fallback by clearing BOTH env vars (the autouse fixture in
        # conftest.py sets ``KT_CONFIG_DIR`` to tmp).
        monkeypatch.delenv("KT_SESSION_DIR", raising=False)
        monkeypatch.delenv("KT_CONFIG_DIR", raising=False)
        assert _session_dir() == _DEFAULT_SESSION_DIR

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KT_SESSION_DIR", "/custom/path")
        assert _session_dir() == "/custom/path"

    def test_kt_config_dir_overrides_default_subdir(self, tmp_path, monkeypatch):
        # When KT_SESSION_DIR is unset, KT_CONFIG_DIR drives the fallback
        # — pollution-safe by construction.
        monkeypatch.delenv("KT_SESSION_DIR", raising=False)
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "custom-config"))
        assert _session_dir() == str(tmp_path / "custom-config" / "sessions")


# ── set_service / get_service ────────────────────────────────────


class _FakeService:
    def __init__(self):
        self.meta_lookup_set = False

    def set_runtime_graph_meta_lookup(self, fn):
        self.meta_lookup_set = True


class TestServiceSingleton:
    def test_set_and_get_service(self):
        svc = _FakeService()
        set_service(svc)
        assert get_service() is svc

    def test_set_none_clears(self):
        svc = _FakeService()
        set_service(svc)
        set_service(None)
        # get_service will now lazy-instantiate a real LocalTerrariumService,
        # which we don't want to actually do — install another fake first.
        svc2 = _FakeService()
        set_service(svc2)
        assert get_service() is svc2

    def test_get_service_lazy_creates_local(self, monkeypatch):
        """When no service is installed, get_service constructs a default."""
        created = []

        def fake_terrarium(session_dir=None):
            created.append(session_dir)

            class _T:
                pass

            return _T()

        class _Local:
            def __init__(self, engine):
                self.engine = engine
                self.lookup_set = False

            def set_runtime_graph_meta_lookup(self, fn):
                self.lookup_set = True

        monkeypatch.setattr(mod, "Terrarium", fake_terrarium)
        monkeypatch.setattr(mod, "LocalTerrariumService", _Local)
        svc = get_service()
        assert isinstance(svc, _Local)
        assert svc.lookup_set is True
        # Second call returns the same instance.
        assert get_service() is svc


# ── get_engine ───────────────────────────────────────────────────


class _FakeEngine:
    def __init__(self):
        self._runtime_prompt = self
        self.attached = False

    def attach(self):
        self.attached = True


class TestGetEngine:
    def test_get_engine_from_local_service(self, monkeypatch):
        from kohakuterrarium.terrarium import LocalTerrariumService

        engine = _FakeEngine()
        local = LocalTerrariumService.__new__(LocalTerrariumService)
        # ``engine`` is a property; back-fill the private slot instead.
        local._engine = engine
        set_service(local)
        out = get_engine()
        assert out is engine
        assert engine.attached

    def test_get_engine_missing_in_labhost_raises(self):
        # A lab-host service with no coordination engine — get_engine
        # can't fall back to anything, so it raises and the route is
        # told to migrate to Depends(get_service).
        class _Svc:
            pass

        set_service(_Svc())  # No ``coordination_engine`` attribute.
        with pytest.raises(RuntimeError, match="lab-host mode"):
            get_engine()

    def test_get_engine_labhost_uses_coordination_engine(self, monkeypatch):
        # In lab-host mode the host runs no agent engine — get_engine
        # falls back to the (agent-free) coordination engine so
        # unmigrated Depends(get_engine) routes still resolve.
        engine = _FakeEngine()

        class _Svc:
            pass

        svc = _Svc()
        svc.coordination_engine = engine
        set_service(svc)
        out = get_engine()
        assert out is engine

    def test_get_engine_dedupes_warning(self, monkeypatch):
        # First call emits a warning; second from the same line does not.
        engine = _FakeEngine()

        class _Svc:
            pass

        svc = _Svc()
        svc.coordination_engine = engine
        set_service(svc)
        get_engine()
        # Second call should not raise.
        get_engine()
