"""Unit tests for :mod:`kohakuterrarium.api.routes.persistence.resume`."""

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.api.deps import (
    get_service_factory,
    resolve_request_session_dir,
)
from kohakuterrarium.api.routes.persistence import resume as resume_mod
from kohakuterrarium.api.routes.persistence.resume_remote import worker_absolute_for
from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.config_serde import pack_agent_config
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.studio.sessions.handles import Session
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.terrarium import resume as terrarium_resume_mod
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.graph_manifest import MANIFEST_KEY
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.terrarium.workspace_resume import (
    WorkspaceResumeError,
    WorkspaceResumeFailure,
)


class _LocalService:
    pass


@pytest.fixture(autouse=True)
def _skip_real_resume_prepare(monkeypatch):
    monkeypatch.setattr(resume_mod, "prepare_resume_workspace", lambda *a, **k: None)


def _app(
    *,
    engine=None,
    service=None,
    service_factory=None,
    session_dir: Path = Path("/"),
    lab_mode: str = "standalone",
) -> FastAPI:
    app = FastAPI()
    app.state.lab_mode = lab_mode
    resolved = service or engine or _LocalService()
    factory = service_factory or (lambda: resolved)
    app.dependency_overrides[get_service_factory] = lambda: factory
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


def _write_workspace_session(path: Path, valid_pwd: Path, missing_pwd: Path) -> None:
    store = SessionStore(path)
    store.init_meta("saved", "terrarium", "", str(valid_pwd), ["alice", "bob"])
    store.meta[MANIFEST_KEY] = {
        "kind": "kohakuterrarium.live_graph",
        "version": 1,
        "revision": 4,
        "graph_id": "graph-saved",
        "creatures": [
            {
                "creature_id": creature_id,
                "name": name,
                "config_snapshot": pack_agent_config(AgentConfig(name=name)),
                "source_ref": f"@pack/{name}",
                "pwd": str(pwd),
                "is_privileged": name == "alice",
                "parent_creature_id": None,
            }
            for creature_id, name, pwd in (
                ("alice-id", "alice", valid_pwd),
                ("bob-id", "bob", missing_pwd),
            )
        ],
        "channels": [],
        "listen": [],
        "send": [],
    }
    store.close(update_status=False)


# ── _worker_absolute_for ───────────────────────────────────────


