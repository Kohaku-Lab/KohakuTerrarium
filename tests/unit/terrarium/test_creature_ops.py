"""Unit tests for :mod:`kohakuterrarium.terrarium.creature_ops`.

Pure-function helpers — exercised against minimal stand-ins for
``Agent`` and ``Terrarium``.
"""

import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from kohakuterrarium.terrarium import creature_ops as co

# ── _redact_env / agent_env ───────────────────────────────────


class TestEnvHelpers:
    def test_redact_env_strips_secrets(self, monkeypatch):
        monkeypatch.setenv("MY_API_KEY", "x")
        monkeypatch.setenv("KEEP_THIS", "y")
        out = co._redact_env()
        assert "KEEP_THIS" in out
        assert "MY_API_KEY" not in out

    def test_agent_env_uses_working_dir(self):
        # The working directory is owned by the agent's *executor*
        # (matching the real Agent — see core/executor.py:86), NOT a
        # bare ``agent._working_dir`` attribute. agent_env must report
        # that executor dir as ``pwd``.
        ag = SimpleNamespace(executor=SimpleNamespace(_working_dir="/some/dir"))
        out = co.agent_env(ag)
        assert out["pwd"] == "/some/dir"
        assert "env" in out

    def test_agent_env_falls_back_to_cwd(self):
        # No workspace + no executor working dir → agent_working_dir
        # yields "" and agent_env falls back to the process cwd.
        ag = SimpleNamespace(executor=None)
        out = co.agent_env(ag)
        assert out["pwd"] == os.getcwd()


# ── prompt + working dir ──────────────────────────────────────


class TestPromptWorkingDir:
    def test_agent_system_prompt(self):
        ag = SimpleNamespace(get_system_prompt=lambda: "you are X")
        assert co.agent_system_prompt(ag) == {"text": "you are X"}

    def test_agent_working_dir_from_workspace(self):
        ws = SimpleNamespace(get=lambda: "/ws")
        ag = SimpleNamespace(workspace=ws, executor=None)
        assert co.agent_working_dir(ag) == "/ws"

    def test_agent_working_dir_from_executor(self):
        ag = SimpleNamespace(
            workspace=None, executor=SimpleNamespace(_working_dir="/exec")
        )
        assert co.agent_working_dir(ag) == "/exec"

    def test_set_working_dir_no_workspace_raises(self):
        ag = SimpleNamespace(workspace=None)
        with pytest.raises(RuntimeError, match="no workspace"):
            co.agent_set_working_dir(ag, "/x")

    def test_set_working_dir_uses_workspace_setter(self):
        called = {}

        def _set(p):
            called["p"] = p
            return p

        ag = SimpleNamespace(workspace=SimpleNamespace(set=_set))
        out = co.agent_set_working_dir(ag, "/x")
        assert out == "/x"
        assert called["p"] == "/x"


# ── scratchpad ─────────────────────────────────────────────────


class _FakePad:
    def __init__(self):
        self.store = {}

    def to_dict(self):
        return dict(self.store)

    def set(self, k, v):
        self.store[k] = v

    def delete(self, k):
        self.store.pop(k, None)


