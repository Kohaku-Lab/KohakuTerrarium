"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2.wiring`."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2 import wiring as wiring_mod
from kohakuterrarium.terrarium.service import CreatureInfo


def _info(cid="cid-1", name="alice"):
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


class _FakeService:
    def __init__(
        self,
        *,
        creatures=None,
        list_returns=None,
        wire_returns=None,
        unwire_returns=True,
        unwire_sink_returns=True,
        raise_on=None,
    ):
        self._creatures = creatures or [_info()]
        self._list = list_returns or [{"edge_id": "e1"}]
        self._wire = wire_returns or {"edge_id": "e2"}
        self._unwire = unwire_returns
        self._unwire_sink = unwire_sink_returns
        self._raise = raise_on or {}

    async def list_creatures(self):
        return tuple(self._creatures)

    async def get_creature_info(self, cid):
        if "get_creature_info" in self._raise:
            raise self._raise["get_creature_info"]
        if getattr(self, "_info_returns_none", False):
            return None
        for c in self._creatures:
            if c.creature_id == cid:
                return c
        return None

    async def list_output_wiring(self, cid):
        if "list_output_wiring" in self._raise:
            raise self._raise["list_output_wiring"]
        return self._list

    async def wire_output(self, cid, target):
        if "wire_output" in self._raise:
            raise self._raise["wire_output"]
        return self._wire

    async def unwire_output(self, cid, edge_id):
        if "unwire_output" in self._raise:
            raise self._raise["unwire_output"]
        return self._unwire

    async def unwire_output_sink(self, cid, sink_id):
        if "unwire_output_sink" in self._raise:
            raise self._raise["unwire_output_sink"]
        return self._unwire_sink


def _client(service):
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service
    app.include_router(wiring_mod.router, prefix="/wiring")
    return TestClient(app)


# ── list_creature_outputs ──────────────────────────────────────


class TestListOutputs:
    def test_success(self):
        client = _client(_FakeService())
        resp = client.get("/wiring/g1/creatures/alice/outputs")
        assert resp.status_code == 200
        assert resp.json()["outputs"] == [{"edge_id": "e1"}]

    def test_unknown_creature(self):
        client = _client(_FakeService(creatures=[]))
        resp = client.get("/wiring/g1/creatures/ghost/outputs")
        assert resp.status_code == 404

    def test_key_error_inside_service(self):
        svc = _FakeService(raise_on={"list_output_wiring": KeyError("no")})
        client = _client(svc)
        resp = client.get("/wiring/g1/creatures/alice/outputs")
        assert resp.status_code == 404


# ── wire_creature_output ───────────────────────────────────────


class TestWireOutput:
    def test_success(self):
        client = _client(_FakeService())
        resp = client.post(
            "/wiring/g1/creatures/alice/outputs",
            json={"to": "bob"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "wired"
        assert resp.json()["edge_id"] == "e2"

    def test_value_error(self):
        svc = _FakeService(raise_on={"wire_output": ValueError("dup")})
        client = _client(svc)
        resp = client.post(
            "/wiring/g1/creatures/alice/outputs",
            json={"to": "bob"},
        )
        assert resp.status_code == 400

    def test_key_error(self):
        svc = _FakeService(raise_on={"wire_output": KeyError("no")})
        client = _client(svc)
        resp = client.post(
            "/wiring/g1/creatures/alice/outputs",
            json={"to": "bob"},
        )
        assert resp.status_code == 404


# ── unwire_creature_output ─────────────────────────────────────


class TestUnwireOutput:
    def test_success(self):
        client = _client(_FakeService(unwire_returns=True))
        resp = client.delete("/wiring/g1/creatures/alice/outputs/e1")
        assert resp.status_code == 200
        assert resp.json() == {"status": "unwired"}

    def test_not_found_returns_status_not_found(self):
        client = _client(_FakeService(unwire_returns=False))
        resp = client.delete("/wiring/g1/creatures/alice/outputs/missing")
        assert resp.status_code == 200
        assert resp.json() == {"status": "not_found"}

    def test_creature_keyerror(self):
        svc = _FakeService(raise_on={"unwire_output": KeyError("no")})
        client = _client(svc)
        resp = client.delete("/wiring/g1/creatures/alice/outputs/e1")
        assert resp.status_code == 404


# ── list_creature_sinks / unwire_sink ──────────────────────────


class TestSinks:
    def test_list_sinks_success(self):
        client = _client(_FakeService())
        resp = client.get("/wiring/g1/creatures/alice/sinks")
        assert resp.status_code == 200
        assert resp.json() == {"sinks": []}

    def test_list_sinks_missing(self):
        svc = _FakeService(creatures=[])
        client = _client(svc)
        resp = client.get("/wiring/g1/creatures/ghost/sinks")
        assert resp.status_code == 404

    def test_list_sinks_creature_info_none_404s(self):
        # The creature resolves (visible to list_creatures) but
        # get_creature_info returns None — a removal race — and the
        # route surfaces that as a clean 404.
        svc = _FakeService()
        svc._info_returns_none = True
        client = _client(svc)
        resp = client.get("/wiring/g1/creatures/alice/sinks")
        assert resp.status_code == 404

    def test_list_sinks_creature_info_keyerror_404s(self):
        svc = _FakeService(raise_on={"get_creature_info": KeyError("gone")})
        client = _client(svc)
        resp = client.get("/wiring/g1/creatures/alice/sinks")
        assert resp.status_code == 404

    def test_unwire_sink_success(self):
        client = _client(_FakeService(unwire_sink_returns=True))
        resp = client.delete("/wiring/g1/creatures/alice/sinks/s1")
        assert resp.status_code == 200
        assert resp.json() == {"status": "unwired"}

    def test_unwire_sink_not_found(self):
        client = _client(_FakeService(unwire_sink_returns=False))
        resp = client.delete("/wiring/g1/creatures/alice/sinks/ghost")
        assert resp.status_code == 200
        assert resp.json() == {"status": "not_found"}

    def test_unwire_sink_keyerror(self):
        svc = _FakeService(raise_on={"unwire_output_sink": KeyError("no")})
        client = _client(svc)
        resp = client.delete("/wiring/g1/creatures/alice/sinks/s1")
        assert resp.status_code == 404


# ── payload helpers ────────────────────────────────────────────


class TestPayloadShape:
    def test_as_entry(self):
        p = wiring_mod.OutputWirePayload(to="bob", with_content=False, prompt="hi")
        entry = p.as_entry()
        assert entry["to"] == "bob"
        assert entry["with_content"] is False
        assert entry["prompt"] == "hi"
        assert entry["prompt_format"] == "simple"
