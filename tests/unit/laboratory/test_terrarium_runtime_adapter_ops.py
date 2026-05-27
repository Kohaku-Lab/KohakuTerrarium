"""Per-creature + wiring + identity dispatch tests for
:class:`TerrariumRuntimeAdapter`.

Companion to `test_terrarium_runtime_adapter.py` (topology / lifecycle /
channel ops); split out to keep each file under the 600-line cap. Same
harness: a real :class:`Terrarium` engine via `TestTerrariumBuilder`,
a fake `LabRegistrar`, and `creature.agent` stubbed per-op where the
test-builder's `_FakeAgent` lacks the production agent surface.
"""

from types import SimpleNamespace


from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.terrarium_runtime import (
    TerrariumRuntimeAdapter,
)
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class _FakeNode:
    def __init__(self, client_id=None):
        self.client_id = client_id
        self.registered = {}
        self.unregistered = []

    def register_app_extension(self, ns, handler):
        self.registered[ns] = handler

    def unregister_app_extension(self, ns):
        self.unregistered.append(ns)
        self.registered.pop(ns, None)


def _msg(type_, body=None, sender="ctrl") -> AppMessage:
    return AppMessage(
        namespace=TerrariumRuntimeAdapter.NAMESPACE,
        type=type_,
        body=body or {},
        sender_node=sender,
        request_id=None,
        in_reply_to=None,
    )


async def _make_adapter():
    engine = await (
        TestTerrariumBuilder()
        .with_creature("alice", responses=["hi"])
        .with_creature("bob")
        .with_channel("chat")
        .with_connection("alice", "bob", channel="chat")
        .build()
    )
    return TerrariumRuntimeAdapter(engine, _FakeNode())


# ── connect / disconnect ────────────────────────────────────────


class TestConnectDisconnect:
    async def test_disconnect_then_connect_round_trip(self):
        adapter = await _make_adapter()
        try:
            # alice↔bob start connected on "chat"; disconnect splits the
            # graph and the result names the affected channel.
            out = await adapter._dispatch(
                _msg(
                    "disconnect",
                    {
                        "sender_id": "alice",
                        "receiver_id": "bob",
                        "channel": "chat",
                    },
                )
            )
            assert out["result"]["delta_kind"] == "split"
            assert "chat" in out["result"]["channels"]
            # Reconnecting merges the two singleton graphs back together.
            out = await adapter._dispatch(
                _msg(
                    "connect",
                    {
                        "sender_id": "alice",
                        "receiver_id": "bob",
                        "channel": "chat",
                    },
                )
            )
            assert out["result"]["delta_kind"] == "merge"
            assert out["result"]["channel"] == "chat"
        finally:
            await adapter._engine.shutdown()


# ── per-creature control ops ────────────────────────────────────


def _stub_agent_control(creature):
    """Replace ``creature.agent`` with a control-surface stub.

    Records ``interrupt`` and exposes deterministic job stores so the
    adapter's control ops can be exercised without a live LLM agent.
    """
    state = {"interrupted": False}
    running = [SimpleNamespace(to_dict=lambda: {"id": "j1", "kind": "tool"})]

    async def _ex_cancel(jid):
        return jid == "exec-job"

    async def _sa_cancel(jid):
        return jid == "sa-job"

    creature.agent = SimpleNamespace(
        is_running=False,
        interrupt=lambda: state.__setitem__("interrupted", True),
        executor=SimpleNamespace(get_running_jobs=lambda: running, cancel=_ex_cancel),
        subagent_manager=SimpleNamespace(
            get_running_jobs=lambda: [], cancel=_sa_cancel
        ),
        _interrupt_direct_job=lambda jid: jid == "direct-job",
        _promote_handle=lambda jid: jid == "promote-me",
    )
    return state