class TestScratchpad:
    def test_scratchpad_reads(self):
        ag = SimpleNamespace(scratchpad=_FakePad())
        ag.scratchpad.set("k", "v")
        assert co.agent_scratchpad(ag) == {"k": "v"}

    def test_patch_set_and_delete(self):
        ag = SimpleNamespace(scratchpad=_FakePad())
        ag.scratchpad.set("old", "x")
        out = co.agent_patch_scratchpad(ag, {"new": "v", "old": None})
        assert out == {"new": "v"}

    def test_patch_reserved_key_raises(self):
        ag = SimpleNamespace(scratchpad=_FakePad())
        with pytest.raises(ValueError, match="Reserved"):
            co.agent_patch_scratchpad(ag, {"__reserved__": "x"})

    def test_patch_persists_snapshot_to_session_store(self, tmp_path):
        """Regression guard for B-fat-studio-3.

        ``agent_patch_scratchpad`` must snapshot the patched scratchpad
        into the attached session store's ``state`` table AND checkpoint
        the WAL — so a *separate* connection (the resume path) sees it
        even while the original store is still open and un-``close()``d
        (exactly a worker creature's store after it stops). Without the
        explicit ``save_state`` + ``checkpoint`` the patch is lost on
        resume: the observer's ``scratchpad_write`` events carry no
        value, and the turn-end ``SessionOutput`` snapshot never fires
        for an API-path patch with no turn after it.
        """
        from kohakuterrarium.session.store import SessionStore

        store = SessionStore(str(tmp_path / "g.kohakutr"))
        store.init_meta(
            session_id="g",
            config_type="agent",
            config_path="",
            pwd="",
            agents=["padkeeper"],
        )
        ag = SimpleNamespace(
            scratchpad=_FakePad(),
            session_store=store,
            config=SimpleNamespace(name="padkeeper"),
        )
        out = co.agent_patch_scratchpad(ag, {"recall": "kept"})
        assert out == {"recall": "kept"}
        # In-process the same store sees it immediately.
        assert store.load_scratchpad("padkeeper") == {"recall": "kept"}
        # A SEPARATE connection sees it WITHOUT the first store being
        # closed — this is the resume path against a still-open worker
        # store. This is the assertion that fails if the checkpoint is
        # dropped.
        fresh = SessionStore(str(tmp_path / "g.kohakutr"))
        try:
            assert fresh.load_scratchpad("padkeeper") == {"recall": "kept"}
        finally:
            fresh.close()
        store.close()

    def test_patch_without_session_store_is_noop_persist(self):
        """No attached store → patch still mutates in-memory, never raises."""
        ag = SimpleNamespace(scratchpad=_FakePad(), session_store=None)
        out = co.agent_patch_scratchpad(ag, {"k": "v"})
        assert out == {"k": "v"}


# ── triggers ───────────────────────────────────────────────────


class _TInfo:
    def __init__(self, tid):
        self.trigger_id = tid
        self.trigger_type = "tick"
        self.running = True
        self.created_at = datetime.now(timezone.utc)


class _TMgr:
    def list(self):
        return [_TInfo("t1")]


class TestTriggers:
    def test_agent_triggers_none(self):
        ag = SimpleNamespace(trigger_manager=None)
        assert co.agent_triggers(ag) == []

    def test_agent_triggers_returns(self):
        ag = SimpleNamespace(trigger_manager=_TMgr())
        out = co.agent_triggers(ag)
        assert out[0]["trigger_id"] == "t1"


# ── native_tool_options ────────────────────────────────────────


class _Tool:
    is_provider_native = True
    description = "tool d"

    @staticmethod
    def provider_native_option_schema():
        return {"k": {"type": "string"}}


class _Registry:
    def __init__(self, tools):
        self._tools = tools

    def list_tools(self):
        return list(self._tools.keys())

    def get_tool(self, name):
        return self._tools.get(name)


class _Helper:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, name):
        return self.data.get(name, {})

    def list(self):
        return dict(self.data)

    def set(self, name, values):
        self.data[name] = values
        return values


class TestNativeToolOptions:
    def test_inventory_none(self):
        ag = SimpleNamespace(registry=None, native_tool_options=None)
        assert co.agent_native_tool_inventory(ag) == []

    def test_inventory_lists_provider_native(self):
        ag = SimpleNamespace(
            registry=_Registry({"x": _Tool()}),
            native_tool_options=_Helper({"x": {"k": "v"}}),
        )
        out = co.agent_native_tool_inventory(ag)
        assert out[0]["name"] == "x"
        assert out[0]["values"] == {"k": "v"}

    def test_inventory_skips_non_native(self):
        class _NonNative:
            is_provider_native = False

        ag = SimpleNamespace(
            registry=_Registry({"y": _NonNative()}),
            native_tool_options=_Helper(),
        )
        assert co.agent_native_tool_inventory(ag) == []

    def test_get_native_tool_options(self):
        ag = SimpleNamespace(native_tool_options=_Helper({"x": {"a": 1}}))
        assert co.agent_get_native_tool_options(ag) == {"x": {"a": 1}}

    def test_get_native_options_missing_helper(self):
        ag = SimpleNamespace(native_tool_options=None)
        assert co.agent_get_native_tool_options(ag) == {}

    def test_set_native_tool_options(self):
        h = _Helper()
        ag = SimpleNamespace(native_tool_options=h)
        co.agent_set_native_tool_options(ag, "x", {"k": "v"})
        assert h.data == {"x": {"k": "v"}}

    def test_set_native_options_missing_helper_raises(self):
        ag = SimpleNamespace(native_tool_options=None)
        with pytest.raises(ValueError):
            co.agent_set_native_tool_options(ag, "x", {})


