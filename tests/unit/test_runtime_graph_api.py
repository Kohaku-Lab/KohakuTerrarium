import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.routes import runtime_graph as route_mod
from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.topology import ChannelKind

from tests.unit.terrarium._fakes import make_creature


async def _build_engine() -> Terrarium:
    engine = Terrarium()
    alice = await engine.add_creature(make_creature("alice"))
    bob = await engine.add_creature(make_creature("bob"), graph=alice.graph_id)
    await engine.add_channel(
        alice.graph_id,
        "tasks",
        kind=ChannelKind.QUEUE,
        description="Task queue",
    )
    await engine.connect(alice, bob, channel="tasks")
    assert bob.graph_id == alice.graph_id
    channel = engine._environments[alice.graph_id].shared_channels.get("tasks")
    await channel.send(ChannelMessage(sender="alice", content="hello bob"))
    await engine.wire_output(alice, {"to": "bob", "with_content": True})
    return engine


@pytest.mark.asyncio
async def test_runtime_graph_snapshot_contains_live_topology_and_wiring():
    engine = await _build_engine()
    try:
        graph = engine.list_graphs()[0]
        snapshot = route_mod.build_runtime_graph_snapshot(engine)

        assert snapshot["version"] > 0
        assert len(snapshot["graphs"]) == 1
        graph_data = snapshot["graphs"][0]
        assert graph_data["graph_id"] == graph.graph_id
        assert {c["creature_id"] for c in graph_data["creatures"]} == {"alice", "bob"}
        assert graph_data["channels"] == [
            {
                "name": "tasks",
                "type": "queue",
                "description": "Task queue",
                "qsize": 1,
                "message_count": 1,
                "last_message": {
                    "message_id": graph_data["channels"][0]["last_message"][
                        "message_id"
                    ],
                    "sender": "alice",
                    "content": "hello bob",
                    "content_preview": "hello bob",
                    "timestamp": graph_data["channels"][0]["last_message"]["timestamp"],
                    "metadata": {},
                    "reply_to": None,
                },
            }
        ]
        edge = graph_data["output_edges"][0]
        assert edge["from"] == "alice"
        assert edge["to"] == "bob"
        assert edge["to_creature_id"] == "bob"
        assert edge["edge_id"]
    finally:
        await engine.shutdown()


def test_runtime_graph_route_uses_dependency_engine():
    engine = Terrarium()
    app = FastAPI()
    app.include_router(route_mod.router, prefix="/api/runtime")
    app.dependency_overrides[get_engine] = lambda: engine
    client = TestClient(app)

    response = client.get("/api/runtime/graph")

    assert response.status_code == 200
    assert response.json()["graphs"] == []


def test_runtime_graph_snapshot_uses_session_metadata(monkeypatch):
    engine = Terrarium()
    monkeypatch.setitem(
        lifecycle._meta,
        "graph_meta",
        {
            "kind": "terrarium",
            "name": "demo",
            "config_path": "/tmp/demo.yml",
            "pwd": "/tmp",
            "created_at": "now",
            "has_root": True,
        },
    )
    graph = engine._topology.graphs["graph_meta"] = route_mod.GraphTopology(
        graph_id="graph_meta"
    )

    try:
        data = route_mod.build_runtime_graph_snapshot(engine)["graphs"][0]
        assert graph.graph_id == "graph_meta"
        assert data["kind"] == "terrarium"
        assert data["name"] == "demo"
        assert data["config_path"] == "/tmp/demo.yml"
        assert data["pwd"] == "/tmp"
        assert data["created_at"] == "now"
        assert data["has_root"] is True
    finally:
        lifecycle._meta.pop("graph_meta", None)
