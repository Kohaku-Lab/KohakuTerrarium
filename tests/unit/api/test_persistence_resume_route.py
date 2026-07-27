"""Unit tests for :mod:`kohakuterrarium.api.routes.persistence.resume`."""

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
import pytest

from kohakuterrarium.api.deps import (
    get_service,
    resolve_request_session_dir,
)
from kohakuterrarium.api.routes.persistence import resume as resume_mod
from kohakuterrarium.studio.sessions.handles import Session


class _LocalService:
    pass


def _app(*, service=None, session_dir=None) -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: (
        service if service is not None else _LocalService()
    )
    if session_dir is not None:
        app.dependency_overrides[resolve_request_session_dir] = lambda: session_dir
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
    def test_lab_host_target_rejected_before_path_resolution(
        self, tmp_path, monkeypatch
    ):
        class _LabService:
            def connected_nodes(self):
                return ()

        def unexpected_resolution(*args, **kwargs):
            raise AssertionError("host-target rejection must happen before path lookup")

        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            unexpected_resolution,
        )
        client = TestClient(_app(service=_LabService(), session_dir=tmp_path))

        response = client.post("/sessions/missing/resume")

        assert response.status_code == 400
        assert "runs no agents on the host" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_concurrent_requests_share_one_underlying_resume(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "shared.kohakutr"
        path.write_bytes(b"saved")
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fake_resume(service, saved_path, pwd_override=None):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return _session()

        monkeypatch.setattr(resume_mod, "studio_resume", fake_resume)
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: path,
        )
        transport = ASGITransport(app=_app(session_dir=tmp_path))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.post("/sessions/shared/resume"))
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set()
            second = asyncio.create_task(client.post("/sessions/alias/resume"))
            await asyncio.sleep(0)

            assert calls == 1
            release.set()
            responses = await asyncio.gather(first, second)

        assert [response.status_code for response in responses] == [200, 200]

    @pytest.mark.asyncio
    async def test_conflicting_target_returns_409(self, tmp_path, monkeypatch):
        path = tmp_path / "shared.kohakutr"
        path.write_bytes(b"session")
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_impl(*args, **kwargs):
            started.set()
            await release.wait()
            return {"instance_id": "sid-conflict"}

        monkeypatch.setattr(resume_mod, "_resume_session", fake_impl)
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: path,
        )
        transport = ASGITransport(app=_app(session_dir=tmp_path))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.post("/sessions/shared/resume"))
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set()
            conflict = await client.post(
                "/sessions/shared/resume",
                json={
                    "on_node": "_host",
                    "members": [{"sid": "other", "on_node": "worker-b"}],
                },
            )
            release.set()
            successful = await first

        assert conflict.status_code == 409
        assert successful.status_code == 200

    def test_same_saved_name_is_isolated_by_request_directory_and_service(
        self, tmp_path, monkeypatch
    ):
        user_a_dir = tmp_path / "user-a"
        user_b_dir = tmp_path / "user-b"
        user_a_dir.mkdir()
        user_b_dir.mkdir()
        user_a_path = user_a_dir / "shared.kohakutr"
        user_b_path = user_b_dir / "shared.kohakutr"
        user_a_path.write_bytes(b"a")
        user_b_path.write_bytes(b"b")
        user_a_service = _LocalService()
        user_b_service = _LocalService()
        resumed: list[tuple[object, Path]] = []

        async def fake_resume(service, path, pwd_override=None):
            resumed.append((service, path))
            return _session(sid=f"sess-{path.read_text()}")

        monkeypatch.setattr(resume_mod, "studio_resume", fake_resume)

        user_a = TestClient(_app(service=user_a_service, session_dir=user_a_dir)).post(
            "/sessions/shared/resume"
        )
        user_b = TestClient(_app(service=user_b_service, session_dir=user_b_dir)).post(
            "/sessions/shared/resume"
        )

        assert user_a.status_code == 200
        assert user_b.status_code == 200
        assert resumed == [
            (user_a_service, user_a_path),
            (user_b_service, user_b_path),
        ]

    def test_session_missing(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod, "resolve_session_path_in", lambda name, session_dir: None
        )
        client = TestClient(_app())
        resp = client.post("/sessions/ghost/resume")
        assert resp.status_code == 404

    def test_host_success_agent(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: Path("/x/s.kohakutr"),
        )

        async def fake_resume(engine, path, pwd_override=None):
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
            "resolve_session_path_in",
            lambda name, session_dir: Path("/x/s.kohakutr"),
        )

        async def fake_resume(engine, path, pwd_override=None):
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
            "resolve_session_path_in",
            lambda name, session_dir: Path("/x/s.kohakutr"),
        )

        async def boom(engine, path, pwd_override=None):
            raise FileNotFoundError("no such file")

        monkeypatch.setattr(resume_mod, "studio_resume", boom)
        client = TestClient(_app())
        resp = client.post("/sessions/sess/resume")
        assert resp.status_code == 404

    def test_value_error_400(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: Path("/x/s.kohakutr"),
        )

        async def boom(engine, path, pwd_override=None):
            raise ValueError("bad payload")

        monkeypatch.setattr(resume_mod, "studio_resume", boom)
        client = TestClient(_app())
        resp = client.post("/sessions/sess/resume")
        assert resp.status_code == 400

    def test_default_on_node_is_host(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: Path("/x/s.kohakutr"),
        )

        called_with = {}

        async def fake_resume(engine, path, pwd_override=None):
            called_with["path"] = path
            called_with["pwd_override"] = pwd_override
            return _session()

        monkeypatch.setattr(resume_mod, "studio_resume", fake_resume)
        client = TestClient(_app())
        # No body → defaults to _host.
        resp = client.post("/sessions/sess/resume")
        assert resp.status_code == 200
        assert called_with["path"] == Path("/x/s.kohakutr")
        assert called_with["pwd_override"] is None

    def test_host_resume_threads_pwd_override(self, monkeypatch):
        # The replacement dir must reach adopt_session BEFORE creatures
        # start — not be patched on afterwards.
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: Path("/x/s.kohakutr"),
        )

        called_with = {}

        async def fake_resume(engine, path, pwd_override=None):
            called_with["pwd_override"] = pwd_override
            return _session()

        monkeypatch.setattr(resume_mod, "studio_resume", fake_resume)
        client = TestClient(_app())
        resp = client.post("/sessions/sess/resume", json={"pwd": "/new/dir"})
        assert resp.status_code == 200
        assert called_with["pwd_override"] == "/new/dir"


# ── remote-node resume ─────────────────────────────────────────


class TestRemoteResume:
    def test_no_lab_host(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: Path("/x/s.kohakutr"),
        )
        # Service has no `.host` attribute → 404.
        client = TestClient(_app())
        resp = client.post("/sessions/sess/resume", json={"on_node": "w1"})
        assert resp.status_code == 404

    def test_unknown_node(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: Path("/x/s.kohakutr"),
        )

        class _Svc:
            host = object()

            def connected_nodes(self):
                return ("_host",)

        client = TestClient(_app(service=_Svc()))
        resp = client.post("/sessions/sess/resume", json={"on_node": "w1"})
        assert resp.status_code == 404