# ── plugins ────────────────────────────────────────────────────


class _Plugin:
    def __init__(self, name, enabled=True, options=None):
        self.name = name
        self.enabled = enabled
        self._options = options or {}

    def get_options(self):
        return self._options

    @staticmethod
    def option_schema():
        return {"k": {"type": "string"}}


class _PMgr:
    def __init__(self, plugins=None):
        self._plugins = plugins or {}
        self.enabled = set(p for p, _ in self._plugins.items())

    def list_plugins(self):
        return [p for _, p in self._plugins.items()]

    def list_plugins_with_options(self):
        return [
            {
                "name": name,
                "description": "d",
                "schema": p.option_schema(),
                "options": p.get_options(),
                "enabled": p.enabled,
                "priority": 0,
            }
            for name, p in self._plugins.items()
        ]

    def get_plugin(self, name):
        return self._plugins.get(name)

    def is_enabled(self, name):
        return name in self.enabled

    def enable(self, name):
        self.enabled.add(name)

    def disable(self, name):
        self.enabled.discard(name)


class TestPlugins:
    def test_list_plugins_none(self):
        ag = SimpleNamespace(plugin_manager=None, plugins=None)
        assert co.agent_list_plugins(ag) == []

    def test_list_plugins_objects(self):
        ag = SimpleNamespace(plugins=_PMgr({"p1": _Plugin("p1")}))
        out = co.agent_list_plugins(ag)
        assert out[0]["name"] == "p1"

    def test_list_plugins_dict_entries(self):
        class _D:
            def list_plugins(self):
                return [{"name": "x", "enabled": True}]

        ag = SimpleNamespace(plugins=_D())
        assert co.agent_list_plugins(ag) == [{"name": "x", "enabled": True}]

    def test_plugin_inventory_none(self):
        ag = SimpleNamespace(plugins=None)
        assert co.agent_plugin_inventory(ag) == []

    def test_plugin_inventory_filled(self):
        ag = SimpleNamespace(plugins=_PMgr({"p1": _Plugin("p1")}))
        out = co.agent_plugin_inventory(ag)
        assert out[0]["name"] == "p1"

    def test_get_plugin_options_unknown_raises(self):
        ag = SimpleNamespace(plugins=_PMgr())
        with pytest.raises(KeyError):
            co.agent_get_plugin_options(ag, "ghost")

    def test_get_plugin_options_no_pm(self):
        ag = SimpleNamespace(plugins=None)
        with pytest.raises(KeyError):
            co.agent_get_plugin_options(ag, "x")

    def test_get_plugin_options_filled(self):
        ag = SimpleNamespace(plugins=_PMgr({"p1": _Plugin("p1", options={"k": "v"})}))
        out = co.agent_get_plugin_options(ag, "p1")
        assert out["name"] == "p1"
        assert out["options"] == {"k": "v"}

    def test_set_plugin_options_no_helper(self):
        ag = SimpleNamespace(plugin_options=None)
        with pytest.raises(ValueError):
            co.agent_set_plugin_options(ag, "x", {})

    def test_set_plugin_options(self):
        h = _Helper()
        ag = SimpleNamespace(plugin_options=h)
        co.agent_set_plugin_options(ag, "x", {"k": "v"})
        assert h.data == {"x": {"k": "v"}}

    async def test_toggle_plugin_no_pm(self):
        ag = SimpleNamespace(plugins=None)
        with pytest.raises(ValueError):
            await co.agent_toggle_plugin(ag, "x")

    async def test_toggle_plugin_disable(self):
        ag = SimpleNamespace(plugins=_PMgr({"p1": _Plugin("p1")}))
        out = await co.agent_toggle_plugin(ag, "p1")
        assert out["enabled"] is False

    async def test_toggle_plugin_enable(self):
        pm = _PMgr({"p1": _Plugin("p1")})
        pm.disable("p1")
        ag = SimpleNamespace(plugins=pm)
        out = await co.agent_toggle_plugin(ag, "p1")
        assert out["enabled"] is True

    async def test_toggle_plugin_unknown_raises(self):
        """Regression guard for B-e2e-2: a plugin name the creature does
        not have must raise ``KeyError`` — not fabricate a success. The
        old impl called ``pm.enable(name)`` (a no-op for an unknown
        name) and returned ``{"enabled": True}`` regardless."""
        ag = SimpleNamespace(plugins=_PMgr({"p1": _Plugin("p1")}))
        with pytest.raises(KeyError):
            await co.agent_toggle_plugin(ag, "ghost")

    async def test_toggle_plugin_explicit_target_is_idempotent(self):
        """Regression guard for B-e2e-2: an explicit ``enabled`` target
        SETS the state (the HTTP/studio surface posts a desired state),
        it does not blindly flip. Disabling an already-disabled plugin
        leaves it disabled — a flip would wrongly re-enable it."""
        pm = _PMgr({"p1": _Plugin("p1")})
        pm.disable("p1")
        ag = SimpleNamespace(plugins=pm)
        out = await co.agent_toggle_plugin(ag, "p1", enabled=False)
        assert out["enabled"] is False
        assert pm.is_enabled("p1") is False
        # And an explicit enable target turns it on.
        out = await co.agent_toggle_plugin(ag, "p1", enabled=True)
        assert out["enabled"] is True
        assert pm.is_enabled("p1") is True


