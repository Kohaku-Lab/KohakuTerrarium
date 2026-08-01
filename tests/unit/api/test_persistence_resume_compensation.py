"""Compensation boundaries for remote and clustered session resume."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from kohakuterrarium.api.routes.persistence import (
    cluster_resume_compensation as compensation_mod,
)
from kohakuterrarium.api.routes.persistence import resume as resume_mod
from kohakuterrarium.studio.sessions.registry import meta_for


@pytest.fixture(autouse=True)
def _resume_boundaries(monkeypatch):
    async def ready(*_args, **_kwargs):
        return {"ready": True}

    monkeypatch.setattr(resume_mod, "_worker_workspace_preflight", ready)
    monkeypatch.setattr(
        resume_mod,
        "_persist_remote_workspace_meta",
        lambda path, *_args, **_kwargs: SimpleNamespace(path=path),
    )
    monkeypatch.setattr(
        compensation_mod,
        "rollback_remote_workspace_meta",
        lambda _snapshot: None,
    )
    monkeypatch.setattr(resume_mod, "_read_saved_cluster_members", lambda path: None)


def _request():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_mirror=None))
    )


class _Host:
    def __init__(self):
        self.calls = []

    async def request(self, *, to_node, namespace, type, body, timeout):
        self.calls.append(
            {"namespace": namespace, "type": type, "to": to_node, "body": body}
        )
        if namespace == "terrarium.session" and type == "rollback_resume":
            return {"ok": True}
        return {}


class _Service:
    def __init__(self, host, nodes=("w1",)):
        self.host = host
        self._nodes = nodes
        self._home = {}
        self.disconnect_calls = []
        self.connect_calls = []

    def connected_nodes(self):
        return tuple(self._nodes)

    async def disconnect(self, left, right, *, channel=None):
        self.disconnect_calls.append((left, right, channel))

    async def connect(self, left, right, *, channel=None):
        self.connect_calls.append((left, right, channel))


def _creature(creature_id, name, graph_id):
    return SimpleNamespace(
        creature_id=creature_id,
        name=name,
        graph_id=graph_id,
        is_running=True,
        is_privileged=False,
    )


def _worker_result(sid, agent, creature_id):
    return (
        sid,
        {"agents": [agent], "config_type": "agent"},
        None,
        f"C:/worker/{sid}.kohakutr",
        [
            {
                "creature_id": creature_id,
                "name": agent,
                "running": True,
                "is_privileged": False,
            }
        ],
    )


@pytest.mark.asyncio
async def test_compensation_restores_exact_controller_state_in_order(
    monkeypatch, tmp_path
):
    events = []

    class OrderedHost:
        async def request(self, *, to_node, namespace, type, body, timeout):
            assert events == ["disconnect", "mirror"]
            events.append("worker")
            return {"ok": True}

    service = _Service(OrderedHost())
    service._home = {"cid-a": "old-node", "unrelated": "w9"}

    async def disconnect(left, right, *, channel=None):
        events.append("disconnect")

    service.disconnect = disconnect
    registry = meta_for(service)
    registry["new-sid"] = {"name": "before", "nested": {"value": 1}}
    registry["unrelated"] = {"name": "keep"}
    snapshot = compensation_mod.snapshot_controller_state(
        service,
        "new-sid",
        ["cid-a", "cid-b"],
    )
    registry["new-sid"] = {"name": "adopted"}
    service._home["cid-a"] = "new-node"
    service._home["cid-b"] = "new-node"
    mirror = SimpleNamespace(path=tmp_path / "saved.kohakutr")

    def rollback_mirror(actual):
        assert actual is mirror
        assert registry["new-sid"] == {
            "name": "before",
            "nested": {"value": 1},
        }
        assert service._home == {"cid-a": "old-node", "unrelated": "w9"}
        events.append("mirror")

    monkeypatch.setattr(
        compensation_mod,
        "rollback_remote_workspace_meta",
        rollback_mirror,
    )

    errors = await compensation_mod.rollback_cluster_resume(
        service,
        {"saved-sid": ("new-sid", {}, "w1")},
        ["new-sid"],
        [("cid-a", "cid-peer")],
        [mirror],
        [snapshot],
    )

    assert errors == []
    assert events == ["disconnect", "mirror", "worker"]
    assert registry == {
        "new-sid": {"name": "before", "nested": {"value": 1}},
        "unrelated": {"name": "keep"},
    }
    assert service._home == {"cid-a": "old-node", "unrelated": "w9"}


@pytest.mark.asyncio
async def test_single_roster_cancellation_restores_pre_refresh_state(
    monkeypatch, tmp_path
):
    path = tmp_path / "saved.kohakutr"
    path.write_bytes(b"saved")
    host = _Host()
    service = _Service(host)
    service._home = {"cid-alice": "old-node", "unrelated": "w9"}
    registry = meta_for(service)
    registry["new-sid"] = {"name": "before"}
    registry["unrelated"] = {"name": "keep"}

    async def push(**_kwargs):
        return _worker_result("new-sid", "alice", "cid-alice")

    async def list_creatures():
        service._home["cid-alice"] = "refreshed-node"
        raise asyncio.CancelledError

    monkeypatch.setattr(resume_mod, "_push_and_resume_member", push)
    service.list_creatures = list_creatures

    with pytest.raises(asyncio.CancelledError):
        await resume_mod._resume_session(
            "saved",
            _request(),
            resume_mod.ResumeRequest(on_node="w1"),
            path,
            tmp_path,
            lambda: service,
        )

    assert service._home == {"cid-alice": "old-node", "unrelated": "w9"}
    assert registry == {
        "new-sid": {"name": "before"},
        "unrelated": {"name": "keep"},
    }
    assert _rollback_calls(host) == [("w1", {"graph_id": "new-sid"})]


@pytest.mark.asyncio
async def test_single_response_failure_restores_pre_refresh_state(
    monkeypatch, tmp_path
):
    path = tmp_path / "saved.kohakutr"
    path.write_bytes(b"saved")
    host = _Host()
    service = _Service(host)
    service._home = {"cid-alice": "old-node", "unrelated": "w9"}
    registry = meta_for(service)
    registry["new-sid"] = {"name": "before"}
    registry["unrelated"] = {"name": "keep"}

    async def push(**_kwargs):
        return _worker_result("new-sid", "alice", "cid-alice")

    async def list_creatures():
        service._home["cid-alice"] = "refreshed-node"
        return [_creature("cid-alice", "alice", "new-sid")]

    def fail_response(*_args, **_kwargs):
        raise RuntimeError("synthetic response failed")

    monkeypatch.setattr(resume_mod, "_push_and_resume_member", push)
    monkeypatch.setattr(
        resume_mod,
        "_build_single_remote_response",
        fail_response,
    )
    service.list_creatures = list_creatures

    with pytest.raises(HTTPException) as raised:
        await resume_mod._resume_session(
            "saved",
            _request(),
            resume_mod.ResumeRequest(on_node="w1"),
            path,
            tmp_path,
            lambda: service,
        )

    assert raised.value.status_code == 502
    assert service._home == {"cid-alice": "old-node", "unrelated": "w9"}
    assert registry == {
        "new-sid": {"name": "before"},
        "unrelated": {"name": "keep"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancel", "relink"])
async def test_cluster_post_roster_failure_restores_pre_refresh_state(
    monkeypatch, tmp_path, failure
):
    members = [("sid-a", "w1"), ("sid-b", "w2")]
    paths = {}
    for sid, _node in members:
        path = tmp_path / f"{sid}.kohakutr"
        path.write_bytes(sid.encode())
        paths[sid] = path
    monkeypatch.setattr(
        resume_mod,
        "resolve_session_path_in",
        lambda name, session_dir: paths.get(name),
    )
    results = {
        "w1": _worker_result("new-a", "alice", "cid-a"),
        "w2": _worker_result("new-b", "bob", "cid-b"),
    }

    async def push(*, on_node, **_kwargs):
        return results[on_node]

    host = _Host()
    service = _Service(host, nodes=("w1", "w2"))
    service._home = {
        "cid-a": "old-a",
        "cid-b": "old-b",
        "unrelated": "w9",
    }
    registry = meta_for(service)
    registry["new-a"] = {"name": "before-a"}
    registry["new-b"] = {"name": "before-b"}
    registry["unrelated"] = {"name": "keep"}

    async def list_creatures():
        service._home["cid-a"] = "refreshed-a"
        service._home["cid-b"] = "refreshed-b"
        if failure == "cancel":
            raise asyncio.CancelledError
        return (
            _creature("cid-a", "alice", "new-a"),
            _creature("cid-b", "bob", "new-b"),
        )

    async def connect(left, right, *, channel=None):
        service.connect_calls.append((left, right, channel))
        raise RuntimeError("relink failed")

    monkeypatch.setattr(resume_mod, "_push_and_resume_member", push)
    service.list_creatures = list_creatures
    service.connect = connect
    operation = resume_mod._resume_session(
        "sid-a",
        _request(),
        resume_mod.ResumeRequest(
            on_node="w1",
            members=[
                resume_mod.ClusterMember(sid=sid, on_node=node) for sid, node in members
            ],
        ),
        paths["sid-a"],
        tmp_path,
        lambda: service,
    )

    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await operation
    else:
        with pytest.raises(HTTPException) as raised:
            await operation
        assert raised.value.status_code == 502

    assert service._home == {
        "cid-a": "old-a",
        "cid-b": "old-b",
        "unrelated": "w9",
    }
    assert registry == {
        "new-a": {"name": "before-a"},
        "new-b": {"name": "before-b"},
        "unrelated": {"name": "keep"},
    }
    assert _rollback_calls(host) == [
        ("w2", {"graph_id": "new-b"}),
        ("w1", {"graph_id": "new-a"}),
    ]


@pytest.mark.asyncio
async def test_all_cluster_preflights_finish_before_first_adoption(
    monkeypatch, tmp_path
):
    members = [("sid-a", "w1"), ("sid-b", "w2"), ("sid-c", "w3")]
    paths = {}
    for sid, _node in members:
        path = tmp_path / f"{sid}.kohakutr"
        path.write_bytes(sid.encode())
        paths[sid] = path
    monkeypatch.setattr(
        resume_mod,
        "resolve_session_path_in",
        lambda name, session_dir: paths.get(name),
    )
    preflighted = []

    async def preflight(
        host,
        path,
        on_node,
        *,
        replacements=None,
        pwd_override=None,
        require_ready=True,
    ):
        preflighted.append(on_node)
        if on_node == "w3":
            raise HTTPException(
                status_code=409,
                detail={"code": "workspace_replacement_required"},
            )
        return {"ready": True}

    async def forbidden_push(**_kwargs):
        pytest.fail("adoption started before every member passed preflight")

    monkeypatch.setattr(resume_mod, "_worker_workspace_preflight", preflight)
    monkeypatch.setattr(resume_mod, "_push_and_resume_member", forbidden_push)
    service = _Service(_Host(), nodes=("w1", "w2", "w3"))

    with pytest.raises(HTTPException) as raised:
        await resume_mod._resume_session(
            "sid-a",
            _request(),
            resume_mod.ResumeRequest(
                on_node="w1",
                members=[
                    resume_mod.ClusterMember(sid=sid, on_node=node)
                    for sid, node in members
                ],
            ),
            paths["sid-a"],
            tmp_path,
            lambda: service,
        )

    assert raised.value.status_code == 409
    assert preflighted == ["w1", "w2", "w3"]
    assert service.host.calls == []
    assert meta_for(service) == {}


def _rollback_calls(host):
    return [
        (call["to"], call["body"])
        for call in host.calls
        if call["namespace"] == "terrarium.session"
        and call["type"] == "rollback_resume"
    ]
