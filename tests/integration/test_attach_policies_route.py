"""Integration tests for the attach policy hint route.

The route is a thin parse-and-call wrapper around
:mod:`kohakuterrarium.studio.attach.policies`. We mount the router with a
fake :class:`Terrarium` engine that returns hand-built creatures /
graphs, then assert the JSON shape and 404 behaviour.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.routes.attach import policies as policies_route


def _make_engine(*, creatures=None, graphs=None):
    """Build a fake engine with ``get_creature`` / ``get_graph`` lookups."""
    creatures = creatures or {}
    graphs = graphs or {}

    class _FakeEngine:
        _environments: dict = {}

        def get_creature(self, cid: str):
            if cid not in creatures:
                raise KeyError(cid)
            return creatures[cid]

        def get_graph(self, sid: str):
            if sid not in graphs:
                raise KeyError(sid)
            return graphs[sid]

    return _FakeEngine()


def _make_client(engine) -> TestClient:
    app = FastAPI()
    app.include_router(policies_route.router, prefix="/api/attach")
    app.dependency_overrides[get_engine] = lambda: engine
    return TestClient(app)


def _make_creature(*, has_input: bool = False, graph_id: str = "g1"):
    """Fake creature wired to the engine."""
    agent = SimpleNamespace(input_module=MagicMock() if has_input else None)
    return SimpleNamespace(agent=agent, graph_id=graph_id)


# ─── creature policies ──────────────────────────────────────────


def test_creature_policies_baseline_no_input():
    """Creature with no input module returns just LOG + TRACE."""
    engine = _make_engine(creatures={"c1": _make_creature(has_input=False)})
    client = _make_client(engine)
    resp = client.get("/api/attach/policies/c1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["policies"] == ["log", "trace"]


def test_creature_policies_with_input_module():
    """Creature with an input module includes IO."""
    engine = _make_engine(creatures={"c1": _make_creature(has_input=True)})
    client = _make_client(engine)
    resp = client.get("/api/attach/policies/c1")
    assert resp.status_code == 200
    assert "io" in resp.json()["policies"]


def test_creature_unknown_id_returns_404():
    """Unknown creature → 404 — frontend treats this as 'no hint'."""
    engine = _make_engine(creatures={})
    client = _make_client(engine)
    resp = client.get("/api/attach/policies/nonexistent")
    assert resp.status_code == 404


# ─── session policies ──────────────────────────────────────────


def test_session_policies_baseline():
    """Session with no root creature returns LOG + OBSERVER + TRACE."""
    graph = SimpleNamespace(creature_ids=[])
    engine = _make_engine(graphs={"s1": graph})
    client = _make_client(engine)
    resp = client.get("/api/attach/session_policies/s1")
    assert resp.status_code == 200
    assert set(resp.json()["policies"]) == {"log", "observer", "trace"}


def test_session_policies_with_root_includes_io():
    """Session with a root creature flags IO."""
    root = SimpleNamespace(is_root=True)
    graph = SimpleNamespace(creature_ids=["root_creature"])

    class _Engine:
        _environments: dict = {}

        def get_creature(self, cid):
            if cid == "root_creature":
                return root
            raise KeyError(cid)

        def get_graph(self, sid):
            if sid == "s1":
                return graph
            raise KeyError(sid)

    client = _make_client(_Engine())
    resp = client.get("/api/attach/session_policies/s1")
    assert resp.status_code == 200
    assert "io" in resp.json()["policies"]


def test_session_unknown_id_returns_404():
    engine = _make_engine(graphs={})
    client = _make_client(engine)
    resp = client.get("/api/attach/session_policies/nonexistent")
    assert resp.status_code == 404
