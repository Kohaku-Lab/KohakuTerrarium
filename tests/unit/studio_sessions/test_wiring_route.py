"""HTTP coverage for runtime output-wiring routes."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.routes.sessions_v2 import wiring as route_mod
from kohakuterrarium.core.output_wiring import OutputWiringEntry


class _FakeEngine:
    def __init__(self) -> None:
        self.agent = SimpleNamespace(
            config=SimpleNamespace(output_wiring=[]),
        )

    def get_creature(self, creature_id: str):
        if creature_id != "alice":
            raise KeyError(creature_id)
        return SimpleNamespace(agent=self.agent)

    def list_output_wiring(self, creature_id: str):
        self.get_creature(creature_id)
        return [
            {
                "id": "wire_bob_content_simple_noself_811c9dc5",
                "to": entry.to,
                "with_content": entry.with_content,
                "prompt": entry.prompt,
                "prompt_format": entry.prompt_format,
                "allow_self_trigger": entry.allow_self_trigger,
            }
            for entry in self.agent.config.output_wiring
        ]

    async def wire_output(self, creature_id: str, target: dict) -> str:
        self.get_creature(creature_id)
        self.agent.config.output_wiring.append(OutputWiringEntry(**target))
        return "wire_bob_content_simple_noself_811c9dc5"

    async def unwire_output(self, creature_id: str, edge_id: str) -> bool:
        self.get_creature(creature_id)
        if edge_id != "wire_bob_content_simple_noself_811c9dc5":
            return False
        self.agent.config.output_wiring.clear()
        return True

    async def unwire_output_sink(self, creature_id: str, sink_id: str) -> bool:
        self.get_creature(creature_id)
        return sink_id == "sink-1"


def _make_client(engine: _FakeEngine) -> TestClient:
    app = FastAPI()
    app.include_router(route_mod.router, prefix="/api/sessions/wiring")
    app.dependency_overrides[get_engine] = lambda: engine
    return TestClient(app)


def test_route_wires_lists_and_unwires_output_edge():
    engine = _FakeEngine()
    client = _make_client(engine)

    resp = client.post(
        "/api/sessions/wiring/s1/creatures/alice/outputs",
        json={"to": "bob"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "status": "wired",
        "edge_id": "wire_bob_content_simple_noself_811c9dc5",
    }

    resp = client.get("/api/sessions/wiring/s1/creatures/alice/outputs")
    assert resp.status_code == 200, resp.text
    assert resp.json()["outputs"][0]["to"] == "bob"

    resp = client.delete(
        "/api/sessions/wiring/s1/creatures/alice/outputs/"
        "wire_bob_content_simple_noself_811c9dc5"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "unwired"}
    assert engine.agent.config.output_wiring == []


def test_route_unwires_secondary_output_sink():
    client = _make_client(_FakeEngine())

    resp = client.delete("/api/sessions/wiring/s1/creatures/alice/sinks/sink-1")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "unwired"}


def test_route_unknown_creature_is_404():
    client = _make_client(_FakeEngine())

    resp = client.post(
        "/api/sessions/wiring/s1/creatures/ghost/outputs",
        json={"to": "bob"},
    )
    assert resp.status_code == 404

    resp = client.delete("/api/sessions/wiring/s1/creatures/ghost/sinks/sink-1")
    assert resp.status_code == 404