# ── module catalog ─────────────────────────────────────────────


class TestModuleCatalog:
    def test_list_modules_empty(self):
        ag = SimpleNamespace(registry=None, native_tool_options=None, plugins=None)
        assert co.agent_list_modules(ag) == []

    def test_get_module_options_plugin(self):
        ag = SimpleNamespace(plugins=_PMgr({"p1": _Plugin("p1")}))
        out = co.agent_get_module_options(ag, "plugin", "p1")
        assert out["type"] == "plugin"

    def test_get_module_options_native_tool(self):
        ag = SimpleNamespace(
            registry=_Registry({"t1": _Tool()}),
            native_tool_options=_Helper({"t1": {"k": "v"}}),
        )
        out = co.agent_get_module_options(ag, "native_tool", "t1")
        assert out["options"] == {"k": "v"}

    def test_get_module_options_unknown_tool_raises(self):
        ag = SimpleNamespace(registry=_Registry({}), native_tool_options=_Helper())
        with pytest.raises(KeyError):
            co.agent_get_module_options(ag, "native_tool", "ghost")

    def test_get_module_options_unknown_type_raises(self):
        ag = SimpleNamespace()
        with pytest.raises(ValueError):
            co.agent_get_module_options(ag, "garbage", "x")

    def test_set_module_options_unknown_type_raises(self):
        ag = SimpleNamespace()
        with pytest.raises(ValueError):
            co.agent_set_module_options(ag, "garbage", "x", {})

    async def test_toggle_module_native_tool_raises(self):
        ag = SimpleNamespace()
        with pytest.raises(ValueError, match="does not support"):
            await co.agent_toggle_module(ag, "native_tool", "x")

    async def test_toggle_module_plugin_dispatches(self):
        ag = SimpleNamespace(plugins=_PMgr({"p1": _Plugin("p1")}))
        out = await co.agent_toggle_module(ag, "plugin", "p1")
        assert "enabled" in out

    async def test_toggle_module_unknown_raises(self):
        ag = SimpleNamespace()
        with pytest.raises(ValueError):
            await co.agent_toggle_module(ag, "garbage", "x")


# ── execute_command ────────────────────────────────────────────


class TestExecuteCommand:
    async def test_unknown_command(self):
        ag = SimpleNamespace()
        with pytest.raises(ValueError, match="Unknown command"):
            await co.agent_execute_command(ag, "no-such-cmd")


# ── chat_history_for ───────────────────────────────────────────


class _Creature:
    def __init__(self, agent, graph_id="g1", name="alice"):
        self.agent = agent
        self.graph_id = graph_id
        self.name = name
        self.is_privileged = False
        self.parent_creature_id = None

    def get_status(self):
        return {
            "creature_id": "cid",
            "agent_id": "aid",
            "name": self.name,
            "is_running": True,
        }


class _Engine:
    def __init__(self, creatures=None, environments=None, graphs=None):
        self._creatures = creatures or {}
        self._environments = environments or {}
        self._graphs = graphs or {}
        self._session_stores = {}

    def get_creature(self, cid):
        if cid not in self._creatures:
            raise KeyError(cid)
        return self._creatures[cid]

    def get_graph(self, gid):
        if gid not in self._graphs:
            raise KeyError(gid)
        return self._graphs[gid]

    def list_graphs(self):
        return list(self._graphs.values())

    def list_output_wiring(self, cid):
        return []


