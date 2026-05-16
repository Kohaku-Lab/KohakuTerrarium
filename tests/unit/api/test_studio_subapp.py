"""Unit tests for :mod:`kohakuterrarium.api.studio`."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api import studio as studio_app
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.studio import deps as deps_mod
from kohakuterrarium.api.studio.routes import meta as meta_mod
from kohakuterrarium.api.studio.utils import paths as paths_mod


class _LocalService:
    pass


class _MultiService:
    def connected_nodes(self):
        return ("_host", "w1", "w2")


# ── build_studio_router smoke ──────────────────────────────────


class TestBuildStudioRouter:
    def test_can_be_built_and_mounted(self):
        router = studio_app.build_studio_router()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_service] = lambda: _LocalService()
        client = TestClient(app)
        resp = client.get("/api/studio/meta/health")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


# ── meta route ─────────────────────────────────────────────────


class TestMetaRoutes:
    def _client(self, service):
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: service
        app.include_router(meta_mod.router)
        return TestClient(app)

    def test_health(self):
        client = self._client(_LocalService())
        resp = client.get("/health")
        assert resp.json() == {"ok": True}

    def test_version_standalone(self):
        client = self._client(_LocalService())
        resp = client.get("/version")
        body = resp.json()
        assert body["mode"] == "standalone"
        assert body["node_count"] == 1
        assert body["studio"] == meta_mod.STUDIO_VERSION

    def test_version_lab_host(self):
        client = self._client(_MultiService())
        body = client.get("/version").json()
        assert body["mode"] == "lab-host"
        assert body["node_count"] == 3

    def test_core_version_helper(self):
        # When the package is installed, _core_version returns its
        # exact reported version — not just "some string".
        from importlib.metadata import version as _pkg_version

        assert meta_mod._core_version() == _pkg_version("kohakuterrarium")

    def test_core_version_handles_missing(self, monkeypatch):
        from importlib.metadata import PackageNotFoundError

        def boom(name):
            raise PackageNotFoundError(name)

        monkeypatch.setattr(meta_mod, "_pkg_version", boom)
        assert meta_mod._core_version() == "unknown"


# ── studio/utils/paths re-export shim ──────────────────────────


class TestPathsShim:
    def test_reexports_exist(self):
        assert hasattr(paths_mod, "UnsafePath")
        assert hasattr(paths_mod, "ensure_in_root")
        assert hasattr(paths_mod, "sanitize_name")


# ── studio/deps re-export shim ─────────────────────────────────


class TestDepsShim:
    def test_reexports_exist(self):
        assert hasattr(deps_mod, "Workspace")
        assert hasattr(deps_mod, "get_workspace")
        assert hasattr(deps_mod, "get_workspace_optional")
        assert hasattr(deps_mod, "set_workspace")


# ── __init__ surface ───────────────────────────────────────────


class TestPackageSurface:
    def test_all_contains_build_studio_router(self):
        assert "build_studio_router" in studio_app.__all__
        assert callable(studio_app.build_studio_router)
