"""Remote-node resume path tests for :mod:`api.routes.persistence.resume`."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.persistence import resume as resume_mod
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.studio.sessions.registry import meta_for


def _app(*, service=None):
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service or SimpleNamespace()
    app.include_router(resume_mod.router, prefix="/sessions")
    return app


class _FakeHost:
    # The resume route pushes the ``.kohakutr`` via the chunked
    # ``write_stream`` family (write_begin / write_chunk / write_commit)
    # rather than a one-shot ``write`` — a session file routinely
    # exceeds the Lab transport frame ceiling. Default responses make
    # that handshake succeed; a test overrides them to exercise a
    # specific failure.
    _STREAM_DEFAULTS = {
        "terrarium.files:write_begin": {
            "transfer_id": "tid-1",
            "chunk_size": 262144,
        },
        "terrarium.files:write_chunk": {"received": 0},
        "terrarium.files:write_commit": {"written": 0, "sha256": ""},
    }

    def __init__(self, responses=None, raises=None):
        self._responses = dict(self._STREAM_DEFAULTS)
        self._responses.update(responses or {})
        self._raises = raises or {}
        self.calls = []

    async def request(self, *, to_node, namespace, type, body, timeout):
        self.calls.append(
            {"namespace": namespace, "type": type, "to": to_node, "body": body}
        )
        key = f"{namespace}:{type}"
        if key in self._raises:
            raise self._raises[key]
        return self._responses.get(key, {})


class _Svc:
    def __init__(self, host, nodes=("w1",)):
        self.host = host
        self._nodes = nodes

    def connected_nodes(self):
        return tuple(self._nodes)


class TestRemoteWritePath:
    def test_file_read_error(self, monkeypatch, tmp_path):
        # The request-scoped resolver returns a path that doesn't exist.
        ghost = tmp_path / "missing.kohakutr"
        monkeypatch.setattr(
            resume_mod, "resolve_session_path_in", lambda name, session_dir: ghost
        )
        svc = _Svc(_FakeHost())
        client = TestClient(_app(service=svc))
        resp = client.post("/sessions/x/resume", json={"on_node": "w1"})
        assert resp.status_code == 404

    def test_write_response_error(self, monkeypatch, tmp_path):
        p = tmp_path / "x.kohakutr"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            resume_mod, "resolve_session_path_in", lambda name, session_dir: p
        )
        host = _FakeHost(
            responses={"terrarium.files:stat": {"error": {"message": "no write"}}}
        )
        client = TestClient(_app(service=_Svc(host)))
        resp = client.post("/sessions/x/resume", json={"on_node": "w1"})
        assert resp.status_code == 502

    def test_resume_response_error(self, monkeypatch, tmp_path):
        p = tmp_path / "x.kohakutr"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            resume_mod, "resolve_session_path_in", lambda name, session_dir: p
        )
        host = _FakeHost(
            responses={
                "terrarium.files:stat": {
                    "stat": {"path": "C:/worker-config/resume/x.kohakutr"}
                },
                "terrarium.session:resume": {"error": {"message": "bad resume"}},
            }
        )
        client = TestClient(_app(service=_Svc(host)))
        resp = client.post("/sessions/x/resume", json={"on_node": "w1"})
        assert resp.status_code == 502

    def test_transport_exception(self, monkeypatch, tmp_path):
        # The chunked push raises mid-handshake (write_begin) — the
        # route must surface a clean 502, not propagate the error.
        p = tmp_path / "x.kohakutr"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            resume_mod, "resolve_session_path_in", lambda name, session_dir: p
        )
        host = _FakeHost(
            raises={"terrarium.files:write_begin": RuntimeError("transport down")}
        )
        client = TestClient(_app(service=_Svc(host)))
        resp = client.post("/sessions/x/resume", json={"on_node": "w1"})
        assert resp.status_code == 502

    def test_remote_success(self, monkeypatch, tmp_path):
        p = tmp_path / "x.kohakutr"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            resume_mod, "resolve_session_path_in", lambda name, session_dir: p
        )
        host = _FakeHost(
            responses={
                "terrarium.files:stat": {
                    "stat": {"path": "D:/worker-config/resume/x.kohakutr"}
                },
                "terrarium.session:resume": {
                    "session_id": "remote-sid",
                    "meta": {
                        "config_type": "terrarium",
                        "terrarium_name": "remote-t",
                        "agents": ["alice", "bob"],
                        "pwd": "/p",
                        "terrarium_creatures": [{"name": "x"}],
                        "conversation_id": "conversation-remote",
                    },
                    "creatures": [
                        {
                            "creature_id": "cid-alice",
                            "name": "alice",
                            "running": True,
                            "is_privileged": False,
                        },
                        {
                            "creature_id": "cid-bob",
                            "name": "bob",
                            "running": True,
                            "is_privileged": False,
                        },
                    ],
                },
            }
        )

        class _RosterSvc(_Svc):
            async def list_creatures(self):
                raise RuntimeError("controller roster refresh unavailable")

        svc = _RosterSvc(host)
        client = TestClient(_app(service=svc))
        resp = client.post("/sessions/x/resume", json={"on_node": "w1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["instance_id"] == "remote-sid"
        assert body["type"] == "terrarium"
        assert body["on_node"] == "w1"
        assert lifecycle.meta_for(svc)["remote-sid"]["conversation_id"] == (
            "conversation-remote"
        )
        assert lifecycle.meta_for(svc)["remote-sid"]["remote_session_path"] == (
            "D:/worker-config/resume/x.kohakutr"
        )
        assert lifecycle.meta_for(svc)["remote-sid"]["creature_ids"] == [
            "cid-alice",
            "cid-bob",
        ]
        resume_call = next(
            call
            for call in host.calls
            if call["namespace"] == "terrarium.session" and call["type"] == "resume"
        )
        assert resume_call["body"]["path"] == "D:/worker-config/resume/x.kohakutr"
        # The push went through the chunked write_stream handshake, not
        # a one-shot ``write`` — that is the whole point of the pack
        # system: no single APP message can overflow the transport.
        pushed = [c["type"] for c in host.calls if c["namespace"] == "terrarium.files"]
        assert "write_begin" in pushed
        assert "write_commit" in pushed
        assert "write" not in pushed

    def test_pwd_override_threads_to_worker(self, monkeypatch, tmp_path):
        p = tmp_path / "x.kohakutr"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            resume_mod, "resolve_session_path_in", lambda name, session_dir: p
        )
        host = _FakeHost(
            responses={
                "terrarium.files:stat": {
                    "stat": {"path": "C:/worker-config/resume/x.kohakutr"}
                },
                "terrarium.session:resume": {
                    "session_id": "sid",
                    "meta": {"agents": ["a"], "pwd": "/p"},
                },
            }
        )
        client = TestClient(_app(service=_Svc(host)))
        resp = client.post(
            "/sessions/x/resume", json={"on_node": "w1", "pwd": "/new/dir"}
        )
        assert resp.status_code == 200
        resume_calls = [c for c in host.calls if c["namespace"] == "terrarium.session"]
        assert resume_calls[0]["body"]["pwd_override"] == "/new/dir"

    def test_worker_pwd_exists_overrides_controller_stat(self, monkeypatch, tmp_path):
        # meta pwd "" makes the controller-side __post_init__ compute
        # True — but the session lives on the worker, whose report of a
        # missing dir must win.
        p = tmp_path / "x.kohakutr"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            resume_mod, "resolve_session_path_in", lambda name, session_dir: p
        )
        host = _FakeHost(
            responses={
                "terrarium.files:stat": {
                    "stat": {"path": "C:/worker-config/resume/x.kohakutr"}
                },
                "terrarium.session:resume": {
                    "session_id": "sid",
                    "meta": {"agents": ["a"], "pwd": ""},
                    "pwd_exists": False,
                },
            }
        )
        client = TestClient(_app(service=_Svc(host)))
        resp = client.post("/sessions/x/resume", json={"on_node": "w1"})
        assert resp.status_code == 200
        assert resp.json()["session"]["pwd_exists"] is False

    def test_failed_worker_resume_rolls_back_by_transferred_path(
        self, monkeypatch, tmp_path
    ):
        p = tmp_path / "x.kohakutr"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            resume_mod, "resolve_session_path_in", lambda name, session_dir: p
        )

        class _FailingHost(_FakeHost):
            async def request(self, *, to_node, namespace, type, body, timeout):
                self.calls.append(
                    {
                        "to": to_node,
                        "namespace": namespace,
                        "type": type,
                        "body": body,
                    }
                )
                if namespace == "terrarium.session" and type == "resume":
                    raise RuntimeError("worker resume failed")
                if namespace == "terrarium.session" and type == "rollback_resume":
                    return {"ok": True}
                if namespace == "terrarium.files" and type == "stat":
                    return {"stat": {"path": f"C:/worker-config/{body['path']}"}}
                return self._responses.get(f"{namespace}:{type}", {})

        host = _FailingHost()
        response = TestClient(_app(service=_Svc(host))).post(
            "/sessions/x/resume", json={"on_node": "w1"}
        )

        assert response.status_code == 502
        cleanup = [
            call
            for call in host.calls
            if call["namespace"] == "terrarium.session"
            and call["type"] == "rollback_resume"
        ]
        resume_token = next(
            call["body"]["resume_token"]
            for call in host.calls
            if call["namespace"] == "terrarium.session" and call["type"] == "resume"
        )
        assert cleanup == [
            {
                "to": "w1",
                "namespace": "terrarium.session",
                "type": "rollback_resume",
                "body": {
                    "session_path": "C:/worker-config/resume/x.kohakutr",
                    "resume_token": resume_token,
                },
            }
        ]

    def test_remote_no_session_id_502(self, monkeypatch, tmp_path):
        p = tmp_path / "x.kohakutr"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            resume_mod, "resolve_session_path_in", lambda name, session_dir: p
        )
        host = _FakeHost(
            responses={
                "terrarium.files:stat": {
                    "stat": {"path": "C:/worker-config/resume/x.kohakutr"}
                },
                "terrarium.session:resume": {"meta": {}},
            }
        )
        client = TestClient(_app(service=_Svc(host)))
        resp = client.post("/sessions/x/resume", json={"on_node": "w1"})
        assert resp.status_code == 502
        cleanup = [
            call
            for call in host.calls
            if call["namespace"] == "terrarium.session"
            and call["type"] == "rollback_resume"
        ]
        resume_token = next(
            call["body"]["resume_token"]
            for call in host.calls
            if call["namespace"] == "terrarium.session" and call["type"] == "resume"
        )
        assert cleanup == [
            {
                "namespace": "terrarium.session",
                "type": "rollback_resume",
                "to": "w1",
                "body": {
                    "session_path": "C:/worker-config/resume/x.kohakutr",
                    "resume_token": resume_token,
                },
            }
        ]

    def test_remote_resume_requires_worker_reported_absolute_path(
        self, monkeypatch, tmp_path
    ):
        p = tmp_path / "x.kohakutr"
        p.write_bytes(b"data")
        monkeypatch.setattr(
            resume_mod, "resolve_session_path_in", lambda name, session_dir: p
        )
        host = _FakeHost(
            responses={
                "terrarium.files:stat": {"ok": True},
                "terrarium.session:resume": {
                    "session_id": "must-not-run",
                    "meta": {"agents": ["alice"]},
                },
            }
        )

        response = TestClient(_app(service=_Svc(host))).post(
            "/sessions/x/resume", json={"on_node": "w1"}
        )

        assert response.status_code == 502
        assert not any(
            call["namespace"] == "terrarium.session" and call["type"] == "resume"
            for call in host.calls
        )
        assert any(
            call["namespace"] == "terrarium.files"
            and call["type"] == "delete"
            and call["body"] == {"scope": "config://", "path": "resume/x.kohakutr"}
            for call in host.calls
        )


# ---------------------------------------------------------------------------
# CF-6 — cluster resume
# ---------------------------------------------------------------------------


class _ClusterSvc(_Svc):
    """``_Svc`` extended with a recording ``connect`` so the cluster
    test can assert the relink fires once per non-primary member."""

    def __init__(self, host, nodes=("w1", "w2")):
        super().__init__(host, nodes=nodes)
        self.connect_calls: list[tuple[str, str]] = []
        # Mirror the home registry the resume route updates.  Roster
        # entries are shaped to mimic ``CreatureInfo``.
        self._roster: list = []

    async def connect(self, sender_id, receiver_id, *, channel=None):
        self.connect_calls.append((sender_id, receiver_id))
        return SimpleNamespace(channel=channel or "auto", graph_id=sender_id)

    async def list_creatures(self):
        return tuple(self._roster)


def _ci(creature_id, name, graph_id, *, is_running=True):
    """Build a minimal stand-in for ``CreatureInfo``."""
    return SimpleNamespace(
        creature_id=creature_id,
        name=name,
        graph_id=graph_id,
        is_running=is_running,
        is_privileged=True,
    )


class TestClusterResume:
    """CF-6 — multi-worker cluster resume.

    On request body ``{members: [{sid, on_node}, ...]}``: each member's
    ``.kohakutr`` is pushed to its own worker, every worker resume RPC
    fires, and ``service.connect()`` is invoked between the primary
    creature and every peer so ``_cluster_links`` is repopulated.
    """

    def test_cluster_resume_pushes_to_every_worker_and_relinks(
        self, monkeypatch, tmp_path
    ):
        # Two saved mirror files — one per member.  Names match each
        # member's sid so the route's per-member path resolution lands
        # them deterministically.
        sid_a, sid_b = "sid-a", "sid-b"
        pa = tmp_path / f"{sid_a}.kohakutr"
        pb = tmp_path / f"{sid_b}.kohakutr"
        pa.write_bytes(b"alpha")
        pb.write_bytes(b"bravo")
        paths = {sid_a: pa, sid_b: pb}
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: paths.get(name),
        )
        # Worker resume RPCs each return a fresh new sid + meta carrying
        # the agents list. Per-worker dispatch is keyed on `to_node`.
        per_node_resume = {
            "w1": {
                "session_id": "new-a",
                "meta": {"agents": ["alpha"], "config_type": "agent"},
            },
            "w2": {
                "session_id": "new-b",
                "meta": {"agents": ["bravo"], "config_type": "agent"},
            },
        }

        class _RoutedHost(_FakeHost):
            async def request(self, *, to_node, namespace, type, body, timeout):
                self.calls.append(
                    {
                        "namespace": namespace,
                        "type": type,
                        "to": to_node,
                        "body": body,
                    }
                )
                if namespace == "terrarium.session" and type == "resume":
                    return per_node_resume[to_node]
                if namespace == "terrarium.files" and type == "stat":
                    return {"stat": {"path": f"C:/worker-config/{body['path']}"}}
                return self._responses.get(f"{namespace}:{type}", {})

        host = _RoutedHost()
        svc = _ClusterSvc(host)
        svc._roster = [
            _ci("cid-alpha", "alpha", "new-a"),
            _ci("cid-bravo", "bravo", "new-b"),
        ]
        client = TestClient(_app(service=svc))
        resp = client.post(
            f"/sessions/{sid_a}/resume",
            json={
                "on_node": "w1",
                "members": [
                    {"sid": sid_a, "on_node": "w1"},
                    {"sid": sid_b, "on_node": "w2"},
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Both workers were targeted.  Each got its own resume RPC.
        resume_targets = sorted(
            c["to"]
            for c in host.calls
            if c["namespace"] == "terrarium.session" and c["type"] == "resume"
        )
        assert resume_targets == ["w1", "w2"]
        # The host relinked the cluster via service.connect between the
        # primary creature and the peer — this is the CF-6 fix:
        # without it, _cluster_links would stay empty after resume and
        # the cluster would silently downgrade to two singletons.
        assert svc.connect_calls == [("cid-alpha", "cid-bravo")]
        # Response surfaces the resumed primary and the cluster_members
        # list (so the frontend knows the cluster is intact).
        assert body["instance_id"] == "new-a"
        assert {m["on_node"] for m in body["cluster_members"]} == {"w1", "w2"}
        assert meta_for(svc)["new-a"]["creature_ids"] == ["cid-alpha"]
        assert meta_for(svc)["new-b"]["creature_ids"] == ["cid-bravo"]

    def test_cluster_resume_uses_persisted_primary_id_after_file_rename(
        self, monkeypatch, tmp_path
    ):
        selected = tmp_path / "renamed.kohakutr"
        peer = tmp_path / "sid-b.kohakutr"
        members = [
            {"sid": "sid-a", "on_node": "w1"},
            {"sid": "sid-b", "on_node": "w2"},
        ]
        for sid, path in (("sid-a", selected), ("sid-b", peer)):
            store = SessionStore(path)
            store.init_meta(sid, "agent", "/cfg", str(tmp_path), [sid])
            store.meta["cluster_members"] = members
            store.close(update_status=False)
        paths = {"renamed": selected, "sid-a": selected, "sid-b": peer}
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: paths.get(name),
        )

        class _RoutedHost(_FakeHost):
            async def request(self, *, to_node, namespace, type, body, timeout):
                self.calls.append(
                    {
                        "namespace": namespace,
                        "type": type,
                        "to": to_node,
                        "body": body,
                    }
                )
                if namespace == "terrarium.files" and type == "stat":
                    return {"stat": {"path": f"C:/worker-config/{body['path']}"}}
                if namespace == "terrarium.session" and type == "resume":
                    suffix = "a" if to_node == "w1" else "b"
                    return {
                        "session_id": f"new-{suffix}",
                        "meta": {"agents": [f"agent-{suffix}"], "config_type": "agent"},
                    }
                return self._responses.get(f"{namespace}:{type}", {})

        host = _RoutedHost()
        service = _ClusterSvc(host)
        service._roster = [
            _ci("cid-a", "agent-a", "new-a"),
            _ci("cid-b", "agent-b", "new-b"),
        ]

        response = TestClient(_app(service=service)).post(
            "/sessions/renamed/resume",
            json={"on_node": "w1"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["instance_id"] == "new-a"

    def test_cluster_resume_rejects_when_member_worker_disconnected(
        self, monkeypatch, tmp_path
    ):
        # Saved mirrors exist but w2 is NOT in connected_nodes.
        sid_a, sid_b = "sid-a", "sid-b"
        pa = tmp_path / f"{sid_a}.kohakutr"
        pa.write_bytes(b"alpha")
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: pa,
        )
        host = _FakeHost()
        svc = _ClusterSvc(host, nodes=("w1",))  # w2 missing on purpose
        client = TestClient(_app(service=svc))
        resp = client.post(
            f"/sessions/{sid_a}/resume",
            json={
                "on_node": "w1",
                "members": [
                    {"sid": sid_a, "on_node": "w1"},
                    {"sid": sid_b, "on_node": "w2"},
                ],
            },
        )
        # Behavior: half-resumed clusters are worse than an honest 404,
        # so the route must reject upfront when any member's worker is
        # absent — no file is pushed, no connect is called.
        assert resp.status_code == 404
        assert svc.connect_calls == []
        pushed = [c for c in host.calls if c["namespace"] == "terrarium.files"]
        assert pushed == []

    def test_cluster_resume_rejects_duplicate_member_ids_before_transfer(
        self, monkeypatch, tmp_path
    ):
        path = tmp_path / "sid-a.kohakutr"
        path.write_bytes(b"saved")
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: path,
        )
        host = _FakeHost()
        svc = _ClusterSvc(host)

        response = TestClient(_app(service=svc)).post(
            "/sessions/sid-a/resume",
            json={
                "on_node": "w1",
                "members": [
                    {"sid": "sid-a", "on_node": "w1"},
                    {"sid": "sid-a", "on_node": "w2"},
                ],
            },
        )

        assert response.status_code == 400
        assert host.calls == []

    def test_cluster_resume_rejects_an_incomplete_saved_members_override(
        self, monkeypatch, tmp_path
    ):
        path = tmp_path / "sid-a.kohakutr"
        path.write_bytes(b"saved")
        monkeypatch.setattr(
            resume_mod, "resolve_session_path_in", lambda name, session_dir: path
        )
        monkeypatch.setattr(
            resume_mod,
            "_read_saved_cluster_members",
            lambda _path: [
                resume_mod.ClusterMember(sid="sid-a", on_node="w1"),
                resume_mod.ClusterMember(sid="sid-b", on_node="w2"),
            ],
        )
        host = _FakeHost()
        service = _ClusterSvc(host)

        response = TestClient(_app(service=service)).post(
            "/sessions/sid-a/resume",
            json={
                "on_node": "w1",
                "members": [{"sid": "sid-a", "on_node": "w1"}],
            },
        )

        assert response.status_code == 400
        assert "every persisted cluster member" in response.json()["detail"]
        assert host.calls == []

    def test_cluster_resume_rolls_back_primary_when_second_member_fails(
        self, monkeypatch, tmp_path
    ):
        sid_a, sid_b = "sid-a", "sid-b"
        paths = {
            sid_a: tmp_path / f"{sid_a}.kohakutr",
            sid_b: tmp_path / f"{sid_b}.kohakutr",
        }
        for path in paths.values():
            path.write_bytes(b"saved")
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: paths.get(name),
        )

        class _FailingHost(_FakeHost):
            async def request(self, *, to_node, namespace, type, body, timeout):
                self.calls.append(
                    {
                        "namespace": namespace,
                        "type": type,
                        "to": to_node,
                        "body": body,
                    }
                )
                if namespace == "terrarium.session" and type == "resume":
                    if to_node == "w2":
                        raise RuntimeError("second resume failed")
                    return {
                        "session_id": "new-a",
                        "meta": {"agents": ["alpha"], "config_type": "agent"},
                    }
                if namespace == "terrarium.session" and type == "rollback_resume":
                    return {"ok": True, "removed": ["cid-alpha"]}
                if namespace == "terrarium.files" and type == "stat":
                    return {"stat": {"path": f"C:/worker-config/{body['path']}"}}
                return self._responses.get(f"{namespace}:{type}", {})

        host = _FailingHost()
        svc = _ClusterSvc(host)
        client = TestClient(_app(service=svc))
        resp = client.post(
            f"/sessions/{sid_a}/resume",
            json={
                "on_node": "w1",
                "members": [
                    {"sid": sid_a, "on_node": "w1"},
                    {"sid": sid_b, "on_node": "w2"},
                ],
            },
        )

        assert resp.status_code == 502
        rollback_calls = [
            call
            for call in host.calls
            if call["namespace"] == "terrarium.session"
            and call["type"] == "rollback_resume"
        ]
        failed_resume_token = next(
            call["body"]["resume_token"]
            for call in host.calls
            if call["namespace"] == "terrarium.session"
            and call["type"] == "resume"
            and call["to"] == "w2"
        )
        assert rollback_calls == [
            {
                "namespace": "terrarium.session",
                "type": "rollback_resume",
                "to": "w2",
                "body": {
                    "session_path": "C:/worker-config/resume/sid-b.kohakutr",
                    "resume_token": failed_resume_token,
                },
            },
            {
                "namespace": "terrarium.session",
                "type": "rollback_resume",
                "to": "w1",
                "body": {"graph_id": "new-a"},
            },
        ]
        assert meta_for(svc) == {}

    def test_cluster_resume_rolls_back_all_members_when_relink_fails(
        self, monkeypatch, tmp_path
    ):
        sid_a, sid_b = "sid-a", "sid-b"
        paths = {
            sid_a: tmp_path / f"{sid_a}.kohakutr",
            sid_b: tmp_path / f"{sid_b}.kohakutr",
        }
        for path in paths.values():
            path.write_bytes(b"saved")
        monkeypatch.setattr(
            resume_mod,
            "resolve_session_path_in",
            lambda name, session_dir: paths.get(name),
        )
        per_node_resume = {
            "w1": {
                "session_id": "new-a",
                "meta": {"agents": ["alpha"], "config_type": "agent"},
            },
            "w2": {
                "session_id": "new-b",
                "meta": {"agents": ["bravo"], "config_type": "agent"},
            },
        }

        class _RoutedHost(_FakeHost):
            async def request(self, *, to_node, namespace, type, body, timeout):
                self.calls.append(
                    {
                        "namespace": namespace,
                        "type": type,
                        "to": to_node,
                        "body": body,
                    }
                )
                if namespace == "terrarium.session" and type == "resume":
                    return per_node_resume[to_node]
                if namespace == "terrarium.session" and type == "rollback_resume":
                    return {"ok": True, "removed": []}
                if namespace == "terrarium.files" and type == "stat":
                    return {"stat": {"path": f"C:/worker-config/{body['path']}"}}
                return self._responses.get(f"{namespace}:{type}", {})

        host = _RoutedHost()
        svc = _ClusterSvc(host)
        svc._roster = [
            _ci("cid-alpha", "alpha", "new-a"),
            _ci("cid-bravo", "bravo", "new-b"),
        ]
        svc._cluster_links = {
            frozenset({("w1", "new-a"), ("w2", "new-b")}),
        }

        disconnect_calls: list[tuple[str, str, str | None]] = []

        async def fail_connect(sender_id, receiver_id, *, channel=None):
            svc._cluster_links.add(frozenset({("w1", "new-a"), ("w2", "new-b")}))
            raise RuntimeError("relink failed")

        async def disconnect(sender_id, receiver_id, *, channel=None):
            disconnect_calls.append((sender_id, receiver_id, channel))
            svc._cluster_links.clear()

        svc.connect = fail_connect
        svc.disconnect = disconnect
        client = TestClient(_app(service=svc))
        resp = client.post(
            f"/sessions/{sid_a}/resume",
            json={
                "on_node": "w1",
                "members": [
                    {"sid": sid_a, "on_node": "w1"},
                    {"sid": sid_b, "on_node": "w2"},
                ],
            },
        )

        assert resp.status_code == 502
        rollback_calls = [
            (call["to"], call["body"]["graph_id"])
            for call in host.calls
            if call["namespace"] == "terrarium.session"
            and call["type"] == "rollback_resume"
        ]
        assert rollback_calls == [("w2", "new-b"), ("w1", "new-a")]
        assert disconnect_calls == [("cid-alpha", "cid-bravo", "default")]
        assert meta_for(svc) == {}
        assert svc._cluster_links == set()
