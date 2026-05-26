"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2.creatures_state`."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2 import creatures_state as state_mod
from kohakuterrarium.terrarium.service import CreatureInfo


def _info(cid="cid", name="alice"):
    return CreatureInfo(
        creature_id=cid,
        name=name,
        graph_id="sess",
        is_running=True,
        is_privileged=False,
        parent_creature_id=None,
        listen_channels=(),
        send_channels=(),
    )


class _FakeService:
    def __init__(self, *, creatures=None, raise_on=None):
        self._creatures = creatures or [_info()]
        self._raise_on = raise_on or {}

    async def list_creatures(self):
        return tuple(self._creatures)

    def _maybe_raise(self, key):
        if key in self._raise_on:
            raise self._raise_on[key]

    async def get_scratchpad(self, cid):
        self._maybe_raise("get_scratchpad")
        return {"k": "v"}

    async def patch_scratchpad(self, cid, updates):
        self._maybe_raise("patch_scratchpad")
        return updates

    async def list_triggers(self, cid):
        self._maybe_raise("list_triggers")
        return [{"id": "t1"}]

    async def get_env(self, cid):
        self._maybe_raise("get_env")
        return {"X": "1"}

    async def get_system_prompt(self, cid):
        self._maybe_raise("get_system_prompt")
        return {"text": "be helpful"}

    async def get_working_dir(self, cid):
        self._maybe_raise("get_working_dir")
        return "/cwd"

    async def set_working_dir(self, cid, p):
        self._maybe_raise("set_working_dir")
        return p

    async def native_tool_inventory(self, cid):
        self._maybe_raise("native_tool_inventory")
        return [{"name": "image_gen"}]

    async def set_native_tool_options(self, cid, tool, values):
        self._maybe_raise("set_native_tool_options")
        return values


def _client(service):
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service
    app.include_router(state_mod.router)
    return TestClient(app)


# ── reads ───────────────────────────────────────────────────────


class TestReads:
    def test_get_scratchpad(self):
        client = _client(_FakeService())
        resp = client.get("/sess/creatures/alice/scratchpad")
        assert resp.status_code == 200
        assert resp.json() == {"k": "v"}

    def test_get_scratchpad_missing(self):
        client = _client(_FakeService(creatures=[]))
        resp = client.get("/sess/creatures/ghost/scratchpad")
        assert resp.status_code == 404

    def test_list_triggers(self):
        client = _client(_FakeService())
        resp = client.get("/sess/creatures/alice/triggers")
        assert resp.status_code == 200
        # Route returns the service's trigger list verbatim.
        assert resp.json() == [{"id": "t1"}]

    def test_get_env(self):
        client = _client(_FakeService())
        resp = client.get("/sess/creatures/alice/env")
        assert resp.status_code == 200
        assert resp.json() == {"X": "1"}

    def test_get_system_prompt(self):
        client = _client(_FakeService())
        resp = client.get("/sess/creatures/alice/system-prompt")
        assert resp.status_code == 200
        # Route returns the service's system-prompt payload verbatim.
        assert resp.json() == {"text": "be helpful"}

    def test_get_working_dir(self):
        client = _client(_FakeService())
        resp = client.get("/sess/creatures/alice/working-dir")
        assert resp.status_code == 200
        assert resp.json() == {"pwd": "/cwd"}

    def test_get_native_tool_options(self):
        client = _client(_FakeService())
        resp = client.get("/sess/creatures/alice/native-tool-options")
        assert resp.status_code == 200
        # Route wraps the service inventory under "tools".
        assert resp.json() == {"tools": [{"name": "image_gen"}]}


# ── writes ──────────────────────────────────────────────────────


