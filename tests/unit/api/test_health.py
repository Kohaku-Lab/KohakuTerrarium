"""Unit tests for the ``/healthz`` and ``/readyz`` endpoints.

These endpoints drive Docker HEALTHCHECK, reverse-proxy active health
probes, and load-balancer rotation, so the status-code contract is
load-bearing.  Each test wires only the health routers onto a
minimal :class:`FastAPI` app — no lifespan, no real HostEngine — so a
``lab-host`` simulation does not require an actual Lab transport.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes import health as health_route
from kohakuterrarium.api.routes import lab_status as lab_status_route


def _mini_app(*, lab_mode: str = "standalone", with_engine: bool = False) -> FastAPI:
    app = FastAPI()
    app.state.lab_mode = lab_mode
    app.state.lab_bind = "127.0.0.1:8100"
    if with_engine:
        # The readyz path only checks for *presence* + a falsy
        # ``is_running`` attribute; a stub satisfies the contract
        # without standing up a real WebSocket server.
        class _StubHost:
            is_running = True

        app.state.lab_host_engine = _StubHost()
    app.include_router(health_route.router)
    app.include_router(lab_status_route.router, prefix="/api/lab")
    return app


class TestHealthz:
    def test_standalone_healthz_ok(self):
        with TestClient(_mini_app()) as c:
            r = c.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_lab_host_healthz_ok_even_without_engine(self):
        """Liveness must not depend on the lab transport — only on the
        process being up.  Switching to ``lab-host`` mode without an
        engine must still return 200.
        """
        with TestClient(_mini_app(lab_mode="lab-host")) as c:
            r = c.get("/healthz")
        assert r.status_code == 200


class TestReadyz:
    def test_standalone_readyz_ready(self):
        with TestClient(_mini_app()) as c:
            r = c.get("/readyz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ready"
        assert body["mode"] == "standalone"

    def test_lab_host_readyz_503_without_engine(self):
        """In ``lab-host`` mode with no HostEngine attached, readyz
        returns 503 so a reverse-proxy keeps traffic away while the
        AIO entry script is still mid-boot.
        """
        with TestClient(_mini_app(lab_mode="lab-host")) as c:
            r = c.get("/readyz")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "not_ready"
        assert body["mode"] == "lab-host"

    def test_lab_host_readyz_200_with_running_engine(self):
        with TestClient(_mini_app(lab_mode="lab-host", with_engine=True)) as c:
            r = c.get("/readyz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ready"
        assert body["lab_bind"] == "127.0.0.1:8100"


class TestLabStatus:
    def test_standalone_lab_status_empty(self):
        with TestClient(_mini_app()) as c:
            r = c.get("/api/lab/status")
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "standalone"
        assert body["lab_bind"] is None
        assert body["clients"] == []

    def test_lab_host_lab_status_reports_bind(self):
        with TestClient(_mini_app(lab_mode="lab-host")) as c:
            r = c.get("/api/lab/status")
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "lab-host"
        assert body["lab_bind"] == "127.0.0.1:8100"
