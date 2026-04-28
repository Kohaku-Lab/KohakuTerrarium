"""Coverage tests for ``studio.sessions.creature_*`` modules.

Each module is a thin delegation layer over the engine + agent.  We
plug a fake ``Agent`` into a real ``Creature`` adopted by a real
``Terrarium``, then call each helper and assert the right method ran
on the agent.

Modules covered:

* ``creature_chat`` — chat / regenerate / edit / rewind / history /
  branches
* ``creature_command`` — execute_command (built-in slash dispatch)
* ``creature_ctl`` — interrupt / list_jobs / cancel_job / promote_job
* ``creature_model`` — switch_model / set_native_tool_options
* ``creature_plugins`` — list_plugins / toggle_plugin
* ``creature_state`` — scratchpad / triggers / env / system_prompt /
  working_dir / native_tool_options
"""

import pytest

import kohakuterrarium.studio.sessions.creature_chat as chat_mod
import kohakuterrarium.studio.sessions.creature_command as command_mod
import kohakuterrarium.studio.sessions.creature_ctl as ctl_mod
import kohakuterrarium.studio.sessions.creature_model as model_mod
import kohakuterrarium.studio.sessions.creature_plugins as plugins_mod
import kohakuterrarium.studio.sessions.creature_state as state_mod
from kohakuterrarium.terrarium.engine import Terrarium

from tests.unit.studio_sessions._fakes import (
    _FakeTriggerInfo,
    install_fake_creature,
    stub_chat_iter,
)

# ---------------------------------------------------------------------------
# creature_chat
# ---------------------------------------------------------------------------


