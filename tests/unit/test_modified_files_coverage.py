"""Coverage fill for files modified during the session-v2 fix pass.

Targets the lines that the focused branching tests don't exercise:
observability wirers, runtime token-usage emitters, agent message
edge-cases, migration helper paths, output stamping. Keeps coverage
on the modified surface honestly above the ~80% bar.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kohakuterrarium.core.agent_messages import AgentMessagesMixin
from kohakuterrarium.core.agent_observability import (
    build_session_info,
    init_branch_state,
    wire_plugin_hook_timing,
    wire_scratchpad_observer,
)
from kohakuterrarium.core.agent_runtime_tools import AgentRuntimeToolsMixin
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.session.migrations.v1_to_v2 import (
    _backfill_assistant_tool_call_content,
    _coerce_args,
    _flush_pending_tool_calls,
    _synth_events_from_message,
    _synth_events_from_snapshot,
    _translate_v1_events,
)
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore

# ─── agent_observability.init_branch_state ────────────────────────────


class TestInitBranchState:
    def test_writes_all_fields(self):
        agent = SimpleNamespace()
        init_branch_state(agent)
        assert agent._wiring_resolver is None
        assert agent._turn_index == 0
        assert agent._branch_id == 0
        assert agent._parent_branch_path == []
        assert agent._last_turn_text == []
        assert agent._turn_usage_accum == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
        }


# ─── agent_observability.wire_scratchpad_observer ─────────────────────


class TestWireScratchpadObserver:
    def test_noop_without_session(self):
        agent = SimpleNamespace(session=None, output_router=None, config=None)
        wire_scratchpad_observer(agent)  # no raise

    def test_noop_without_router(self):
        pad = SimpleNamespace(set_write_observer=lambda fn: None)
        session = SimpleNamespace(scratchpad=pad)
        agent = SimpleNamespace(
            session=session,
            output_router=None,
            config=SimpleNamespace(name="alice"),
        )
        wire_scratchpad_observer(agent)  # short-circuits at router check

    def test_observer_emits_via_router(self):
        captured = []

        def _set_observer(fn):
            captured.append(fn)

        pad = SimpleNamespace(set_write_observer=_set_observer)
        session = SimpleNamespace(scratchpad=pad)
        notified = []
        router = SimpleNamespace(
            notify_activity=lambda *a, **kw: notified.append((a, kw)),
        )
        agent = SimpleNamespace(
            session=session,
            output_router=router,
            config=SimpleNamespace(name="alice"),
        )
        wire_scratchpad_observer(agent)
        assert captured, "observer should be installed"
        captured[0]("plan", "set", 42)
        assert notified
        a, kw = notified[0]
        assert a[0] == "scratchpad_write"
        assert "alice" in a[1]
        assert kw["metadata"]["key"] == "plan"
        assert kw["metadata"]["size_bytes"] == 42


# ─── agent_observability.wire_plugin_hook_timing ──────────────────────


class TestWirePluginHookTiming:
    def test_noop_without_plugins(self):
        agent = SimpleNamespace(plugins=None, output_router=None)
        wire_plugin_hook_timing(agent)

    def test_noop_when_callback_missing(self):
        plugins = SimpleNamespace()  # no set_hook_timing_callback
        agent = SimpleNamespace(
            plugins=plugins,
            output_router=SimpleNamespace(notify_activity=lambda *a, **kw: None),
        )
        wire_plugin_hook_timing(agent)

    def test_observer_emits_metadata(self):
        captured = []
        plugins = SimpleNamespace(
            set_hook_timing_callback=lambda fn: captured.append(fn),
        )
        notified = []
        router = SimpleNamespace(
            notify_activity=lambda *a, **kw: notified.append(kw["metadata"]),
        )
        agent = SimpleNamespace(plugins=plugins, output_router=router)
        wire_plugin_hook_timing(agent)
        assert captured
        captured[0]("pre_tool_call", "auth_plugin", 12.5, False)
        assert notified
        meta = notified[0]
        assert meta["plugin"] == "auth_plugin"
        assert meta["hook"] == "pre_tool_call"
        assert abs(meta["duration_ms"] - 12.5) < 1e-6
        assert meta["blocked"] is False


# ─── agent_observability.build_session_info ───────────────────────────


class TestBuildSessionInfo:
    def test_no_store_returns_empty_payload(self):
        agent = SimpleNamespace(
            config=SimpleNamespace(name="alice"), session_store=None
        )
        assert build_session_info(agent, "own") == {"agent": "alice", "tokens": {}}
        assert build_session_info(agent, "all_loops") == {
            "agent": "alice",
            "tokens": [],
        }

    def test_returns_token_views_from_store(self):
        store = MagicMock()
        store.token_usage.return_value = {"prompt": 10}
        store.token_usage_all_loops.return_value = [("alice", {"prompt": 10})]
        agent = SimpleNamespace(
            config=SimpleNamespace(name="alice"), session_store=store
        )
        own = build_session_info(agent, "own")
        assert own["tokens"] == {"prompt": 10}
        all_loops = build_session_info(agent, "all_loops")
        assert all_loops["tokens"] == [("alice", {"prompt": 10})]


# ─── agent_runtime_tools._emit_token_usage ────────────────────────────


class _FakeRouter:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def notify_activity(
        self, activity_type: str, detail: str, metadata: dict | None = None
    ) -> None:
        self.events.append((activity_type, detail, metadata or {}))


class _RuntimeToolsHost(AgentRuntimeToolsMixin):
    def __init__(self):
        self.config = SimpleNamespace(name="alice")
        self.output_router = _FakeRouter()
        self._direct_job_meta = {}
        self._bg_controller_notify = {}
        self._turn_usage_accum = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
        }


class TestEmitTokenUsage:
    def test_noop_when_no_usage(self):
        host = _RuntimeToolsHost()
        ctrl = SimpleNamespace(_last_usage={})
        host._emit_token_usage(ctrl)
        assert host.output_router.events == []

    def test_basic_usage_accumulates_and_emits(self):
        host = _RuntimeToolsHost()
        ctrl = SimpleNamespace(
            _last_usage={
                "prompt_tokens": 100,
                "completion_tokens": 25,
                "total_tokens": 125,
            }
        )
        host._emit_token_usage(ctrl)
        types = [e[0] for e in host.output_router.events]
        assert "token_usage" in types
        # No cache fields → no cache_stats event.
        assert "cache_stats" not in types
        assert host._turn_usage_accum["prompt_tokens"] == 100
        assert host._turn_usage_accum["completion_tokens"] == 25
        assert host._turn_usage_accum["total_tokens"] == 125

    def test_cache_fields_emit_cache_stats(self):
        host = _RuntimeToolsHost()
        ctrl = SimpleNamespace(
            _last_usage={
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "cached_tokens": 40,
                "cache_creation_input_tokens": 60,
            }
        )
        host._emit_token_usage(ctrl)
        types = [e[0] for e in host.output_router.events]
        assert "cache_stats" in types
        cache_evt = next(e for e in host.output_router.events if e[0] == "cache_stats")
        meta = cache_evt[2]
        assert meta["cache_write"] == 60
        assert meta["cache_read"] == 40
        assert abs(meta["cache_hit_ratio"] - 0.4) < 1e-6

    def test_anthropic_style_cache_read_field(self):
        host = _RuntimeToolsHost()
        ctrl = SimpleNamespace(
            _last_usage={
                "prompt_tokens": 200,
                "cache_read_input_tokens": 80,
            }
        )
        host._emit_token_usage(ctrl)
        cache_evt = next(
            (e for e in host.output_router.events if e[0] == "cache_stats"), None
        )
        assert cache_evt is not None
        assert cache_evt[2]["cache_read"] == 80


# ─── agent_messages: error / no-op paths ──────────────────────────────


class _NoStoreController:
    def __init__(self):
        self.conversation = Conversation()


class _NoStoreAgent(AgentMessagesMixin):
    def __init__(self):
        self.config = SimpleNamespace(name="alice")
        self.session_store = None
        self.controller = _NoStoreController()
        self._turn_index = 0
        self._branch_id = 0
        self._parent_branch_path: list[tuple[int, int]] = []
        self._rerun_calls: list[str] = []

    async def _rerun_from_last(self, new_user_content: str = "") -> None:
        self._rerun_calls.append(new_user_content)


@pytest.mark.asyncio
async def test_regenerate_with_no_user_message_is_noop():
    agent = _NoStoreAgent()
    await agent.regenerate_last_response()
    assert agent._rerun_calls == []  # never invoked rerun
    assert agent._branch_id == 0  # state unchanged


@pytest.mark.asyncio
async def test_regenerate_without_session_store_still_runs():
    agent = _NoStoreAgent()
    agent.controller.conversation.append("user", "hi")
    agent.controller.conversation.append("assistant", "first reply")
    agent._turn_index = 1
    agent._branch_id = 1
    await agent.regenerate_last_response()
    # Conversation is truncated past the last user message.
    msgs = agent.controller.conversation.get_messages()
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert len(agent._rerun_calls) == 1


@pytest.mark.asyncio
async def test_edit_invalid_index_is_noop():
    agent = _NoStoreAgent()
    agent.controller.conversation.append("user", "hi")
    await agent.edit_and_rerun(99, "edited")
    assert agent._rerun_calls == []


@pytest.mark.asyncio
async def test_edit_non_user_role_rejected():
    agent = _NoStoreAgent()
    agent.controller.conversation.append("user", "hi")
    agent.controller.conversation.append("assistant", "reply")
    assert not await agent.edit_and_rerun(1, "should-fail")  # idx 1 is assistant
    assert agent._rerun_calls == []


@pytest.mark.asyncio
async def test_edit_position_target_skips_system_prompt():
    agent = _NoStoreAgent()
    agent.controller.conversation.append("system", "sys")
    agent.controller.conversation.append("user", "hi")
    agent.controller.conversation.append("assistant", "reply")
    agent._turn_index = 1
    agent._branch_id = 1
    assert await agent.edit_and_rerun(0, "edited", user_position=0)
    msgs = agent.controller.conversation.get_messages()
    assert [m.role for m in msgs] == ["system"]
    assert agent._rerun_calls == ["edited"]
    assert agent._branch_id == 2


@pytest.mark.asyncio
async def test_rewind_to_truncates_without_rerun(tmp_path):
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    agent = _NoStoreAgent()
    agent.session_store = store
    agent.controller.conversation.append("user", "u1")
    agent.controller.conversation.append("assistant", "a1")
    agent.controller.conversation.append("user", "u2")
    await agent.rewind_to(1)
    assert len(agent.controller.conversation.get_messages()) == 1
    assert agent._rerun_calls == []  # rewind does NOT rerun
    store.close(update_status=False)


# ─── v1_to_v2 helpers ──────────────────────────────────────────────────


class TestCoerceArgs:
    def test_string_passthrough(self):
        assert _coerce_args('{"a": 1}') == '{"a": 1}'

    def test_none_returns_empty_object(self):
        assert _coerce_args(None) == "{}"

    def test_dict_serialized(self):
        out = _coerce_args({"a": 1})
        assert "a" in out and "1" in out

    def test_unserializable_falls_back(self):
        class _Unjsonable:
            pass

        assert _coerce_args(_Unjsonable()) == "{}"


def test_flush_pending_tool_calls_on_empty_returns_none():
    pending: list = []
    assert _flush_pending_tool_calls(pending) is None


def test_flush_pending_tool_calls_drains_and_clears():
    pending = [{"name": "read", "call_id": "c1", "args": '{"path":"x"}'}]
    out = _flush_pending_tool_calls(pending)
    assert out is not None
    etype, data = out
    assert etype == "assistant_tool_calls"
    assert data["tool_calls"][0]["function"]["name"] == "read"
    assert pending == []


def test_synth_events_from_message_unknown_role_dropped():
    assert _synth_events_from_message({"role": "weird", "content": "x"}) == []


def test_synth_events_from_message_assistant_with_tool_calls():
    out = _synth_events_from_message(
        {
            "role": "assistant",
            "content": "before",
            "tool_calls": [{"id": "c1"}],
        }
    )
    types = [e[0] for e in out]
    assert "text_chunk" in types
    assert "assistant_tool_calls" in types


def test_synth_events_from_message_assistant_empty_emits_placeholder():
    out = _synth_events_from_message({"role": "assistant", "content": ""})
    assert len(out) == 1
    assert out[0][0] == "text_chunk"
    assert out[0][1]["content"] == ""


def test_synth_events_from_message_tool_role():
    out = _synth_events_from_message(
        {"role": "tool", "content": "result", "name": "read", "tool_call_id": "c1"}
    )
    assert out[0][0] == "tool_result"
    assert out[0][1]["call_id"] == "c1"


def test_synth_events_from_snapshot_preserves_turn_indices():
    snap = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    triples = _synth_events_from_snapshot(snap)
    user_turns = [ti for etype, _, ti in triples if etype == "user_message"]
    assert user_turns == [1, 2]


def test_translate_v1_events_buffers_and_flushes_tool_calls():
    v1 = [
        {"type": "user_input", "content": "hi"},
        {"type": "tool_call", "call_id": "c1", "name": "read", "args": "{}"},
        {"type": "tool_call", "call_id": "c2", "name": "glob", "args": "{}"},
        {"type": "tool_result", "call_id": "c1", "name": "read", "output": "..."},
    ]
    triples = _translate_v1_events(v1)
    types = [t for t, _, _ in triples]
    assert types.count("assistant_tool_calls") == 1
    assert "tool_result" in types


def test_translate_v1_events_compact_complete_emits_replace_and_complete():
    v1 = [
        {"type": "user_input", "content": "hi"},
        {
            "type": "compact_complete",
            "summary": "S",
            "round": 1,
            "messages_compacted": 4,
        },
    ]
    triples = _translate_v1_events(v1)
    types = [t for t, _, _ in triples]
    assert "compact_replace" in types
    assert "compact_complete" in types


def test_translate_v1_events_passthrough_unknown_type():
    v1 = [{"type": "some_audit_event", "info": "x"}]
    triples = _translate_v1_events(v1)
    assert any(t == "some_audit_event" for t, _, _ in triples)


def test_backfill_assistant_tool_call_content_inherits_text():
    triples = [
        ("text_chunk", {"content": "hello", "chunk_seq": 0}, 1),
        ("assistant_tool_calls", {"tool_calls": [], "content": ""}, 1),
    ]
    out = _backfill_assistant_tool_call_content(triples)
    tc = next(t for t in out if t[0] == "assistant_tool_calls")
    assert tc[1]["content"] == "hello"


def test_backfill_only_fills_empty_content():
    triples = [
        ("text_chunk", {"content": "hello", "chunk_seq": 0}, 1),
        ("assistant_tool_calls", {"tool_calls": [], "content": "preset"}, 1),
    ]
    out = _backfill_assistant_tool_call_content(triples)
    tc = next(t for t in out if t[0] == "assistant_tool_calls")
    assert tc[1]["content"] == "preset"


# ─── session/output: parent_branch_path stamping ──────────────────────


class _AgentLikeForOutput:
    def __init__(
        self, *, ti=1, bi=1, path: list[tuple[int, int]] | None = None
    ) -> None:
        self._turn_index = ti
        self._branch_id = bi
        self._parent_branch_path = list(path or [])


def test_session_output_stamps_parent_branch_path_on_text_chunks(tmp_path):
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    agent = _AgentLikeForOutput(ti=2, bi=1, path=[(1, 2)])
    out = SessionOutput("alice", store, agent)
    out._emit_text_chunk("hello")
    events = store.get_events("alice")
    assert events
    chunk = next(e for e in events if e.get("type") == "text_chunk")
    assert chunk["parent_branch_path"] == [[1, 2]]
    assert chunk["turn_index"] == 2
    assert chunk["branch_id"] == 1
    store.close(update_status=False)


def test_session_output_stamps_parent_branch_path_on_record(tmp_path):
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    agent = _AgentLikeForOutput(ti=3, bi=1, path=[(1, 1), (2, 2)])
    out = SessionOutput("alice", store, agent)
    out._record("processing_start", {})
    events = store.get_events("alice")
    ps = next(e for e in events if e.get("type") == "processing_start")
    assert ps["parent_branch_path"] == [[1, 1], [2, 2]]
    store.close(update_status=False)


# ─── session/resume._restore_turn_branch_state ───────────────────────


def test_restore_turn_branch_state_picks_leaf_and_parent_path(tmp_path):
    from kohakuterrarium.session.resume import _restore_turn_branch_state

    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    # Three turns: turn 1 has branches 1+2; turn 2 has branch 1;
    # turn 3 has branches 1+2+3.
    for ti, bi in [(1, 1), (1, 2), (2, 1), (3, 1), (3, 2), (3, 3)]:
        store.append_event(
            "alice", "user_message", {"content": f"u{ti}"}, turn_index=ti, branch_id=bi
        )
    agent = SimpleNamespace(_turn_index=0, _branch_id=0, _parent_branch_path=[])
    _restore_turn_branch_state(agent, store, "alice")
    # Latest leaf: turn 3, branch 3.
    assert agent._turn_index == 3
    assert agent._branch_id == 3
    # Parent path = latest branch of every prior turn.
    assert agent._parent_branch_path == [(1, 2), (2, 1)]
    store.close(update_status=False)


def test_restore_turn_branch_state_noop_when_no_events(tmp_path):
    from kohakuterrarium.session.resume import _restore_turn_branch_state

    store = SessionStore(str(tmp_path / "empty.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    agent = SimpleNamespace(_turn_index=0, _branch_id=0, _parent_branch_path=[])
    _restore_turn_branch_state(agent, store, "alice")
    assert agent._turn_index == 0
    assert agent._branch_id == 0
    store.close(update_status=False)


def test_restore_turn_branch_state_handles_store_error():
    from kohakuterrarium.session.resume import _restore_turn_branch_state

    class _BadStore:
        def get_events(self, name):
            raise RuntimeError("disk gone")

    agent = SimpleNamespace(_turn_index=0, _branch_id=0, _parent_branch_path=[])
    _restore_turn_branch_state(agent, _BadStore(), "alice")
    # Bad store → silent no-op, agent state unchanged.
    assert agent._turn_index == 0


# ─── /edit slash command ───────────────────────────────────────────────


def _edit_agent_with_session(tmp_path):
    """Build a fake agent + store with two messages so /edit can land."""
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    agent = _NoStoreAgent()
    agent.session_store = store
    agent.controller.conversation.append("user", "first")
    agent.controller.conversation.append("assistant", "reply")
    agent._turn_index = 1
    agent._branch_id = 1
    return agent, store


@pytest.mark.asyncio
async def test_edit_slash_parses_and_invokes_edit_and_rerun(tmp_path):
    from kohakuterrarium.builtins.user_commands.edit import EditCommand
    from kohakuterrarium.modules.user_command.base import UserCommandContext

    agent, store = _edit_agent_with_session(tmp_path)
    ctx = UserCommandContext(agent=agent)
    cmd = EditCommand()
    result = await cmd._execute("0 actually-this", ctx)
    assert result.error is None
    assert "0" in result.output
    assert agent._rerun_calls == ["actually-this"]
    store.close(update_status=False)


@pytest.mark.asyncio
async def test_edit_slash_negative_index_resolves(tmp_path):
    from kohakuterrarium.builtins.user_commands.edit import EditCommand
    from kohakuterrarium.modules.user_command.base import UserCommandContext

    agent, store = _edit_agent_with_session(tmp_path)
    ctx = UserCommandContext(agent=agent)
    cmd = EditCommand()
    # -2 → conversation has 2 msgs → resolves to idx 0.
    result = await cmd._execute("-2 hello", ctx)
    assert result.error is None
    store.close(update_status=False)


@pytest.mark.asyncio
async def test_edit_slash_out_of_range_errors(tmp_path):
    from kohakuterrarium.builtins.user_commands.edit import EditCommand
    from kohakuterrarium.modules.user_command.base import UserCommandContext

    agent, store = _edit_agent_with_session(tmp_path)
    ctx = UserCommandContext(agent=agent)
    cmd = EditCommand()
    result = await cmd._execute("99 nope", ctx)
    assert result.error is not None
    assert "out of range" in result.error
    store.close(update_status=False)


@pytest.mark.asyncio
async def test_edit_slash_malformed_args_errors(tmp_path):
    from kohakuterrarium.builtins.user_commands.edit import EditCommand
    from kohakuterrarium.modules.user_command.base import UserCommandContext

    agent, store = _edit_agent_with_session(tmp_path)
    ctx = UserCommandContext(agent=agent)
    cmd = EditCommand()
    result = await cmd._execute("not-an-int content", ctx)
    assert result.error is not None
    result = await cmd._execute("", ctx)
    assert result.error is not None
    store.close(update_status=False)


@pytest.mark.asyncio
async def test_edit_slash_no_agent_context_errors():
    from kohakuterrarium.builtins.user_commands.edit import EditCommand
    from kohakuterrarium.modules.user_command.base import UserCommandContext

    cmd = EditCommand()
    result = await cmd._execute("0 hi", UserCommandContext(agent=None))
    assert result.error is not None


# ─── /regen alias of regen ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_regen_slash_invokes_agent_regenerate(tmp_path):
    from kohakuterrarium.builtins.user_commands.regen import RegenCommand
    from kohakuterrarium.modules.user_command.base import UserCommandContext

    agent, store = _edit_agent_with_session(tmp_path)
    cmd = RegenCommand()
    result = await cmd._execute("", UserCommandContext(agent=agent))
    assert result.error is None
    assert agent._rerun_calls == [""]  # pure regen passes empty content
    store.close(update_status=False)


@pytest.mark.asyncio
async def test_regen_slash_no_agent_errors():
    from kohakuterrarium.builtins.user_commands.regen import RegenCommand
    from kohakuterrarium.modules.user_command.base import UserCommandContext

    cmd = RegenCommand()
    result = await cmd._execute("", UserCommandContext(agent=None))
    assert result.error is not None


# ─── /fork slash command ──────────────────────────────────────────────


def _fork_agent(tmp_path):
    store = SessionStore(str(tmp_path / "parent.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    # Need at least one event so fork has somewhere to fork from.
    store.append_event(
        "alice",
        "user_message",
        {"content": "hi"},
        turn_index=1,
        branch_id=1,
    )
    agent = SimpleNamespace(
        config=SimpleNamespace(name="alice"),
        session_store=store,
    )
    return agent, store


@pytest.mark.asyncio
async def test_fork_slash_creates_child_session(tmp_path):
    from kohakuterrarium.builtins.user_commands.fork import ForkCommand
    from kohakuterrarium.modules.user_command.base import UserCommandContext

    agent, store = _fork_agent(tmp_path)
    cmd = ForkCommand()
    result = await cmd._execute("--name myfork", UserCommandContext(agent=agent))
    assert result.error is None
    assert "myfork" in result.output
    store.close(update_status=False)


@pytest.mark.asyncio
async def test_fork_slash_no_session_errors():
    from kohakuterrarium.builtins.user_commands.fork import ForkCommand
    from kohakuterrarium.modules.user_command.base import UserCommandContext

    cmd = ForkCommand()
    result = await cmd._execute(
        "",
        UserCommandContext(
            agent=SimpleNamespace(session_store=None, config=SimpleNamespace(name="x"))
        ),
    )
    assert result.error is not None


@pytest.mark.asyncio
async def test_fork_slash_no_events_errors(tmp_path):
    from kohakuterrarium.builtins.user_commands.fork import ForkCommand
    from kohakuterrarium.modules.user_command.base import UserCommandContext

    store = SessionStore(str(tmp_path / "empty.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    agent = SimpleNamespace(config=SimpleNamespace(name="alice"), session_store=store)
    cmd = ForkCommand()
    result = await cmd._execute("", UserCommandContext(agent=agent))
    assert result.error is not None
    assert "No events" in result.error
    store.close(update_status=False)


# ─── session/output writeable surface ─────────────────────────────────


@pytest.mark.asyncio
async def test_session_output_write_emits_text_chunk(tmp_path):
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    agent = _AgentLikeForOutput(ti=1, bi=1)
    out = SessionOutput("alice", store, agent)
    await out.write("plain text")
    await out.write_stream("more")
    await out.write("")  # empty no-op
    chunks = [e for e in store.get_events("alice") if e.get("type") == "text_chunk"]
    contents = [c.get("content") for c in chunks]
    assert "plain text" in contents
    assert "more" in contents
    store.close(update_status=False)


@pytest.mark.asyncio
async def test_session_output_start_restores_token_totals(tmp_path):
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    store.state["alice:token_usage"] = {
        "total_input_tokens": 100,
        "total_output_tokens": 50,
        "total_cached_tokens": 25,
    }
    out = SessionOutput("alice", store, None)
    await out.start()
    assert out._total_input_tokens == 100
    assert out._total_output_tokens == 50
    assert out._total_cached_tokens == 25
    await out.stop()
    store.close(update_status=False)


def test_session_output_assistant_image_persisted(tmp_path):
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    agent = _AgentLikeForOutput(ti=1, bi=1)
    out = SessionOutput("alice", store, agent)
    out.on_assistant_image(
        "/artifacts/img.png",
        detail="high",
        source_type="dalle",
        source_name="img.png",
        revised_prompt="cat",
    )
    img_evt = next(
        e for e in store.get_events("alice") if e.get("type") == "assistant_image"
    )
    assert img_evt["url"] == "/artifacts/img.png"
    assert img_evt["detail"] == "high"
    assert img_evt["source_type"] == "dalle"
    assert img_evt["source_name"] == "img.png"
    assert img_evt["revised_prompt"] == "cat"
    store.close(update_status=False)


def test_session_output_on_activity_records(tmp_path):
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    agent = _AgentLikeForOutput(ti=1, bi=1)
    out = SessionOutput("alice", store, agent)
    # tool_start activity
    out.on_activity_with_metadata(
        "tool_start", "[bash] echo hi", {"job_id": "j1", "args": {"cmd": "echo hi"}}
    )
    out.on_activity_with_metadata(
        "tool_done", "[bash] OK", {"job_id": "j1", "result": "hi"}
    )
    types = [e.get("type") for e in store.get_events("alice")]
    assert "tool_call" in types
    assert "tool_result" in types
    store.close(update_status=False)


def test_session_output_capture_disabled_drops_activity(tmp_path):
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    agent = _AgentLikeForOutput(ti=1, bi=1)
    out = SessionOutput("alice", store, agent, capture_activity=False)
    out.on_activity("tool_start", "[bash] echo")
    out.on_activity_with_metadata("tool_done", "[bash] OK", {})
    # No tool events recorded when capture_activity=False
    types = [e.get("type") for e in store.get_events("alice")]
    assert "tool_call" not in types
    assert "tool_result" not in types
    store.close(update_status=False)


def test_session_output_no_agent_emits_no_path(tmp_path):
    store = SessionStore(str(tmp_path / "s.kohakutr.v2"))
    store.init_meta(
        session_id="s",
        config_type="agent",
        config_path="x",
        pwd=str(tmp_path),
        agents=["alice"],
    )
    out = SessionOutput("alice", store, None)
    out._record("processing_start", {})
    events = store.get_events("alice")
    ps = next(e for e in events if e.get("type") == "processing_start")
    # No agent → no parent_branch_path stamped.
    assert "parent_branch_path" not in ps
    store.close(update_status=False)