class TestWorkerAbsoluteFor:
    def test_expands_under_kohakuterrarium(self, monkeypatch):
        # Verify the HOME-derived fallback, not the autouse env override.
        monkeypatch.delenv("KT_CONFIG_DIR", raising=False)
        out = worker_absolute_for("resume/alice.kohakutr")
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
        client = TestClient(
            _app(
                service=_LabService(),
                session_dir=tmp_path,
                lab_mode="lab-host",
            )
        )

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

        async def fake_resume(
            service,
            saved_path,
            pwd_override=None,
            workspace_overrides=None,
        ):
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
        conversation_id = "shared-conversation"
        for path, session_id in (
            (user_a_path, "saved-a"),
            (user_b_path, "saved-b"),
        ):
            store = SessionStore(path)
            store.init_meta(
                session_id,
                "agent",
                "/cfg",
                str(path.parent),
                ["alice"],
            )
            store.meta["conversation_id"] = conversation_id
            store.close(update_status=False)
        user_a_service = _LocalService()
        user_b_service = _LocalService()
        resumed: list[tuple[object, Path]] = []

        async def fake_resume(
            service,
            path,
            pwd_override=None,
            workspace_overrides=None,
        ):
            resumed.append((service, path))
            return _session(sid=f"sess-{path.parent.name}")

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

    def test_preflight_and_targeted_replacement_are_read_only(
        self, monkeypatch, tmp_path
    ):
        valid_pwd = tmp_path / "valid"
        replacement = tmp_path / "replacement"
        valid_pwd.mkdir()
        replacement.mkdir()
        path = tmp_path / "saved.kohakutr"
        _write_workspace_session(path, valid_pwd, tmp_path / "missing")
        seen_dirs = []

        def resolve(name, session_dir):
            seen_dirs.append(session_dir)
            return path if name == "saved" else None

        def forbidden_factory():
            pytest.fail("local preflight must not construct a runtime service")

        monkeypatch.setattr(resume_mod, "resolve_session_path_in", resolve)
        client = TestClient(
            _app(
                service_factory=forbidden_factory,
                session_dir=tmp_path / "request-sessions",
            )
        )

        unresolved = client.post("/sessions/saved/resume/preflight")
        assert unresolved.status_code == 200
        assert unresolved.json()["ready"] is False
        assert unresolved.json()["gaps"][0]["creature_ids"] == ["bob-id"]

        resolved = client.post(
            "/sessions/saved/resume/preflight",
            json={"workspace_overrides": {"bob-id": str(replacement)}},
        )
        assert resolved.status_code == 200
        body = resolved.json()
        assert body["ready"] is True
        assert {
            member["creature_id"]: member["saved_pwd"] for member in body["members"]
        } == {
            "alice-id": str(valid_pwd),
            "bob-id": str(replacement),
        }
        assert seen_dirs == [
            tmp_path / "request-sessions",
            tmp_path / "request-sessions",
        ]

    def test_preflight_reports_partial_dirty_as_conflict(self, monkeypatch, tmp_path):
        valid_pwd = tmp_path / "valid"
        valid_pwd.mkdir()
        path = tmp_path / "dirty.kohakutr"
        _write_workspace_session(path, valid_pwd, tmp_path / "missing")
        store = SessionStore(path)
        store.meta["workspace_resume_state"] = {"status": "partial_dirty"}
        store.close(update_status=False)
        monkeypatch.setattr(resume_mod, "resolve_session_path_in", lambda *_args: path)

        response = TestClient(_app(service_factory=lambda: None)).post(
            "/sessions/dirty/resume/preflight"
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "stale_manifest"

    def test_legacy_replacement_survives_resume_stop_preflight(
        self, monkeypatch, tmp_path
    ):
        """Exercise the UI's preflight -> resume -> stop -> preflight lifecycle."""
        old_pwd = tmp_path / "deleted-workspace"
        replacement = tmp_path / "relocated"
        replacement.mkdir()
        path = tmp_path / "saved.kohakutr"
        store = SessionStore(path)
        store.init_meta(
            "saved",
            "agent",
            "",
            str(old_pwd),
            ["alice"],
            config_snapshot=pack_agent_config(AgentConfig(name="alice")),
        )
        store.close(update_status=False)

        monkeypatch.setattr(resume_mod, "resolve_session_path_in", lambda *_args: path)
        monkeypatch.setattr(
            resume_mod,
            "prepare_resume_workspace",
            terrarium_resume_mod.prepare_resume_workspace,
        )

        def scripted_provider(*_args, **_kwargs):
            return ScriptedLLM(["ok"])

        monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", scripted_provider)
        monkeypatch.setattr(_agent_init, "create_llm_provider", scripted_provider)

        engine = Terrarium(drive_config=DriveRuntimeConfig(enabled=False))
        service = LocalTerrariumService(engine)
        with TestClient(_app(service=service, session_dir=tmp_path)) as client:
            initial = client.post("/sessions/saved/resume/preflight")
            assert initial.status_code == 200
            assert initial.json()["legacy"] is True
            assert initial.json()["ready"] is False

            resumed = client.post(
                "/sessions/saved/resume",
                json={"pwd": str(replacement)},
            )
            assert resumed.status_code == 200, resumed.text
            sid = resumed.json()["instance_id"]

            client.portal.call(lifecycle.stop_session, service, sid)

            second = client.post("/sessions/saved/resume/preflight")
            assert second.status_code == 200
            assert second.json()["legacy"] is True
            assert second.json()["ready"] is True
            client.portal.call(engine.shutdown)

        reopened = SessionStore(path)
        try:
            assert reopened.load_meta()["pwd"] == str(replacement.resolve())
        finally:
            reopened.close(update_status=False)

    def test_local_resume_orders_preflight_before_service_and_forwards_overrides(
        self, monkeypatch, tmp_path
    ):
        path = tmp_path / "saved.kohakutr"
        events = []
        service = _LocalService()

        def resolve(name, session_dir):
            events.append(("resolve", name, session_dir))
            return path

        def prepare(target, **kwargs):
            events.append(("preflight", target, kwargs))

        def factory():
            events.append(("service",))
            return service

        async def fake_resume(
            actual_service, target, pwd_override=None, workspace_overrides=None
        ):
            events.append(
                (
                    "resume",
                    actual_service,
                    target,
                    pwd_override,
                    workspace_overrides,
                )
            )
            return _session()

        monkeypatch.setattr(resume_mod, "resolve_session_path_in", resolve)
        monkeypatch.setattr(resume_mod, "prepare_resume_workspace", prepare)
        monkeypatch.setattr(resume_mod, "studio_resume", fake_resume)
        session_dir = tmp_path / "request-sessions"
        client = TestClient(_app(service_factory=factory, session_dir=session_dir))

        response = client.post(
            "/sessions/saved/resume",
            json={"workspace_overrides": {"bob-id": str(tmp_path)}},
        )

        assert response.status_code == 200
        assert [event[0] for event in events] == [
            "resolve",
            "preflight",
            "service",
            "resume",
        ]
        assert events[0] == ("resolve", "saved", session_dir)
        assert events[1][2] == {
            "pwd": None,
            "workspace_overrides": {"bob-id": str(tmp_path)},
        }
        assert events[3][1:] == (
            service,
            path,
            None,
            {"bob-id": str(tmp_path)},
        )

    def test_unresolved_preflight_has_no_runtime_side_effect(
        self, monkeypatch, tmp_path
    ):
        path = tmp_path / "saved.kohakutr"
        calls = []
        monkeypatch.setattr(resume_mod, "resolve_session_path_in", lambda *_args: path)

        def unresolved(*_args, **_kwargs):
            calls.append("preflight")
            raise WorkspaceResumeError(
                WorkspaceResumeFailure.UNRESOLVED,
                "replacement required",
                creature_ids=("bob-id",),
            )

        def forbidden_factory():
            calls.append("service")
            return _LocalService()

        async def forbidden_resume(*_args, **_kwargs):
            calls.append("resume")
            return _session()

        monkeypatch.setattr(resume_mod, "prepare_resume_workspace", unresolved)
        monkeypatch.setattr(resume_mod, "studio_resume", forbidden_resume)
        client = TestClient(_app(service_factory=forbidden_factory))

        response = client.post("/sessions/saved/resume")

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "unresolved"
        assert calls == ["preflight"]

    def test_lab_host_rejected_before_path_resolution(self, monkeypatch):
        calls = []

        def forbidden_resolve(*_args):
            calls.append("resolve")
            return None

        def forbidden_factory():
            calls.append("service")
            return _LocalService()

        monkeypatch.setattr(resume_mod, "resolve_session_path_in", forbidden_resolve)
        app = _app(service_factory=forbidden_factory)
        app.state.lab_mode = "lab-host"

        response = TestClient(app).post("/sessions/ghost/resume")

        assert response.status_code == 400
        assert calls == []

    def test_session_missing(self, monkeypatch):
        monkeypatch.setattr(
            resume_mod, "resolve_session_path_in", lambda n, _session_dir: None
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

        async def fake_resume(
            engine, path, pwd_override=None, workspace_overrides=None
        ):
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

        async def fake_resume(
            engine, path, pwd_override=None, workspace_overrides=None
        ):
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

        async def boom(engine, path, pwd_override=None, workspace_overrides=None):
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

        async def boom(engine, path, pwd_override=None, workspace_overrides=None):
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

        async def fake_resume(
            engine, path, pwd_override=None, workspace_overrides=None
        ):
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

        async def fake_resume(
            engine, path, pwd_override=None, workspace_overrides=None
        ):
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