class TestWrites:
    def test_patch_scratchpad(self):
        client = _client(_FakeService())
        resp = client.patch(
            "/sess/creatures/alice/scratchpad",
            json={"updates": {"k": "v2"}},
        )
        assert resp.status_code == 200
        # Route returns the applied updates verbatim — the fake echoes
        # them, so the mutation payload round-trips.
        assert resp.json() == {"k": "v2"}

    def test_patch_scratchpad_value_error(self):
        svc = _FakeService(raise_on={"patch_scratchpad": ValueError("bad")})
        client = _client(svc)
        resp = client.patch(
            "/sess/creatures/alice/scratchpad",
            json={"updates": {"k": "v2"}},
        )
        assert resp.status_code == 400

    def test_set_working_dir(self):
        client = _client(_FakeService())
        resp = client.put(
            "/sess/creatures/alice/working-dir",
            json={"path": "/new"},
        )
        assert resp.status_code == 200
        assert resp.json()["pwd"] == "/new"

    def test_set_working_dir_value_error(self):
        svc = _FakeService(raise_on={"set_working_dir": ValueError("bad path")})
        client = _client(svc)
        resp = client.put(
            "/sess/creatures/alice/working-dir",
            json={"path": "/x"},
        )
        assert resp.status_code == 400

    def test_set_working_dir_runtime_error(self):
        svc = _FakeService(raise_on={"set_working_dir": RuntimeError("locked")})
        client = _client(svc)
        resp = client.put(
            "/sess/creatures/alice/working-dir",
            json={"path": "/x"},
        )
        assert resp.status_code == 409

    def test_set_native_tool_options(self):
        client = _client(_FakeService())
        resp = client.put(
            "/sess/creatures/alice/native-tool-options",
            json={"tool": "image_gen", "values": {"size": "256"}},
        )
        assert resp.status_code == 200
        # Route echoes status + tool name + applied values (the fake
        # returns the values it was handed).
        assert resp.json() == {
            "status": "saved",
            "tool": "image_gen",
            "values": {"size": "256"},
        }

    def test_set_native_tool_options_value_error(self):
        svc = _FakeService(raise_on={"set_native_tool_options": ValueError("bad")})
        client = _client(svc)
        resp = client.put(
            "/sess/creatures/alice/native-tool-options",
            json={"tool": "image_gen", "values": {}},
        )
        assert resp.status_code == 400


# ── KeyError after a successful name resolution → clean 404 ─────────
# Every handler resolves the creature first; if the engine-level op
# then raises KeyError (a removal race between resolution and the op)
# the route must surface a 404, not a 500.


class TestVanishedCreature404s:
    def test_get_scratchpad(self):
        svc = _FakeService(raise_on={"get_scratchpad": KeyError("cid")})
        assert _client(svc).get("/s/creatures/alice/scratchpad").status_code == 404

    def test_patch_scratchpad(self):
        svc = _FakeService(raise_on={"patch_scratchpad": KeyError("cid")})
        resp = _client(svc).patch(
            "/s/creatures/alice/scratchpad", json={"updates": {"k": "v"}}
        )
        assert resp.status_code == 404

    def test_list_triggers(self):
        svc = _FakeService(raise_on={"list_triggers": KeyError("cid")})
        assert _client(svc).get("/s/creatures/alice/triggers").status_code == 404

    def test_get_env(self):
        svc = _FakeService(raise_on={"get_env": KeyError("cid")})
        assert _client(svc).get("/s/creatures/alice/env").status_code == 404

    def test_get_system_prompt(self):
        svc = _FakeService(raise_on={"get_system_prompt": KeyError("cid")})
        assert _client(svc).get("/s/creatures/alice/system-prompt").status_code == 404

    def test_get_working_dir(self):
        svc = _FakeService(raise_on={"get_working_dir": KeyError("cid")})
        assert _client(svc).get("/s/creatures/alice/working-dir").status_code == 404

    def test_set_working_dir(self):
        svc = _FakeService(raise_on={"set_working_dir": KeyError("cid")})
        resp = _client(svc).put("/s/creatures/alice/working-dir", json={"path": "/x"})
        assert resp.status_code == 404

    def test_get_native_tool_options(self):
        svc = _FakeService(raise_on={"native_tool_inventory": KeyError("cid")})
        resp = _client(svc).get("/s/creatures/alice/native-tool-options")
        assert resp.status_code == 404

    def test_set_native_tool_options(self):
        svc = _FakeService(raise_on={"set_native_tool_options": KeyError("cid")})
        resp = _client(svc).put(
            "/s/creatures/alice/native-tool-options",
            json={"tool": "image_gen", "values": {}},
        )
        assert resp.status_code == 404