class TestChat:
    @pytest.mark.asyncio
    async def test_chat_streams_chunks(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            stub_chat_iter(c, ["hel", "lo"])
            chunks: list[str] = []
            async for chunk in chat_mod.chat(engine, c.graph_id, "alice", "hi"):
                chunks.append(chunk)
            assert chunks == ["hel", "lo"]
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_regenerate_dispatches(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            await chat_mod.regenerate(engine, c.graph_id, "alice")
            assert c.agent.regen_calls == 1
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_edit_message_dispatches(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            ok = await chat_mod.edit_message(
                engine,
                c.graph_id,
                "alice",
                3,
                "new",
                turn_index=2,
                user_position=1,
            )
            assert ok is True
            assert c.agent.edit_calls == [(3, "new", 2, 1)]
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_rewind_dispatches(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            await chat_mod.rewind(engine, c.graph_id, "alice", 7)
            assert c.agent.rewind_calls == [7]
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_history_with_no_session_store(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            c.agent.conversation_history = [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
            payload = chat_mod.history(engine, c.graph_id, "alice")
            assert payload["creature_id"] == "alice"
            assert payload["session_id"] == c.graph_id
            assert payload["events"] == []
            assert payload["is_processing"] is False
            assert payload["messages"][1]["content"] == "hello"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_history_uses_attached_session_store(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            recorded = {"agent_name": None}

            class _Store:
                def get_resumable_events(self, agent_name):
                    recorded["agent_name"] = agent_name
                    return [
                        {"agent": agent_name, "type": "user_input"},
                        {"agent": agent_name, "type": "text"},
                    ]

            c.agent.session_store = _Store()
            payload = chat_mod.history(engine, c.graph_id, "alice")
            assert payload["events"] and len(payload["events"]) == 2
            assert recorded["agent_name"] == "alice"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_history_falls_back_to_lifecycle_store(self, monkeypatch):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            c.agent.session_store = None
            captured = {"calls": 0}

            class _LifecycleStore:
                def get_resumable_events(self, agent_name):
                    captured["calls"] += 1
                    return [{"agent": agent_name, "type": "tool_call"}]

            monkeypatch.setattr(
                chat_mod,
                "get_session_store",
                lambda sid: _LifecycleStore(),
            )
            payload = chat_mod.history(engine, c.graph_id, "alice")
            assert captured["calls"] == 1
            assert payload["events"][0]["type"] == "tool_call"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_history_swallows_session_store_errors(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")

            class _BoomStore:
                def get_resumable_events(self, _agent):
                    raise RuntimeError("disk full")

            c.agent.session_store = _BoomStore()
            payload = chat_mod.history(engine, c.graph_id, "alice")
            # Falls through to the empty list on exception
            assert payload["events"] == []
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_history_swallows_lifecycle_store_errors(self, monkeypatch):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            c.agent.session_store = None

            class _BoomStore:
                def get_resumable_events(self, _agent):
                    raise RuntimeError("disk full")

            monkeypatch.setattr(
                chat_mod, "get_session_store", lambda _sid: _BoomStore()
            )
            payload = chat_mod.history(engine, c.graph_id, "alice")
            assert payload["events"] == []
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_history_returns_channel_payload(self, monkeypatch):
        """``target = "ch:<name>"`` is the tab key the frontend sends
        for terrarium channel views (``stores/chat.js:1297``).  History
        must shape a channel payload from the attached session store
        instead of 404'ing on ``find_creature`` (regression for
        api-audit row 2.2 BROKEN entry).
        """
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")

            class _ChannelStore:
                def get_channel_messages(self, channel):
                    assert channel == "tasks"
                    return [
                        {"sender": "alice", "content": "hello", "ts": 1.0},
                        {"sender": "bob", "content": "hi back", "ts": 2.0},
                    ]

            monkeypatch.setattr(
                chat_mod, "get_session_store", lambda _sid: _ChannelStore()
            )
            payload = chat_mod.history(engine, c.graph_id, "ch:tasks")
            assert payload["creature_id"] == "ch:tasks"
            assert payload["messages"] == []
            assert payload["is_processing"] is False
            assert len(payload["events"]) == 2
            assert payload["events"][0]["type"] == "channel_message"
            assert payload["events"][0]["channel"] == "tasks"
            assert payload["events"][0]["sender"] == "alice"
            assert payload["events"][1]["content"] == "hi back"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_history_channel_with_no_store(self, monkeypatch):
        """No attached store -> empty event list (no exception)."""
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            monkeypatch.setattr(chat_mod, "get_session_store", lambda _sid: None)
            payload = chat_mod.history(engine, c.graph_id, "ch:none")
            assert payload["events"] == []
            assert payload["creature_id"] == "ch:none"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_branches_returns_per_turn_metadata(self, monkeypatch):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")

            class _Store:
                def get_resumable_events(self, _agent):
                    return [
                        {"type": "user_input", "turn_index": 1, "branch": "main"},
                    ]

            c.agent.session_store = _Store()
            monkeypatch.setattr(
                chat_mod,
                "collect_branch_metadata",
                lambda events: {1: {"branches": ["main"], "latest_branch": "main"}},
            )
            out = chat_mod.branches(engine, c.graph_id, "alice")
            assert out["creature_id"] == "alice"
            assert out["turns"][0]["turn_index"] == 1
            assert out["turns"][0]["branches"] == ["main"]
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# creature_command
# ---------------------------------------------------------------------------


class _FakeCommandResult:
    def __init__(
        self,
        output: str = "",
        error: str = "",
        success: bool = True,
        data=None,
    ) -> None:
        self.output = output
        self.error = error
        self.success = success
        self.data = data


class _FakeCommand:
    def __init__(self, result: _FakeCommandResult):
        self.result = result
        self.calls: list[tuple] = []

    async def execute(self, args, context):
        self.calls.append((args, context))
        return self.result


class TestExecuteCommand:
    @pytest.mark.asyncio
    async def test_dispatches_to_builtin(self, monkeypatch):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            cmd = _FakeCommand(_FakeCommandResult(output="ok"))
            monkeypatch.setattr(
                command_mod, "get_builtin_user_command", lambda name: cmd
            )
            resp = await command_mod.execute_command(
                engine, c.graph_id, "alice", "status", "x"
            )
            assert resp["command"] == "status"
            assert resp["output"] == "ok"
            assert resp["success"] is True
            assert "data" not in resp
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_includes_data_when_present(self, monkeypatch):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            cmd = _FakeCommand(_FakeCommandResult(output="", data={"k": 1}))
            monkeypatch.setattr(
                command_mod, "get_builtin_user_command", lambda name: cmd
            )
            resp = await command_mod.execute_command(
                engine, c.graph_id, "alice", "info"
            )
            assert resp["data"] == {"k": 1}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_unknown_command_raises(self, monkeypatch):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            monkeypatch.setattr(
                command_mod, "get_builtin_user_command", lambda name: None
            )
            with pytest.raises(ValueError, match="Unknown command"):
                await command_mod.execute_command(engine, c.graph_id, "alice", "ghost")
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# creature_ctl
# ---------------------------------------------------------------------------


class TestCtl:
    @pytest.mark.asyncio
    async def test_interrupt(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            ctl_mod.interrupt(engine, c.graph_id, "alice")
            assert c.agent.interrupt_calls == 1
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_list_jobs_collects_executor_and_subagent(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            c.agent.executor.add_job("tool_1")
            c.agent.subagent_manager.add_job("sub_1")
            jobs = ctl_mod.list_jobs(engine, c.graph_id, "alice")
            ids = {j["job_id"] for j in jobs}
            assert ids == {"tool_1", "sub_1"}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_cancel_job_executor_hit(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            c.agent.executor.add_job("tool_1")
            ok = await ctl_mod.cancel_job(engine, c.graph_id, "alice", "tool_1")
            assert ok is True
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_cancel_job_subagent_hit(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            c.agent.subagent_manager.add_job("sub_1")
            ok = await ctl_mod.cancel_job(engine, c.graph_id, "alice", "sub_1")
            assert ok is True
            assert "sub_1" in c.agent.subagent_manager.cancelled
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_cancel_job_miss(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            ok = await ctl_mod.cancel_job(engine, c.graph_id, "alice", "ghost")
            assert ok is False
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_cancel_job_via_direct_handle(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")

            def _hit(job_id):
                return job_id == "direct"

            c.agent._interrupt_direct_job = _hit
            ok = await ctl_mod.cancel_job(engine, c.graph_id, "alice", "direct")
            assert ok is True
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_promote_job(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            assert ctl_mod.promote_job(engine, c.graph_id, "alice", "promote-me")
            assert not ctl_mod.promote_job(engine, c.graph_id, "alice", "ghost")
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# creature_model
# ---------------------------------------------------------------------------


class TestModel:
    @pytest.mark.asyncio
    async def test_switch_model(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            out = model_mod.switch_model(engine, c.graph_id, "alice", "gpt-9")
            assert out == "gpt-9"
            assert c.agent.switched_to == "gpt-9"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_set_native_tool_options(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            out = model_mod.set_native_tool_options(
                engine, c.graph_id, "alice", "web_search", {"max_results": 5}
            )
            assert out == {"max_results": 5}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_set_native_tool_options_passes_empty_dict(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            out = model_mod.set_native_tool_options(
                engine, c.graph_id, "alice", "web_search", None
            )
            assert out == {}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_set_native_tool_options_no_helper_raises(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(
                engine, "alice", with_native_tool_options=False
            )
            with pytest.raises(ValueError, match="no native_tool_options"):
                model_mod.set_native_tool_options(
                    engine, c.graph_id, "alice", "web_search", {}
                )
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# creature_plugins
# ---------------------------------------------------------------------------


class TestPlugins:
    @pytest.mark.asyncio
    async def test_list_plugins_with_manager(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            out = plugins_mod.list_plugins(engine, c.graph_id, "alice")
            names = [p["name"] for p in out]
            assert "plug_a" in names
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_list_plugins_empty_when_no_manager(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice", with_plugins=False)
            assert plugins_mod.list_plugins(engine, c.graph_id, "alice") == []
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_toggle_plugin_enable(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            # plug_b starts disabled — toggling enables it
            out = await plugins_mod.toggle_plugin(engine, c.graph_id, "alice", "plug_b")
            assert out == {"name": "plug_b", "enabled": True}
            assert c.agent.plugins.load_pending_calls == 1
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_toggle_plugin_disable(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            out = await plugins_mod.toggle_plugin(engine, c.graph_id, "alice", "plug_a")
            assert out == {"name": "plug_a", "enabled": False}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_toggle_plugin_no_manager_raises(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice", with_plugins=False)
            with pytest.raises(ValueError, match="No plugins"):
                await plugins_mod.toggle_plugin(engine, c.graph_id, "alice", "plug_a")
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# creature_state
# ---------------------------------------------------------------------------


class TestState:
    @pytest.mark.asyncio
    async def test_get_scratchpad(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            c.agent.scratchpad.set("plan", "do thing")
            out = state_mod.get_scratchpad(engine, c.graph_id, "alice")
            assert out == {"plan": "do thing"}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_patch_scratchpad_set_and_delete(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            c.agent.scratchpad.set("a", "1")
            out = state_mod.patch_scratchpad(
                engine, c.graph_id, "alice", {"b": "2", "a": None}
            )
            assert out == {"b": "2"}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_patch_scratchpad_rejects_reserved(self, monkeypatch):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            monkeypatch.setattr(
                state_mod, "is_reserved_scratchpad_key", lambda k: k == "system"
            )
            with pytest.raises(ValueError, match="Reserved"):
                state_mod.patch_scratchpad(engine, c.graph_id, "alice", {"system": "v"})
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_list_triggers(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            c.agent.trigger_manager._triggers["t1"] = _FakeTriggerInfo(
                trigger_id="t1", trigger_type="timer"
            )
            out = state_mod.list_triggers(engine, c.graph_id, "alice")
            assert len(out) == 1
            assert out[0]["trigger_id"] == "t1"
            assert out[0]["trigger_type"] == "timer"
            assert out[0]["running"] is True
            assert "T" in out[0]["created_at"]
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_list_triggers_no_manager(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            c.agent.trigger_manager = None
            assert state_mod.list_triggers(engine, c.graph_id, "alice") == []
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_get_env_redacts_secrets(self, monkeypatch):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            monkeypatch.setenv("SAFE_VAR", "ok")
            monkeypatch.setenv("MY_SECRET_KEY", "hush")
            out = state_mod.get_env(engine, c.graph_id, "alice")
            assert "SAFE_VAR" in out["env"]
            # Anything matching a redact-substring is filtered
            assert "MY_SECRET_KEY" not in out["env"]
            assert out["pwd"]
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_get_env_uses_cwd_when_no_working_dir(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            c.agent._working_dir = None
            out = state_mod.get_env(engine, c.graph_id, "alice")
            assert out["pwd"]  # falls back to os.getcwd()
        finally:
            await engine.shutdown()

    def test_redacted_env_filters(self, monkeypatch):
        monkeypatch.setenv("APP_TOKEN", "x")
        monkeypatch.setenv("APP_NAME", "y")
        out = state_mod._redacted_env()
        assert "APP_TOKEN" not in out
        assert "APP_NAME" in out

    @pytest.mark.asyncio
    async def test_get_system_prompt(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            c.agent.system_prompt_text = "I am alice."
            out = state_mod.get_system_prompt(engine, c.graph_id, "alice")
            assert out == {"text": "I am alice."}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_working_dir_get_set(self, tmp_path):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            assert state_mod.get_working_dir(engine, c.graph_id, "alice") == "."
            new = state_mod.set_working_dir(engine, c.graph_id, "alice", str(tmp_path))
            assert new == str(tmp_path)
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_get_working_dir_no_workspace_falls_back_to_executor(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice", with_workspace=False)
            c.agent.executor._working_dir = "/path/x"
            out = state_mod.get_working_dir(engine, c.graph_id, "alice")
            assert out == "/path/x"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_set_working_dir_no_workspace_raises(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice", with_workspace=False)
            with pytest.raises(RuntimeError, match="no workspace helper"):
                state_mod.set_working_dir(engine, c.graph_id, "alice", "/x")
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_native_tool_inventory_filters_to_provider_native(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")

            class _NativeTool:
                description = "native"
                is_provider_native = True

                @classmethod
                def provider_native_option_schema(cls):
                    return {"properties": {"x": {"type": "string"}}}

            class _PlainTool:
                description = "plain"
                is_provider_native = False

            c.agent.registry.add_tool("native", _NativeTool())
            c.agent.registry.add_tool("plain", _PlainTool())
            out = state_mod.native_tool_inventory(engine, c.graph_id, "alice")
            assert len(out) == 1
            entry = out[0]
            assert entry["name"] == "native"
            assert entry["option_schema"]["properties"]["x"]["type"] == "string"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_native_tool_inventory_swallows_schema_errors(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")

            class _BoomTool:
                description = "x"
                is_provider_native = True

                @classmethod
                def provider_native_option_schema(cls):
                    raise RuntimeError("schema unavailable")

            c.agent.registry.add_tool("boom", _BoomTool())
            out = state_mod.native_tool_inventory(engine, c.graph_id, "alice")
            assert out[0]["option_schema"] == {}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_native_tool_inventory_handles_missing_tool(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            # Tool registered then mock get_tool to return None
            c.agent.registry._tools["ghost"] = None
            out = state_mod.native_tool_inventory(engine, c.graph_id, "alice")
            assert out == []
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_get_native_tool_options(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            c.agent.native_tool_options.set("web_search", {"k": 5})
            out = state_mod.get_native_tool_options(engine, c.graph_id, "alice")
            assert out == {"web_search": {"k": 5}}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_get_native_tool_options_no_helper(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(
                engine, "alice", with_native_tool_options=False
            )
            assert state_mod.get_native_tool_options(engine, c.graph_id, "alice") == {}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_set_native_tool_options(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            out = state_mod.set_native_tool_options(
                engine, c.graph_id, "alice", "web_search", {"k": 3}
            )
            assert out == {"k": 3}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_set_native_tool_options_handles_none_values(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            out = state_mod.set_native_tool_options(
                engine, c.graph_id, "alice", "web_search", None
            )
            assert out == {}
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_set_native_tool_options_no_helper_raises(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(
                engine, "alice", with_native_tool_options=False
            )
            with pytest.raises(ValueError, match="no native_tool_options"):
                state_mod.set_native_tool_options(engine, c.graph_id, "alice", "x", {})
        finally:
            await engine.shutdown()