class TestChatHistoryFor:
    def test_basic_no_events(self):
        agent = SimpleNamespace(
            conversation_history=[],
            session_store=None,
            _processing_task=None,
            _direct_job_meta={},
        )
        eng = _Engine(creatures={"c1": _Creature(agent)})
        out = co.chat_history_for(eng, "c1")
        assert out["events"] == []
        assert out["is_processing"] is False

    def test_branches_callable(self):
        agent = SimpleNamespace(list_branches=lambda: [{"branch": "main"}])
        eng = _Engine(creatures={"c1": _Creature(agent)})
        out = co.chat_branches_for(eng, "c1")
        assert out == [{"branch": "main"}]

    def test_branches_no_method(self):
        agent = SimpleNamespace()
        eng = _Engine(creatures={"c1": _Creature(agent)})
        assert co.chat_branches_for(eng, "c1") == []


# ── attach_policies_for / session_attach_policies_for ────────


class TestAttachPolicies:
    def test_unknown_creature_baseline(self):
        eng = _Engine()
        assert co.attach_policies_for(eng, "ghost") == ["log", "trace"]

    def test_with_input_module(self):
        agent = SimpleNamespace(input_module=object())
        eng = _Engine(creatures={"c1": _Creature(agent)})
        out = co.attach_policies_for(eng, "c1")
        assert "io" in out

    def test_session_unknown_graph_baseline(self):
        eng = _Engine()
        assert co.session_attach_policies_for(eng, "g") == [
            "log",
            "observer",
            "trace",
        ]

    def test_session_with_privileged_creature(self):
        c = _Creature(SimpleNamespace())
        c.is_privileged = True
        eng = _Engine(
            creatures={"c1": c},
            graphs={"g": SimpleNamespace(graph_id="g", creature_ids={"c1"})},
        )
        out = co.session_attach_policies_for(eng, "g")
        assert "io" in out


# ── _resolve_target_creature_id ───────────────────────────────


class TestResolveTarget:
    def test_empty_returns_empty(self):
        from kohakuterrarium.terrarium.topology import GraphTopology

        g = GraphTopology(graph_id="g", creature_ids=set(), channels={})
        assert co._resolve_target_creature_id(g, [], "") == ""

    def test_by_id_match(self):
        from kohakuterrarium.terrarium.topology import GraphTopology

        g = GraphTopology(graph_id="g", creature_ids={"c1"}, channels={})
        creatures = [{"creature_id": "c1", "name": "alice"}]
        assert co._resolve_target_creature_id(g, creatures, "c1") == "c1"

    def test_by_name_match(self):
        from kohakuterrarium.terrarium.topology import GraphTopology

        g = GraphTopology(graph_id="g", creature_ids={"c1"}, channels={})
        creatures = [{"creature_id": "c1", "name": "alice"}]
        assert co._resolve_target_creature_id(g, creatures, "alice") == "c1"

    def test_unknown_returns_empty(self):
        from kohakuterrarium.terrarium.topology import GraphTopology

        g = GraphTopology(graph_id="g", creature_ids={"c1"}, channels={})
        creatures = [{"creature_id": "c1", "name": "alice"}]
        assert co._resolve_target_creature_id(g, creatures, "ghost") == ""


# ── build_runtime_graph_snapshot_for ──────────────────────────


class TestBuildRuntimeGraphSnapshot:
    def test_empty_engine(self):
        eng = _Engine()
        out = co.build_runtime_graph_snapshot_for(eng)
        assert out["graphs"] == []
        assert "version" in out

    def test_with_meta_lookup(self):
        from kohakuterrarium.terrarium.topology import GraphTopology

        graph = GraphTopology(graph_id="g1", creature_ids=set(), channels={})
        eng = _Engine(
            graphs={"g1": graph},
            environments={
                "g1": SimpleNamespace(
                    shared_channels=SimpleNamespace(
                        list_channels=lambda: [],
                        get=lambda n: None,
                    )
                )
            },
        )
        meta = lambda gid: {"kind": "creature", "name": "alice"}
        out = co.build_runtime_graph_snapshot_for(eng, meta_lookup=meta)
        assert out["graphs"][0]["name"] == "alice"
