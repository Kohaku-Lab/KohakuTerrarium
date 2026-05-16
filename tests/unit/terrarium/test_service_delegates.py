"""Mock-based unit tests for the per-creature delegate methods on
:class:`LocalTerrariumService`.

The existing ``test_service.py`` uses a real engine with a fake agent;
that fake doesn't implement the agent-level surfaces (scratchpad,
regenerate, edit_and_rerun, executor.get_running_jobs, etc.) that
service delegates exercise.  This file targets those delegates with a
mock-style agent so each branch flips.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.terrarium import service as svc_mod
from kohakuterrarium.terrarium.service import LocalTerrariumService


class _NamedPlugin(BasePlugin):
    """Minimal real ``BasePlugin`` — just a name — for registering into
    a real ``PluginManager`` so toggle delegates hit the genuine
    enable/disable surface instead of an invented ``set_plugin_enabled``.
    """

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name


# ── helpers ───────────────────────────────────────────────────


class _MockAgent:
    def __init__(self):
        self.scratchpad = SimpleNamespace(to_dict=lambda: {"k": "v"})
        self.config = SimpleNamespace(model="m", name="c")
        self.trigger_manager = SimpleNamespace(list=lambda: [])
        self.registry = SimpleNamespace(list_tools=lambda: [], get_tool=lambda n: None)
        self.native_tool_options = SimpleNamespace(
            get=lambda n: {},
            list=lambda: {},
            set=lambda n, v: v,
        )
        self.plugins = None
        self.plugin_manager = None
        self.workspace = SimpleNamespace(get=lambda: "/wd", set=lambda p: p)
        self.executor = SimpleNamespace(
            get_running_jobs=lambda: [SimpleNamespace(to_dict=lambda: {"id": "j1"})],
            cancel=AsyncMock(return_value=True),
            _working_dir="/wd",
        )
        self.subagent_manager = SimpleNamespace(
            get_running_jobs=lambda: [],
            cancel=AsyncMock(return_value=False),
        )
        self.regenerate_last_response = AsyncMock()
        self.edit_and_rerun = AsyncMock(return_value=True)
        self.rewind_to = AsyncMock()
        self.interrupt = MagicMock()
        self._interrupt_direct_job = MagicMock(return_value=False)
        self._promote_handle = MagicMock(return_value=True)
        self.session = None
        self.session_store = None
        self.conversation_history = []
        self._processing_task = None
        self._direct_job_meta = {}
        self.input_module = None

    def get_system_prompt(self):
        return "you are X"

    def switch_model(self, model):
        self.config.model = model


class _MockCreature:
    def __init__(self):
        self.creature_id = "cid"
        self.name = "alice"
        self.graph_id = "g1"
        self.agent = _MockAgent()
        self.is_running = True
        self.is_privileged = False
        self.parent_creature_id = None
        self.listen_channels = []
        self.send_channels = []


def _build_service():
    engine = MagicMock()
    engine.list_output_wiring = MagicMock(return_value=[])
    engine.wire_output = AsyncMock(return_value="edge-1")
    engine.unwire_output = AsyncMock(return_value=True)
    engine.unwire_output_sink = AsyncMock(return_value=True)
    engine.add_creature = AsyncMock()
    engine.remove_creature = AsyncMock()
    engine.start = AsyncMock()
    engine.stop = AsyncMock()
    engine.shutdown = AsyncMock()
    engine.add_channel = AsyncMock()
    engine.remove_channel = AsyncMock()
    engine.connect = AsyncMock()
    engine.disconnect = AsyncMock()
    engine.status = MagicMock(return_value={})
    engine.list_creatures = MagicMock(return_value=[])
    engine.list_graphs = MagicMock(return_value=[])
    engine._environments = {}
    engine._creatures = {}
    engine._session_stores = {}
    creature = _MockCreature()
    creature.inject_input = AsyncMock()

    def _chat_iter():
        async def gen():
            yield "x"

        return gen()

    creature.chat = MagicMock(return_value=_chat_iter())
    engine.get_creature = MagicMock(return_value=creature)
    return LocalTerrariumService(engine), creature


# ── lifecycle (lines 555-565, 567-577) ────────────────────────


class TestLifecycle:
    async def test_add_creature_local_path(self):
        svc, c = _build_service()
        svc._engine.add_creature.return_value = c
        out = await svc.add_creature("/some/config")
        assert out.creature_id == "cid"

    async def test_add_creature_with_kwargs(self):
        svc, c = _build_service()
        svc._engine.add_creature.return_value = c
        out = await svc.add_creature(
            "/some/config",
            graph_id="g1",
            creature_id="cid-x",
            is_privileged=True,
            parent_creature_id="p",
            start=False,
            pwd="/wd",
            llm_override="m",
        )
        assert out.creature_id == "cid"

    async def test_remove_creature(self):
        svc, _ = _build_service()
        await svc.remove_creature("cid")
        svc._engine.remove_creature.assert_awaited_once_with("cid")

    async def test_start_stop_creature(self):
        svc, _ = _build_service()
        await svc.start_creature("cid")
        await svc.stop_creature("cid")
        svc._engine.start.assert_awaited_with("cid")
        svc._engine.stop.assert_awaited_with("cid")

    async def test_shutdown(self):
        svc, _ = _build_service()
        await svc.shutdown()
        svc._engine.shutdown.assert_awaited_once()


# ── channel ops ──────────────────────────────────────────────


class TestChannels:
    async def test_add_channel(self):
        svc, _ = _build_service()
        await svc.add_channel("g1", "chat", "desc")
        svc._engine.add_channel.assert_awaited_with("g1", "chat", "desc")

    async def test_remove_channel(self):
        svc, _ = _build_service()
        await svc.remove_channel("g1", "chat")
        svc._engine.remove_channel.assert_awaited_with("g1", "chat")

    async def test_connect(self):
        svc, _ = _build_service()
        await svc.connect("a", "b", channel="x")
        svc._engine.connect.assert_awaited_with("a", "b", channel="x")

    async def test_disconnect(self):
        svc, _ = _build_service()
        await svc.disconnect("a", "b")
        svc._engine.disconnect.assert_awaited_with("a", "b", channel=None)


# ── interaction ───────────────────────────────────────────────


class TestInteraction:
    async def test_inject_input(self):
        svc, c = _build_service()
        await svc.inject_input("cid", "hello", source="api")
        c.inject_input.assert_awaited_with("hello", source="api")

    def test_chat_delegates(self):
        svc, c = _build_service()
        stream = svc.chat("cid", "hi")
        assert stream is c.chat.return_value


# ── per-creature control (jobs / interrupt) ───────────────────


class TestJobs:
    async def test_interrupt(self):
        svc, c = _build_service()
        await svc.interrupt("cid")
        c.agent.interrupt.assert_called_once()

    async def test_list_jobs(self):
        svc, _ = _build_service()
        out = await svc.list_jobs("cid")
        assert out == [{"id": "j1"}]

    async def test_stop_job_via_direct(self):
        svc, c = _build_service()
        c.agent._interrupt_direct_job.return_value = True
        out = await svc.stop_job("cid", "j1")
        assert out is True

    async def test_stop_job_via_executor(self):
        svc, c = _build_service()
        c.agent._interrupt_direct_job.return_value = False
        c.agent.executor.cancel = AsyncMock(return_value=True)
        out = await svc.stop_job("cid", "j1")
        assert out is True

    async def test_stop_job_via_subagent(self):
        svc, c = _build_service()
        c.agent._interrupt_direct_job.return_value = False
        c.agent.executor.cancel = AsyncMock(return_value=False)
        c.agent.subagent_manager.cancel = AsyncMock(return_value=True)
        out = await svc.stop_job("cid", "j1")
        assert out is True

    async def test_promote_job(self):
        svc, c = _build_service()
        out = await svc.promote_job("cid", "j1")
        assert out is True


# ── per-creature reads/writes via creature_ops ────────────────


class TestPerCreatureOps:
    async def test_chat_history(self):
        svc, _ = _build_service()
        out = await svc.chat_history("cid")
        assert out["creature_id"] == "cid"

    async def test_chat_branches(self):
        svc, _ = _build_service()
        out = await svc.chat_branches("cid")
        assert isinstance(out, list)

    async def test_regenerate(self):
        svc, c = _build_service()
        out = await svc.regenerate("cid", turn_index=1)
        assert out["status"] == "regenerating"
        c.agent.regenerate_last_response.assert_awaited()

    async def test_edit_message(self):
        svc, c = _build_service()
        out = await svc.edit_message("cid", 2, "new content")
        assert out is True
        c.agent.edit_and_rerun.assert_awaited()

    async def test_rewind(self):
        svc, c = _build_service()
        await svc.rewind("cid", 3)
        c.agent.rewind_to.assert_awaited_with(3)

    async def test_get_scratchpad(self):
        svc, _ = _build_service()
        out = await svc.get_scratchpad("cid")
        assert out == {"k": "v"}

    async def test_patch_scratchpad(self):
        svc, c = _build_service()
        data = {}

        def setter(k, v):
            data[k] = v

        c.agent.scratchpad = SimpleNamespace(
            to_dict=lambda: dict(data),
            set=setter,
            delete=lambda k: data.pop(k, None),
        )
        out = await svc.patch_scratchpad("cid", {"k": "v"})
        assert out == {"k": "v"}

    async def test_list_triggers(self):
        svc, _ = _build_service()
        out = await svc.list_triggers("cid")
        assert out == []

    async def test_get_env(self):
        svc, _ = _build_service()
        out = await svc.get_env("cid")
        assert "pwd" in out

    async def test_get_system_prompt(self):
        svc, _ = _build_service()
        out = await svc.get_system_prompt("cid")
        assert out["text"] == "you are X"

    async def test_get_set_working_dir(self):
        svc, _ = _build_service()
        out = await svc.get_working_dir("cid")
        assert out == "/wd"
        out2 = await svc.set_working_dir("cid", "/new")
        assert out2 == "/new"

    async def test_native_tool_inventory(self):
        svc, _ = _build_service()
        out = await svc.native_tool_inventory("cid")
        assert out == []

    async def test_get_set_native_tool_options(self):
        svc, _ = _build_service()
        out = await svc.get_native_tool_options("cid")
        assert out == {}
        out2 = await svc.set_native_tool_options("cid", "tool", {"k": "v"})
        assert out2 == {"k": "v"}

    async def test_switch_model_via_helper(self):
        svc, c = _build_service()
        out = await svc.switch_model("cid", "new-model")
        assert out == "new-model"
        assert c.agent.config.model == "new-model"

    async def test_switch_model_fallback(self):
        svc, c = _build_service()
        # Strip the helper class-method by replacing the agent with one
        # that lacks switch_model, so the hasattr branch flips.
        new_agent = SimpleNamespace(config=SimpleNamespace(model="m"))
        c.agent = new_agent
        out = await svc.switch_model("cid", "fallback-model")
        assert out == "fallback-model"
        assert new_agent.config.model == "fallback-model"

    async def test_list_plugins_empty(self):
        svc, _ = _build_service()
        out = await svc.list_plugins("cid")
        assert out == []

    async def test_toggle_plugin_no_plugin_manager_raises(self):
        """Regression guard for B-e2e-2: a creature with no plugin
        manager must raise — NOT fabricate a ``{"enabled": True}``
        success. The old delegate reached for a non-existent
        ``agent.set_plugin_enabled`` and returned the requested state
        unconditionally, so this case silently 'succeeded'."""
        svc, _ = _build_service()  # _MockAgent.plugins is None
        with pytest.raises(ValueError):
            await svc.toggle_plugin("cid", "p1", True)

    async def test_toggle_plugin_disables_a_real_plugin(self):
        """Regression guard for B-e2e-2: toggling ``enabled=False``
        actually disables the plugin on the creature's real
        ``PluginManager`` — observable via ``is_enabled``."""
        svc, c = _build_service()
        pm = PluginManager()
        pm.register(_NamedPlugin("p1"))
        c.agent.plugins = pm
        out = await svc.toggle_plugin("cid", "p1", False)
        assert out == {"plugin": "p1", "enabled": False}
        assert pm.is_enabled("p1") is False
        # And re-enabling flips it back on the same real manager.
        out = await svc.toggle_plugin("cid", "p1", True)
        assert out["enabled"] is True
        assert pm.is_enabled("p1") is True

    async def test_toggle_plugin_unknown_raises(self):
        """Regression guard for B-e2e-2: toggling a plugin name the
        creature doesn't have raises ``KeyError`` — never a fabricated
        success that the HTTP/lab surface would 200."""
        svc, c = _build_service()
        pm = PluginManager()
        pm.register(_NamedPlugin("p1"))
        c.agent.plugins = pm
        with pytest.raises(KeyError):
            await svc.toggle_plugin("cid", "ghost", True)


# ── modules / command exec ────────────────────────────────────


class TestModulesAndCommands:
    async def test_list_modules(self):
        svc, _ = _build_service()
        out = await svc.list_modules("cid")
        assert isinstance(out, list)

    async def test_get_module_options_unknown_type(self):
        svc, _ = _build_service()
        with pytest.raises(ValueError):
            await svc.get_module_options("cid", "bad", "x")

    async def test_set_module_options_unknown_type(self):
        svc, _ = _build_service()
        with pytest.raises(ValueError):
            await svc.set_module_options("cid", "bad", "x", {})

    async def test_toggle_module_unknown_type(self):
        svc, _ = _build_service()
        with pytest.raises(ValueError):
            await svc.toggle_module("cid", "bad", "x")

    async def test_execute_command_unknown(self):
        svc, _ = _build_service()
        with pytest.raises(ValueError):
            await svc.execute_command("cid", "no-such")


# ── wiring + output edges ─────────────────────────────────────


class TestWiring:
    async def test_list_output_wiring(self):
        svc, _ = _build_service()
        out = await svc.list_output_wiring("cid")
        assert out == []

    async def test_list_output_wiring_swallows_exception(self):
        svc, _ = _build_service()
        svc._engine.list_output_wiring.side_effect = RuntimeError
        out = await svc.list_output_wiring("cid")
        assert out == []

    async def test_wire_output(self):
        svc, _ = _build_service()
        out = await svc.wire_output("cid", "to-name")
        assert out["edge_id"] == "edge-1"

    async def test_unwire_output(self):
        svc, _ = _build_service()
        out = await svc.unwire_output("cid", "edge-1")
        assert out is True

    async def test_unwire_output_sink(self):
        svc, _ = _build_service()
        out = await svc.unwire_output_sink("cid", "sink")
        assert out is True


# ── attach policies + runtime graph snapshot ──────────────────


class TestPoliciesAndSnapshot:
    async def test_attach_policies(self):
        svc, _ = _build_service()
        out = await svc.attach_policies("cid")
        assert "log" in out

    async def test_session_attach_policies(self):
        svc, _ = _build_service()
        # No graph in topology → returns baseline.
        svc._engine.get_graph = MagicMock(side_effect=KeyError("g"))
        out = await svc.session_attach_policies("g")
        assert "log" in out

    async def test_runtime_graph_snapshot(self):
        svc, _ = _build_service()
        out = await svc.runtime_graph_snapshot()
        assert "graphs" in out

    async def test_runtime_graph_snapshot_with_meta_lookup(self):
        svc, _ = _build_service()
        svc.set_runtime_graph_meta_lookup(lambda gid: {"name": "x"})
        out = await svc.runtime_graph_snapshot()
        assert "graphs" in out


# ── _normalize_command_args ──────────────────────────────────


class TestNormalizeCommandArgs:
    def test_none(self):
        assert svc_mod._normalize_command_args(None) == ""

    def test_str_passthrough(self):
        assert svc_mod._normalize_command_args("hi") == "hi"

    def test_dict_with_args_key(self):
        assert svc_mod._normalize_command_args({"args": "v"}) == "v"

    def test_dict_coerces_value_to_str(self):
        assert svc_mod._normalize_command_args({"args": 42}) == "42"

    def test_dict_key_equals_value(self):
        out = svc_mod._normalize_command_args({"k": "v", "n": "1"})
        assert "k=v" in out and "n=1" in out

    def test_other_coerces(self):
        assert svc_mod._normalize_command_args(123) == "123"


# ── add_creature on-node mismatch (line 549-554) ─────────────


class TestAddCreatureNodeMismatch:
    async def test_rejects_wrong_node(self):
        svc, _ = _build_service()
        with pytest.raises(ValueError, match="mismatches"):
            await svc.add_creature("config", on_node="worker-1")
