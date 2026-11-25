"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2.creatures.modules`."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

import kohakuterrarium as kt
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2.creatures import modules as mods_mod
from kohakuterrarium.core.config.types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.modules.plugin.base import PluginLockedError
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.modules.tool.origin import ToolOrigin
from kohakuterrarium.terrarium.creature.host import Creature
from kohakuterrarium.terrarium.engine.engine import Terrarium
from kohakuterrarium.terrarium.service.service import (
    CreatureInfo,
    LocalTerrariumService,
)
from kohakuterrarium.testing.llm import ScriptedLLM


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
    def __init__(self, **overrides):
        self._creatures = overrides.get("creatures", [_info()])
        self._list = overrides.get("list_returns", [{"name": "permgate"}])
        self._get = overrides.get("get_returns", {"name": "permgate"})
        self._set = overrides.get("set_returns", {"k": 1})
        self._toggle = overrides.get("toggle_returns", {"enabled": True})
        self._raise_on = overrides.get("raise_on", {})

    async def list_creatures(self):
        return tuple(self._creatures)

    async def list_modules(self, cid):
        if "list_modules" in self._raise_on:
            raise self._raise_on["list_modules"]
        return self._list

    async def get_module_options(self, cid, t, n):
        if "get_module_options" in self._raise_on:
            raise self._raise_on["get_module_options"]
        return self._get

    async def set_module_options(self, cid, t, n, v):
        if "set_module_options" in self._raise_on:
            raise self._raise_on["set_module_options"]
        return self._set

    async def toggle_module(self, cid, t, n):
        if "toggle_module" in self._raise_on:
            raise self._raise_on["toggle_module"]
        return self._toggle


def _client(service):
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service
    app.include_router(mods_mod.router)
    return TestClient(app)


class TestListModules:
    def test_success(self):
        client = _client(_FakeService())
        resp = client.get("/sess/creatures/alice/modules")
        assert resp.status_code == 200
        assert resp.json()["modules"] == [{"name": "permgate"}]

    def test_key_error(self):
        svc = _FakeService(raise_on={"list_modules": KeyError("no")})
        client = _client(svc)
        resp = client.get("/sess/creatures/alice/modules")
        assert resp.status_code == 404


class TestGetOptions:
    def test_success(self):
        client = _client(_FakeService(get_returns={"name": "permgate"}))
        resp = client.get("/sess/creatures/alice/modules/plugin/permgate/options")
        assert resp.status_code == 200
        # Route returns the service payload verbatim.
        assert resp.json() == {"name": "permgate"}

    def test_key_error(self):
        svc = _FakeService(raise_on={"get_module_options": KeyError("no")})
        client = _client(svc)
        resp = client.get("/sess/creatures/alice/modules/plugin/p/options")
        assert resp.status_code == 404

    def test_value_error(self):
        svc = _FakeService(raise_on={"get_module_options": ValueError("bad")})
        client = _client(svc)
        resp = client.get("/sess/creatures/alice/modules/plugin/p/options")
        assert resp.status_code == 400


class TestSetOptions:
    def test_success(self):
        client = _client(_FakeService(set_returns={"foo": 1}))
        resp = client.put(
            "/sess/creatures/alice/modules/plugin/permgate/options",
            json={"values": {"foo": 1}},
        )
        assert resp.status_code == 200
        # Route echoes status + type + name + the applied options.
        assert resp.json() == {
            "status": "saved",
            "type": "plugin",
            "name": "permgate",
            "options": {"foo": 1},
        }

    def test_key_error(self):
        svc = _FakeService(raise_on={"set_module_options": KeyError("no")})
        client = _client(svc)
        resp = client.put(
            "/sess/creatures/alice/modules/plugin/p/options",
            json={"values": {}},
        )
        assert resp.status_code == 404

    def test_locked_plugin_returns_409(self):
        svc = _FakeService(
            raise_on={"set_module_options": PluginLockedError("sandbox is locked")}
        )
        resp = _client(svc).put(
            "/sess/creatures/alice/modules/plugin/sandbox/options",
            json={"values": {"backend": "off"}},
        )
        assert resp.status_code == 409

    def test_value_error(self):
        svc = _FakeService(raise_on={"set_module_options": ValueError("bad")})
        client = _client(svc)
        resp = client.put(
            "/sess/creatures/alice/modules/plugin/p/options",
            json={"values": {}},
        )
        assert resp.status_code == 400


class TestToggleModule:
    def test_success(self):
        client = _client(_FakeService(toggle_returns={"enabled": False}))
        resp = client.post("/sess/creatures/alice/modules/plugin/permgate/toggle")
        assert resp.status_code == 200
        # Route returns the service's new-state payload verbatim.
        assert resp.json() == {"enabled": False}

    def test_key_error(self):
        svc = _FakeService(raise_on={"toggle_module": KeyError("no")})
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/modules/plugin/p/toggle")
        assert resp.status_code == 404

    def test_value_error(self):
        svc = _FakeService(raise_on={"toggle_module": ValueError("bad")})
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/modules/plugin/p/toggle")
        assert resp.status_code == 400


class _Probe(BaseTool):
    def __init__(self, name, native=False):
        super().__init__()
        self._name = name
        self.is_provider_native = native

    @property
    def tool_name(self):
        return self._name

    @property
    def description(self):
        return f"{self._name} tool"

    @property
    def execution_mode(self):
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(output="ran")


class TestToolModulesOnALiveCreature:
    async def test_list_and_toggle_a_tool(self, tmp_path):
        cfg = AgentConfig(
            name="alice",
            system_prompt="Test.",
            include_hints_in_prompt=False,
            agent_path=tmp_path,
            input=InputConfig(type="none"),
            output=OutputConfig(type="none"),
        )
        agent = await kt.Agent.build(cfg, llm=ScriptedLLM(["ok"]))
        agent.add_tool(_Probe("lab_query"), origin=ToolOrigin("mcp", "sandbox-lab"))
        agent.add_tool(_Probe("hosted", native=True))
        engine = Terrarium()
        creature = await engine.add_creature(
            Creature(creature_id="alice", name="alice", agent=agent), start=False
        )
        client = _client(LocalTerrariumService(engine))
        base = f"/{creature.graph_id}/creatures/alice/modules"
        try:
            modules = client.get(base).json()["modules"]
            rows = {(m["type"], m["name"]): m for m in modules}
            lab = rows[("tool", "lab_query")]
            assert lab["enabled"] is True
            assert lab["origin"] == {
                "kind": "mcp",
                "name": "sandbox-lab",
                "label": "mcp:sandbox-lab",
            }
            assert rows[("native_tool", "hosted")]["origin"]["label"] == "runtime"

            resp = client.post(f"{base}/tool/lab_query/toggle")
            assert resp.status_code == 200
            assert resp.json() == {
                "name": "lab_query",
                "enabled": False,
                "warning": "",
            }
            assert agent.is_tool_enabled("lab_query") is False
            listed = {m["name"]: m for m in client.get(base).json()["modules"]}
            assert listed["lab_query"]["enabled"] is False

            assert client.post(f"{base}/tool/ghost/toggle").status_code == 404
            assert client.post(f"{base}/tool/hosted/toggle").status_code == 404
            resp = client.post(f"{base}/native_tool/hosted/toggle")
            assert resp.json()["enabled"] is False
            assert agent.disabled_tools == frozenset({"lab_query", "hosted"})
        finally:
            await engine.shutdown()
