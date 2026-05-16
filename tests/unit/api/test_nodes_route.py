"""Unit tests for :mod:`kohakuterrarium.api.routes.nodes`."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes import nodes as nodes_mod
from kohakuterrarium.studio.deploy import DeployError
from kohakuterrarium.terrarium.service import CreatureInfo


def _info(cid="cid", name="alice"):
    return CreatureInfo(
        creature_id=cid,
        name=name,
        graph_id="g",
        is_running=True,
        is_privileged=False,
        parent_creature_id=None,
        listen_channels=(),
        send_channels=(),
    )


class _StandaloneService:
    """No ``connected_nodes`` attribute → lab routes 404."""

    pass


class _PerNode:
    def __init__(self, creatures=None, raise_on=None):
        self._creatures = creatures or []
        self._raise_on = raise_on or {}

    async def list_creatures(self):
        if "list_creatures" in self._raise_on:
            raise self._raise_on["list_creatures"]
        return tuple(self._creatures)

    async def status_snapshot(self):
        return {"snap": True}


class _MultiNode:
    def __init__(self, nodes=None):
        self._nodes: dict[str, _PerNode] = nodes or {
            "_host": _PerNode([_info()]),
            "w1": _PerNode([_info("c-w1")]),
        }
        self.host = object()

    def connected_nodes(self):
        return tuple(self._nodes.keys())

    def service_for(self, node_id):
        return self._nodes[node_id]


def _client(service):
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service
    app.include_router(nodes_mod.router, prefix="/nodes")
    return TestClient(app)


# ── list_nodes ──────────────────────────────────────────────────


class TestListNodes:
    def test_standalone_returns_404(self):
        client = _client(_StandaloneService())
        resp = client.get("/nodes")
        assert resp.status_code == 404

    def test_multi_node_returns_list(self):
        client = _client(_MultiNode())
        resp = client.get("/nodes")
        body = resp.json()
        ids = {n["node_id"] for n in body["nodes"]}
        assert ids == {"_host", "w1"}

    def test_unreachable_node(self):
        nodes = {
            "_host": _PerNode([_info()]),
            "w1": _PerNode(raise_on={"list_creatures": RuntimeError("dead")}),
        }
        client = _client(_MultiNode(nodes))
        resp = client.get("/nodes")
        body = resp.json()
        w1 = next(n for n in body["nodes"] if n["node_id"] == "w1")
        assert w1["status"] == "unreachable"


# ── node_status ─────────────────────────────────────────────────


class TestNodeStatus:
    def test_unknown_returns_404(self):
        client = _client(_MultiNode())
        resp = client.get("/nodes/ghost/status")
        assert resp.status_code == 404

    def test_success(self):
        client = _client(_MultiNode())
        resp = client.get("/nodes/_host/status")
        body = resp.json()
        assert body["ok"] is True
        assert body["creatures"] == 1

    def test_unreachable_returns_503(self):
        nodes = {
            "_host": _PerNode([_info()]),
            "w1": _PerNode(raise_on={"list_creatures": RuntimeError("dead")}),
        }
        client = _client(_MultiNode(nodes))
        resp = client.get("/nodes/w1/status")
        assert resp.status_code == 503


# ── deploy_creature ─────────────────────────────────────────────


class TestDeployCreature:
    def test_host_rejected(self):
        client = _client(_MultiNode())
        resp = client.post(
            "/nodes/_host/deploy/creature",
            json={"workspace_path": "/x"},
        )
        assert resp.status_code == 400

    def test_unknown_node(self):
        client = _client(_MultiNode())
        resp = client.post(
            "/nodes/ghost/deploy/creature",
            json={"workspace_path": "/x"},
        )
        assert resp.status_code == 404

    def test_success(self, monkeypatch):
        async def fake_deploy(host, node, workspace_path):
            return "/remote/recipes/x"

        monkeypatch.setattr(nodes_mod, "deploy_creature_to_node", fake_deploy)
        client = _client(_MultiNode())
        resp = client.post(
            "/nodes/w1/deploy/creature",
            json={"workspace_path": "/local/x"},
        )
        assert resp.status_code == 200
        assert resp.json()["target_path"] == "/remote/recipes/x"

    def test_deploy_error_returns_409(self, monkeypatch):
        async def boom(host, node, workspace_path):
            raise DeployError("conflict")

        monkeypatch.setattr(nodes_mod, "deploy_creature_to_node", boom)
        client = _client(_MultiNode())
        resp = client.post(
            "/nodes/w1/deploy/creature",
            json={"workspace_path": "/x"},
        )
        assert resp.status_code == 409

    def test_file_not_found_returns_404(self, monkeypatch):
        async def boom(host, node, workspace_path):
            raise FileNotFoundError("nope")

        monkeypatch.setattr(nodes_mod, "deploy_creature_to_node", boom)
        client = _client(_MultiNode())
        resp = client.post(
            "/nodes/w1/deploy/creature",
            json={"workspace_path": "/x"},
        )
        assert resp.status_code == 404