class TestControlOps:
    async def test_interrupt_calls_agent_interrupt(self):
        adapter = await _make_adapter()
        try:
            state = _stub_agent_control(adapter._engine.get_creature("alice"))
            out = await adapter._dispatch(_msg("interrupt", {"creature_id": "alice"}))
            assert out == {}
            # The op's whole job is to forward to agent.interrupt().
            assert state["interrupted"] is True
        finally:
            await adapter._engine.shutdown()

    async def test_list_jobs_merges_executor_and_subagent_jobs(self):
        adapter = await _make_adapter()
        try:
            _stub_agent_control(adapter._engine.get_creature("alice"))
            out = await adapter._dispatch(_msg("list_jobs", {"creature_id": "alice"}))
            assert out["jobs"] == [{"id": "j1", "kind": "tool"}]
        finally:
            await adapter._engine.shutdown()

    async def test_stop_job_via_direct_interrupt(self):
        adapter = await _make_adapter()
        try:
            _stub_agent_control(adapter._engine.get_creature("alice"))
            out = await adapter._dispatch(
                _msg("stop_job", {"creature_id": "alice", "job_id": "direct-job"})
            )
            assert out == {"cancelled": True}
        finally:
            await adapter._engine.shutdown()

    async def test_stop_job_via_executor_cancel(self):
        adapter = await _make_adapter()
        try:
            _stub_agent_control(adapter._engine.get_creature("alice"))
            out = await adapter._dispatch(
                _msg("stop_job", {"creature_id": "alice", "job_id": "exec-job"})
            )
            assert out == {"cancelled": True}
        finally:
            await adapter._engine.shutdown()

    async def test_stop_job_via_subagent_cancel(self):
        adapter = await _make_adapter()
        try:
            _stub_agent_control(adapter._engine.get_creature("alice"))
            out = await adapter._dispatch(
                _msg("stop_job", {"creature_id": "alice", "job_id": "sa-job"})
            )
            assert out == {"cancelled": True}
        finally:
            await adapter._engine.shutdown()

    async def test_stop_job_unknown_reports_not_cancelled(self):
        adapter = await _make_adapter()
        try:
            _stub_agent_control(adapter._engine.get_creature("alice"))
            out = await adapter._dispatch(
                _msg("stop_job", {"creature_id": "alice", "job_id": "ghost"})
            )
            assert out == {"cancelled": False}
        finally:
            await adapter._engine.shutdown()

    async def test_promote_job_reports_outcome(self):
        adapter = await _make_adapter()
        try:
            _stub_agent_control(adapter._engine.get_creature("alice"))
            ok = await adapter._dispatch(
                _msg(
                    "promote_job",
                    {"creature_id": "alice", "job_id": "promote-me"},
                )
            )
            assert ok == {"promoted": True}
            miss = await adapter._dispatch(
                _msg("promote_job", {"creature_id": "alice", "job_id": "no"})
            )
            assert miss == {"promoted": False}
        finally:
            await adapter._engine.shutdown()

    async def test_control_op_on_unknown_creature_is_not_hosted(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(_msg("interrupt", {"creature_id": "ghost"}))
            assert out["error"]["kind"] == "creature_not_hosted"
        finally:
            await adapter._engine.shutdown()


# ── per-creature chat ops ───────────────────────────────────────


class TestChatOps:
    async def test_chat_history_returns_history_payload(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("chat_history", {"creature_id": "alice"})
            )
            # The op wraps the engine's chat-history view under "history".
            assert "history" in out
        finally:
            await adapter._engine.shutdown()

    async def test_chat_branches_returns_branches_payload(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("chat_branches", {"creature_id": "alice"})
            )
            assert "branches" in out
        finally:
            await adapter._engine.shutdown()

    async def test_chat_history_unknown_creature_not_hosted(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("chat_history", {"creature_id": "ghost"})
            )
            assert out["error"]["kind"] == "creature_not_hosted"
        finally:
            await adapter._engine.shutdown()

    async def test_regenerate_forwards_turn_args_to_agent(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            calls = []

            async def _regen(*, turn_index, branch_view):
                calls.append((turn_index, branch_view))

            creature.agent = SimpleNamespace(
                is_running=False, regenerate_last_response=_regen
            )
            out = await adapter._dispatch(
                _msg(
                    "regenerate",
                    {
                        "creature_id": "alice",
                        "turn_index": 3,
                        "branch_view": "v",
                    },
                )
            )
            # Stub agent has no ``_turn_index`` / ``_branch_id``
            # attributes, so the dispatcher only echoes the status.
            assert out == {"status": "regenerating"}
            # The turn/branch selectors are passed through verbatim.
            assert calls == [(3, "v")]
        finally:
            await adapter._engine.shutdown()

    async def test_regenerate_returns_opened_branch_for_navigator_promotion(self):
        # The frontend's chevron navigator <N/M> needs to promote
        # immediately on Retry / Save&Rerun — it can't wait for the
        # post-turn history resync. The dispatcher therefore surfaces
        # the agent's freshly-opened branch_id (and the turn_index it
        # belongs to) so the worker -> remote_service hop carries it
        # back to the frontend.
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")

            async def _regen(*, turn_index, branch_view):
                pass

            creature.agent = SimpleNamespace(
                is_running=False,
                regenerate_last_response=_regen,
                _turn_index=3,
                _branch_id=2,
            )
            out = await adapter._dispatch(
                _msg(
                    "regenerate",
                    {
                        "creature_id": "alice",
                        "turn_index": 3,
                        "branch_view": None,
                    },
                )
            )
            assert out == {
                "status": "regenerating",
                "turn_index": 3,
                "branch_id": 2,
            }
        finally:
            await adapter._engine.shutdown()

    async def test_edit_message_returns_edited_flag(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")

            async def _edit(msg_idx, content, **kw):
                # Echo back a truthy result only for the expected index.
                return msg_idx == 2

            creature.agent = SimpleNamespace(is_running=False, edit_and_rerun=_edit)
            out = await adapter._dispatch(
                _msg(
                    "edit_message",
                    {
                        "creature_id": "alice",
                        "msg_idx": 2,
                        "content": "fixed",
                    },
                )
            )
            # Successful edit echoes the canonical ``edited`` flag plus
            # the ``status: edited`` marker the remote_service shim
            # uses to detect "new-style" replies. Stub agent has no
            # ``_turn_index`` / ``_branch_id`` attributes, so those
            # keys are absent.
            assert out == {"edited": True, "status": "edited"}
        finally:
            await adapter._engine.shutdown()

    async def test_edit_message_returns_branch_id_when_agent_assigns_one(self):
        # Same rationale as the regenerate case above — the navigator
        # promotion is gated on receiving the new branch_id back from
        # the worker, so the dispatcher must surface it.
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")

            async def _edit(msg_idx, content, **kw):
                return True

            creature.agent = SimpleNamespace(
                is_running=False,
                edit_and_rerun=_edit,
                _turn_index=4,
                _branch_id=3,
            )
            out = await adapter._dispatch(
                _msg(
                    "edit_message",
                    {
                        "creature_id": "alice",
                        "msg_idx": 2,
                        "content": "fixed",
                    },
                )
            )
            assert out == {
                "edited": True,
                "status": "edited",
                "turn_index": 4,
                "branch_id": 3,
            }
        finally:
            await adapter._engine.shutdown()

    async def test_edit_message_failure_does_not_leak_branch_metadata(self):
        # When the agent refuses the edit (wrong target, etc.) the
        # ``edited: False`` reply must NOT carry a stale branch_id —
        # the frontend uses the truthiness of ``edited`` to decide
        # whether to promote the navigator, and a sticky branch_id
        # from a previous successful call would flip <1/1> to <2/2>
        # on a bona fide failure.
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")

            async def _edit(msg_idx, content, **kw):
                return False

            creature.agent = SimpleNamespace(
                is_running=False,
                edit_and_rerun=_edit,
                _turn_index=7,
                _branch_id=9,
            )
            out = await adapter._dispatch(
                _msg(
                    "edit_message",
                    {
                        "creature_id": "alice",
                        "msg_idx": 2,
                        "content": "fixed",
                    },
                )
            )
            assert out == {"edited": False}
        finally:
            await adapter._engine.shutdown()

    async def test_rewind_forwards_index_to_agent(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            rewound = []

            async def _rewind(idx):
                rewound.append(idx)

            creature.agent = SimpleNamespace(is_running=False, rewind_to=_rewind)
            out = await adapter._dispatch(
                _msg("rewind", {"creature_id": "alice", "msg_idx": 5})
            )
            assert out == {}
            assert rewound == [5]
        finally:
            await adapter._engine.shutdown()


# ── per-creature state ops ──────────────────────────────────────


class TestStateOps:
    async def test_scratchpad_read_and_patch(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            store = {"k": "v"}

            class _Scratch:
                def to_dict(self):
                    return dict(store)

                def set(self, key, val):
                    store[key] = val

                def delete(self, key):
                    store.pop(key, None)

            creature.agent = SimpleNamespace(is_running=False, scratchpad=_Scratch())
            out = await adapter._dispatch(
                _msg("get_scratchpad", {"creature_id": "alice"})
            )
            assert out == {"scratchpad": {"k": "v"}}
            patched = await adapter._dispatch(
                _msg(
                    "patch_scratchpad",
                    {"creature_id": "alice", "updates": {"k2": "v2"}},
                )
            )
            # The patch is applied and the fresh full scratchpad returned.
            assert patched["scratchpad"] == {"k": "v", "k2": "v2"}
        finally:
            await adapter._engine.shutdown()

    async def test_get_env_returns_env_payload(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(_msg("get_env", {"creature_id": "alice"}))
            # agent_env yields a dict; the op wraps it under "env".
            assert isinstance(out["env"], dict)
        finally:
            await adapter._engine.shutdown()

    async def test_list_triggers_returns_trigger_list(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            from datetime import datetime

            info = SimpleNamespace(
                trigger_id="t1",
                trigger_type="timer",
                running=True,
                created_at=datetime(2026, 1, 1, 12, 0, 0),
            )
            creature.agent = SimpleNamespace(
                is_running=False,
                trigger_manager=SimpleNamespace(list=lambda: [info]),
            )
            out = await adapter._dispatch(
                _msg("list_triggers", {"creature_id": "alice"})
            )
            # Each trigger is flattened into a serialisable dict.
            assert out["triggers"] == [
                {
                    "trigger_id": "t1",
                    "trigger_type": "timer",
                    "running": True,
                    "created_at": "2026-01-01T12:00:00",
                }
            ]
        finally:
            await adapter._engine.shutdown()

    async def test_list_triggers_no_manager_returns_empty(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            creature.agent = SimpleNamespace(is_running=False)
            out = await adapter._dispatch(
                _msg("list_triggers", {"creature_id": "alice"})
            )
            assert out == {"triggers": []}
        finally:
            await adapter._engine.shutdown()

    async def test_get_working_dir_and_set_working_dir(self, tmp_path):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            wd = {"path": "/old"}

            class _Workspace:
                def get(self):
                    return wd["path"]

                def set(self, new):
                    # set_working_dir mutates the workspace and echoes
                    # the resolved path back.
                    wd["path"] = new
                    return new

            creature.agent = SimpleNamespace(is_running=False, workspace=_Workspace())
            target = str(tmp_path)
            out = await adapter._dispatch(
                _msg(
                    "set_working_dir",
                    {"creature_id": "alice", "new_path": target},
                )
            )
            assert out == {"working_dir": target}
            # A follow-up read reflects the mutation.
            got = await adapter._dispatch(
                _msg("get_working_dir", {"creature_id": "alice"})
            )
            assert got == {"working_dir": target}
        finally:
            await adapter._engine.shutdown()

    async def test_get_system_prompt_returns_helper_payload(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("get_system_prompt", {"creature_id": "alice"})
            )
            # agent_system_prompt returns a dict that the op returns as-is.
            assert isinstance(out, dict)
        finally:
            await adapter._engine.shutdown()

    async def test_native_tool_inventory_and_options(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("native_tool_inventory", {"creature_id": "alice"})
            )
            assert isinstance(out["inventory"], list)
            opts = await adapter._dispatch(
                _msg("get_native_tool_options", {"creature_id": "alice"})
            )
            assert isinstance(opts["options"], dict)
        finally:
            await adapter._engine.shutdown()

    async def test_set_native_tool_options_forwards_values(self):
        adapter = await _make_adapter()
        try:
            creature = adapter._engine.get_creature("alice")
            received = {}

            def _set_opts(tool, values):
                received["tool"] = tool
                received["values"] = values
                return {"applied": True}

            creature.agent = SimpleNamespace(
                is_running=False,
                set_native_tool_option=lambda *a, **k: None,
            )
            # Patch the helper indirection: the adapter calls
            # agent_set_native_tool_options(agent, tool, values); stub the
            # agent surface that helper reaches.
            import kohakuterrarium.laboratory.adapters.terrarium_runtime as mod

            orig = mod.agent_set_native_tool_options
            mod.agent_set_native_tool_options = lambda ag, tool, values: _set_opts(
                tool, values
            )
            try:
                out = await adapter._dispatch(
                    _msg(
                        "set_native_tool_options",
                        {
                            "creature_id": "alice",
                            "tool": "bash",
                            "values": {"timeout": 5},
                        },
                    )
                )
            finally:
                mod.agent_set_native_tool_options = orig
            assert out == {"options": {"applied": True}}
            assert received == {"tool": "bash", "values": {"timeout": 5}}
        finally:
            await adapter._engine.shutdown()
