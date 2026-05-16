"""Unit tests for :mod:`kohakuterrarium.api.routes.persistence.resume`."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_engine, get_service
from kohakuterrarium.api.routes.persistence import resume as resume_mod
from kohakuterrarium.studio.sessions.handles import Session


class _LocalEngine:
    pass


class _LocalService:
    pass


def _app(*, engine=None, service=None) -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_engine] = lambda: engine or _LocalEngine()
    app.dependency_overrides[get_service] = lambda: service or _LocalService()
    app.include_router(resume_mod.router, prefix="/sessions")
    return app


def _session(*, sid="sess-1", name="alice", creatures=None):
    return Session(
        session_id=sid,
        name=name,
        creatures=creatures or [{"creature_id": "cid-1", "name": "alice"}],
        channels=[],
        has_root=False,
    )


# ── _worker_absolute_for ───────────────────────────────────────


class TestWorkerAbsoluteFor:
    def test_expands_under_kohakuterrarium(self, monkeypatch):
        # Verify the HOME-derived fallback, not the autouse env override.
        monkeypatch.delenv("KT_CONFIG_DIR", raising=False)
        out = resume_mod._worker_absolute_for("resume/alice.kohakutr")
        # Path-style ends with the relative.
        assert "alice.kohakutr" in out
        assert ".kohakuterrarium" in out


# ── host-mode resume ───────────────────────────────────────────


class TestHostResume:
    def test_session_missing(self, monkeypatch):
        monkeypatch.setattr(resume_mod, "resolve_session_path_default", lambda n: None)
        client = TestClient(_app())
        resp = client.post("/sessions/ghost/resume")
        assert resp.status_code == 404

    def test_host_success_agent(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )

        async def fake_resume(engine, path):
            return _session()

        monkeypatch.setattr(resume_mod, "studio_resume", fake_resume)
        client = TestClient(_app())
        resp = client.post("/sessions/sess/resume")
        assert resp.status_code == 200
        body = resp.json()
        assert body["type"] == "agent"
        assert body["instance_id"] == "sess-1"

    def test_host_success_terrarium(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )

        async def fake_resume(engine, path):
            return _session(
                creatures=[
                    {"creature_id": "c1", "name": "alice"},
                    {"creature_id": "c2", "name": "bob"},
                ]
            )

        monkeypatch.setattr(resume_mod, "studio_resume", fake_resume)
        client = TestClient(_app())
        resp = client.post("/sessions/sess/resume")
        body = resp.json()
        assert body["type"] == "terrarium"

    def test_file_not_found_404(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )

        async def boom(engine, path):
            raise FileNotFoundError("no such file")

        monkeypatch.setattr(resume_mod, "studio_resume", boom)
        client = TestClient(_app())
        resp = client.post("/sessions/sess/resume")
        assert resp.status_code == 404

    def test_value_error_400(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )

        async def boom(engine, path):
            raise ValueError("bad payload")

        monkeypatch.setattr(resume_mod, "studio_resume", boom)
        client = TestClient(_app())
        resp = client.post("/sessions/sess/resume")
        assert resp.status_code == 400

    def test_default_on_node_is_host(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )

        called_with = {}

        async def fake_resume(engine, path):
            called_with["path"] = path
            return _session()

        monkeypatch.setattr(resume_mod, "studio_resume", fake_resume)
        client = TestClient(_app())
        # No body → defaults to _host.
        resp = client.post("/sessions/sess/resume")
        assert resp.status_code == 200
        assert called_with["path"] == Path("/x/s.kohakutr")


# ── remote-node resume ─────────────────────────────────────────


class TestRemoteResume:
    def test_no_lab_host(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )
        # Service has no `.host` attribute → 404.
        client = TestClient(_app())
        resp = client.post("/sessions/sess/resume", json={"on_node": "w1"})
        assert resp.status_code == 404

    def test_unknown_node(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )

        class _Svc:
            host = object()

            def connected_nodes(self):
                return ("_host",)

        client = TestClient(_app(service=_Svc()))
        resp = client.post("/sessions/sess/resume", json={"on_node": "w1"})
        assert resp.status_code == 404
