"""Unit tests for :mod:`kohakuterrarium.session.output`."""

import json


from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.session.output import (
    SessionOutput,
    _parse_detail,
    _subagent_name,
    _token_metadata,
)
from kohakuterrarium.session.store import SessionStore

# ── fakes ─────────────────────────────────────────────────────────


class _FakeScratchpad:
    def to_dict(self):
        return {"k": "v"}


class _FakeSession:
    def __init__(self):
        self.scratchpad = _FakeScratchpad()


class _FakeConversation:
    def to_messages(self):
        return [{"role": "user", "content": "from controller"}]


class _FakeController:
    def __init__(self, last_usage=None):
        self.conversation = _FakeConversation()
        self._last_usage = last_usage or {}


class _FakeAgent:
    def __init__(
        self,
        *,
        controller=True,
        session=True,
        turn=2,
        branch=1,
        path=None,
        last_usage=None,
    ):
        self.controller = _FakeController(last_usage=last_usage) if controller else None
        self.session = _FakeSession() if session else None
        self._turn_index = turn
        self._branch_id = branch
        self._parent_branch_path = path or []


def _make(tmp_path, agent=None, *, capture_activity=True, prefix=None) -> tuple:
    store = SessionStore(str(tmp_path / "x.kohakutr"))
    out = SessionOutput(
        "alice",
        store,
        agent,
        capture_activity=capture_activity,
        event_key_prefix=prefix,
    )
    return store, out


# ── module-level helpers ─────────────────────────────────────────


class TestParseDetail:
    def test_bracket_prefix(self):
        assert _parse_detail("[tool] running") == ("tool", "running")

    def test_no_brackets(self):
        assert _parse_detail("plain text") == ("unknown", "plain text")

    def test_bare_brackets(self):
        # The ``[name]`` with no trailing content falls to the second
        # branch which extracts the bare label with empty body.
        assert _parse_detail("[bare]") == ("bare", "")

    def test_nested_brackets(self):
        # Splits on the FIRST "] " — the inner ``[id]`` stays intact.
        assert _parse_detail("[name[id]] body") == ("name[id]", "body")


class TestSubagentName:
    def test_explicit(self):
        assert _subagent_name("fallback", {"subagent": "x"}) == "x"

    def test_via_subagent_name(self):
        assert _subagent_name("fallback", {"subagent_name": "y"}) == "y"

    def test_job_id_extraction(self):
        # ``agent_<name>_<seq>`` → name.
        assert _subagent_name("agent", {"job_id": "agent_critic_0"}) == "critic"

    def test_fallback_kept(self):
        assert _subagent_name("default", {}) == "default"


class TestTokenMetadata:
    def test_basic(self):
        out = _token_metadata(
            {"prompt_tokens": 5, "completion_tokens": 3, "cached_tokens": 1}
        )
        assert out == {
            "total_tokens": 8,
            "prompt_tokens": 5,
            "completion_tokens": 3,
            "cached_tokens": 1,
        }

    def test_alias_keys(self):
        out = _token_metadata({"tokens_in": 4, "tokens_out": 2, "tokens_cached": 1})
        assert out["prompt_tokens"] == 4
        assert out["completion_tokens"] == 2
        assert out["cached_tokens"] == 1

    def test_explicit_total(self):
        out = _token_metadata(
            {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 99}
        )
        assert out["total_tokens"] == 99

    def test_cost_passthrough(self):
        out = _token_metadata({"cost_usd": 0.05})
        assert out["cost_usd"] == 0.05


# ── SessionOutput state-machine ──────────────────────────────────


class TestSessionOutputBasic:
    def test_construction(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            assert out._agent_name == "alice"
            assert out._event_key_prefix == "alice"
            assert out._capture_activity is True
            assert out._chunk_seq == 0
        finally:
            store.close()

    def test_custom_event_key_prefix(self, tmp_path):
        store, out = _make(tmp_path, prefix="host:attached:rev:0")
        try:
            assert out._event_key_prefix == "host:attached:rev:0"
        finally:
            store.close()


class TestCurrentTurnBranch:
    def test_no_agent_returns_none(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            assert out._current_turn_branch() == (None, None)
            assert out._current_parent_path() is None
        finally:
            store.close()

    def test_with_agent_returns_pair(self, tmp_path):
        agent = _FakeAgent(turn=5, branch=2, path=[(0, 1), (1, 1)])
        store, out = _make(tmp_path, agent=agent)
        try:
            assert out._current_turn_branch() == (5, 2)
            assert out._current_parent_path() == [(0, 1), (1, 1)]
        finally:
            store.close()

    def test_zero_values_treated_as_unset(self, tmp_path):
        agent = _FakeAgent(turn=0, branch=0)
        store, out = _make(tmp_path, agent=agent)
        try:
            assert out._current_turn_branch() == (None, None)
        finally:
            store.close()


# ── streaming chunks ──────────────────────────────────────────────


class TestStreaming:
    async def test_write_emits_text_chunk(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.write("hello")
            store.flush()
            evts = store.get_events("alice")
            assert len(evts) == 1
            assert evts[0]["type"] == "text_chunk"
            assert evts[0]["content"] == "hello"
            assert evts[0]["chunk_seq"] == 0
        finally:
            store.close()

    async def test_write_empty_dropped(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.write("")
            store.flush()
            assert store.get_events("alice") == []
        finally:
            store.close()

    async def test_write_stream_increments_seq(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.write_stream("a")
            await out.write_stream("b")
            store.flush()
            evts = store.get_events("alice")
            assert [e["chunk_seq"] for e in evts] == [0, 1]
        finally:
            store.close()

    async def test_processing_start_resets_chunk_seq(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.write_stream("a")
            await out.on_processing_start()
            await out.write_stream("b")
            store.flush()
            evts = [e for e in store.get_events("alice") if e["type"] == "text_chunk"]
            assert [e["chunk_seq"] for e in evts] == [0, 0]
        finally:
            store.close()


# ── processing lifecycle ─────────────────────────────────────────


class TestProcessingLifecycle:
    async def test_start_event(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.on_processing_start()
            store.flush()
            evts = store.get_events("alice")
            assert any(e["type"] == "processing_start" for e in evts)
        finally:
            store.close()

    async def test_end_saves_snapshot_using_controller(self, tmp_path):
        agent = _FakeAgent()
        store, out = _make(tmp_path, agent=agent)
        try:
            await out.on_processing_end()
            # Snapshot from controller conversation.
            snap = store.load_conversation("alice")
            assert snap == [{"role": "user", "content": "from controller"}]
        finally:
            store.close()

    async def test_end_saves_token_usage_state(self, tmp_path):
        agent = _FakeAgent(last_usage={"prompt_tokens": 7, "completion_tokens": 3})
        store, out = _make(tmp_path, agent=agent)
        try:
            await out.on_processing_end()
            usage = store.state.get("alice:token_usage")
            assert usage["prompt_tokens"] == 7
        finally:
            store.close()

    async def test_end_no_agent_falls_back_to_replay(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            # Append a stream so replay has something to rebuild.
            await out.write_stream("hello")
            await out.on_processing_end()
            snap = store.load_conversation("alice")
            assert snap == [{"role": "assistant", "content": "hello"}]
        finally:
            store.close()


# ── start() restores token totals ─────────────────────────────────


class TestStart:
    async def test_restores_totals(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            store.state["alice:token_usage"] = {
                "total_input_tokens": 100,
                "total_output_tokens": 50,
                "total_cached_tokens": 5,
            }
            out = SessionOutput("alice", store, None)
            await out.start()
            assert out._total_input_tokens == 100
            assert out._total_output_tokens == 50
            assert out._total_cached_tokens == 5
        finally:
            store.close()

    async def test_no_state_no_crash(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.start()
            assert out._total_input_tokens == 0
        finally:
            store.close()

    async def test_stop_is_noop(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.stop()
            # A no-op: writes no events to the store.
            store.flush()
            assert store.get_events("alice") == []
        finally:
            store.close()

    async def test_flush_is_noop(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.flush()
            # A no-op: writes no events to the store.
            store.flush()
            assert store.get_events("alice") == []
        finally:
            store.close()


# ── activity handlers ─────────────────────────────────────────────


class TestActivityHandlers:
    def test_capture_disabled_returns_early(self, tmp_path):
        store, out = _make(tmp_path, capture_activity=False)
        try:
            out.on_activity("tool_start", "[bash] cmd")
            store.flush()
            assert store.get_events("alice") == []
        finally:
            store.close()

    def test_tool_start(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "tool_start", "[bash] running", {"job_id": "j1", "args": {"x": 1}}
            )
            store.flush()
            evts = [e for e in store.get_events("alice") if e["type"] == "tool_call"]
            assert len(evts) == 1
            assert evts[0]["name"] == "bash"
            assert evts[0]["call_id"] == "j1"
            assert evts[0]["args"] == {"x": 1}
        finally:
            store.close()

    def test_tool_done(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "tool_done", "[bash] done", {"job_id": "j1", "result": "ok"}
            )
            store.flush()
            evts = [e for e in store.get_events("alice") if e["type"] == "tool_result"]
            assert len(evts) == 1
            assert evts[0]["exit_code"] == 0
            assert evts[0]["output"] == "ok"
        finally:
            store.close()

    def test_tool_error(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "tool_error",
                "[bash] failed",
                {"job_id": "j1", "error": "boom", "interrupted": True},
            )
            store.flush()
            evt = next(
                e for e in store.get_events("alice") if e["type"] == "tool_result"
            )
            assert evt["exit_code"] == 1
            assert evt["interrupted"] is True
            assert evt["error"] == "boom"
        finally:
            store.close()

    def test_subagent_start_then_done(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "subagent_start",
                "[explore] task",
                {"job_id": "j1", "task": "find x"},
            )
            out.on_activity_with_metadata(
                "subagent_done",
                "[explore] done",
                {"job_id": "j1", "result": "found", "turns": 1, "duration": 0.5},
            )
            store.flush()
            evts = store.get_events("alice")
            assert any(e["type"] == "subagent_call" for e in evts)
            assert any(e["type"] == "subagent_result" for e in evts)
            # SubAgent conversation persisted.
            convo = store.load_subagent_conversation("alice", "explore", 0)
            assert convo is not None
            parsed = json.loads(convo)
            assert parsed[0]["role"] == "user"
            assert parsed[1]["content"] == "found"
        finally:
            store.close()

    def test_subagent_error(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "subagent_start",
                "[critic] task",
                {"job_id": "j2", "task": "review"},
            )
            out.on_activity_with_metadata(
                "subagent_error",
                "[critic] failed",
                {"job_id": "j2", "error": "boom", "result": "nope"},
            )
            store.flush()
            evt = next(
                e for e in store.get_events("alice") if e["type"] == "subagent_result"
            )
            assert evt["success"] is False
            assert evt["error"] == "boom"
        finally:
            store.close()

    def test_subagent_token_update(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "subagent_token_update",
                "[plan] tokens",
                {
                    "job_id": "j1",
                    "subagent": "plan",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                },
            )
            store.flush()
            evt = next(
                e
                for e in store.get_events("alice")
                if e["type"] == "subagent_token_usage"
            )
            assert evt["prompt_tokens"] == 10
        finally:
            store.close()

    def test_subagent_tool_dispatch(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "subagent_tool_call",
                "[plan] using",
                {"subagent": "plan", "tool": "bash"},
            )
            store.flush()
            evt = next(
                e for e in store.get_events("alice") if e["type"] == "subagent_tool"
            )
            assert evt["activity"] == "tool_call"
        finally:
            store.close()

    def test_token_usage_accumulates(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "token_usage",
                "[llm]",
                {"prompt_tokens": 5, "completion_tokens": 3, "cached_tokens": 1},
            )
            out.on_activity_with_metadata(
                "token_usage",
                "[llm]",
                {"prompt_tokens": 2, "completion_tokens": 1},
            )
            assert out._total_input_tokens == 7
            assert out._total_output_tokens == 4
            assert out._total_cached_tokens == 1
            store.flush()
            evts = [e for e in store.get_events("alice") if e["type"] == "token_usage"]
            assert len(evts) == 2
        finally:
            store.close()

    def test_compact_events(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata("compact_start", "", {"round": 1})
            out.on_activity_with_metadata(
                "compact_complete",
                "",
                {"round": 1, "summary": "s", "messages_compacted": 5},
            )
            store.flush()
            types = [e["type"] for e in store.get_events("alice")]
            assert "compact_start" in types
            assert "compact_complete" in types
        finally:
            store.close()

    def test_trigger_fired(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "trigger_fired",
                "[trig]",
                {"trigger_id": "t1", "channel": "ch", "sender": "s"},
            )
            store.flush()
            evt = next(
                e for e in store.get_events("alice") if e["type"] == "trigger_fired"
            )
            assert evt["trigger_id"] == "t1"
        finally:
            store.close()

    def test_unknown_activity_recorded_as_prefixed(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata("what_is_this", "[name] x", {"k": 1})
            store.flush()
            types = [e["type"] for e in store.get_events("alice")]
            # Falls through to ``activity:<type>``.
            assert "activity:what_is_this" in types
        finally:
            store.close()

    def test_context_cleared(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "context_cleared", "", {"messages_cleared": 10}
            )
            store.flush()
            evt = next(
                e for e in store.get_events("alice") if e["type"] == "context_cleared"
            )
            assert evt["messages_cleared"] == 10
        finally:
            store.close()

    def test_processing_error(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "processing_error", "boom", {"error_type": "RuntimeError"}
            )
            store.flush()
            evt = next(
                e for e in store.get_events("alice") if e["type"] == "processing_error"
            )
            assert evt["error_type"] == "RuntimeError"
        finally:
            store.close()

    def test_processing_complete(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "processing_complete",
                "",
                {"trigger_channel": "c", "output_preview": "p"},
            )
            store.flush()
            evt = next(
                e
                for e in store.get_events("alice")
                if e["type"] == "processing_complete"
            )
            assert evt["trigger_channel"] == "c"
        finally:
            store.close()


class TestWaveBHandlers:
    def test_tool_wait(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "tool_wait", "", {"tool": "bash", "wait_ms": 50}
            )
            store.flush()
            evt = next(e for e in store.get_events("alice") if e["type"] == "tool_wait")
            assert evt["wait_ms"] == 50
        finally:
            store.close()

    def test_compact_decision(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "compact_decision",
                "",
                {"reason": "threshold", "skipped": True},
            )
            store.flush()
            evt = next(
                e for e in store.get_events("alice") if e["type"] == "compact_decision"
            )
            assert evt["skipped"] is True
        finally:
            store.close()

    def test_turn_token_usage_saves_rollup(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "turn_token_usage",
                "",
                {
                    "turn_index": 1,
                    "prompt_tokens": 5,
                    "completion_tokens": 6,
                    "cached_tokens": 1,
                    "total_tokens": 11,
                },
            )
            store.flush()
            evts = [
                e for e in store.get_events("alice") if e["type"] == "turn_token_usage"
            ]
            assert len(evts) == 1
            rollup = store.get_turn_rollup("alice", 1)
            assert rollup["tokens_in"] == 5
            assert rollup["tokens_out"] == 6
        finally:
            store.close()

    def test_plugin_hook_timing(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "plugin_hook_timing",
                "[name]",
                {"hook": "pre_tool_execute", "duration_ms": 12},
            )
            store.flush()
            evt = next(
                e
                for e in store.get_events("alice")
                if e["type"] == "plugin_hook_timing"
            )
            assert evt["duration_ms"] == 12
        finally:
            store.close()

    def test_cache_stats(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "cache_stats", "", {"cache_write": 100, "cache_read": 50}
            )
            store.flush()
            evt = next(
                e for e in store.get_events("alice") if e["type"] == "cache_stats"
            )
            assert evt["cache_write"] == 100
        finally:
            store.close()

    def test_scratchpad_write(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_activity_with_metadata(
                "scratchpad_write",
                "[key1]",
                {"key": "k", "action": "set", "size_bytes": 8},
            )
            store.flush()
            evt = next(
                e for e in store.get_events("alice") if e["type"] == "scratchpad_write"
            )
            assert evt["action"] == "set"
        finally:
            store.close()


# ── assistant_image ───────────────────────────────────────────────


class TestAssistantImage:
    def test_basic(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_assistant_image("url-1")
            store.flush()
            evt = next(
                e for e in store.get_events("alice") if e["type"] == "assistant_image"
            )
            assert evt["url"] == "url-1"
            assert evt["detail"] == "auto"
        finally:
            store.close()

    def test_with_optional_fields(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            out.on_assistant_image(
                "url",
                detail="high",
                source_type="gen",
                source_name="dall-e",
                revised_prompt="r",
            )
            store.flush()
            evt = next(
                e for e in store.get_events("alice") if e["type"] == "assistant_image"
            )
            assert evt["source_type"] == "gen"
            assert evt["source_name"] == "dall-e"
            assert evt["revised_prompt"] == "r"
        finally:
            store.close()


# ── emit() native event consumer ─────────────────────────────────


class TestEmitMatch:
    async def test_text_event(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.emit(OutputEvent(type="text", content="hi"))
            store.flush()
            evts = [e for e in store.get_events("alice") if e["type"] == "text_chunk"]
            assert evts[0]["content"] == "hi"
        finally:
            store.close()

    async def test_processing_start_end(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.emit(OutputEvent(type="processing_start", content=""))
            await out.emit(OutputEvent(type="processing_end", content=""))
            store.flush()
            types = [e["type"] for e in store.get_events("alice")]
            assert "processing_start" in types
            assert "processing_end" in types
        finally:
            store.close()

    async def test_user_input_skipped(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.emit(OutputEvent(type="user_input", content="ignored"))
            store.flush()
            assert store.get_events("alice") == []
        finally:
            store.close()

    async def test_resume_batch_skipped(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.emit(OutputEvent(type="resume_batch", content=""))
            store.flush()
            assert store.get_events("alice") == []
        finally:
            store.close()

    async def test_assistant_image_event(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.emit(
                OutputEvent(
                    type="assistant_image",
                    content="",
                    payload={"url": "u"},
                )
            )
            store.flush()
            evts = [
                e for e in store.get_events("alice") if e["type"] == "assistant_image"
            ]
            assert evts[0]["url"] == "u"
        finally:
            store.close()

    async def test_fallback_to_activity(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            await out.emit(
                OutputEvent(
                    type="tool_start",
                    content="[bash] x",
                    payload={"job_id": "j1"},
                )
            )
            store.flush()
            evts = [e for e in store.get_events("alice") if e["type"] == "tool_call"]
            assert len(evts) == 1
        finally:
            store.close()

    async def test_capture_disabled_skips_activity(self, tmp_path):
        store, out = _make(tmp_path, capture_activity=False)
        try:
            await out.emit(OutputEvent(type="tool_start", content="[bash]", payload={}))
            store.flush()
            assert store.get_events("alice") == []
        finally:
            store.close()


# -- _current_parent_path defensive shapes ------------------------


class TestCurrentParentPath:
    def test_no_agent_returns_none(self, tmp_path):
        store, out = _make(tmp_path, agent=None)
        try:
            assert out._current_parent_path() is None
        finally:
            store.close()

    def test_non_list_branch_path_returns_none(self, tmp_path):
        # An agent whose _parent_branch_path is not a list (corrupt /
        # uninitialised) yields None rather than crashing.
        agent = _FakeAgent()
        agent._parent_branch_path = "not-a-list"
        store, out = _make(tmp_path, agent=agent)
        try:
            assert out._current_parent_path() is None
        finally:
            store.close()

    def test_list_branch_path_returned_as_tuples(self, tmp_path):
        agent = _FakeAgent(path=[[1, 1], [2, 1]])
        store, out = _make(tmp_path, agent=agent)
        try:
            assert out._current_parent_path() == [(1, 1), (2, 1)]
        finally:
            store.close()


# -- start() token-usage restore defensive path -------------------


class TestStartTokenRestore:
    async def test_restores_cumulative_totals(self, tmp_path):
        store, out = _make(tmp_path)
        try:
            store.state["alice:token_usage"] = {
                "total_input_tokens": 100,
                "total_output_tokens": 40,
                "total_cached_tokens": 10,
            }
            store.flush()
            await out.start()
            assert out._total_input_tokens == 100
            assert out._total_output_tokens == 40
            assert out._total_cached_tokens == 10
        finally:
            store.close()

    async def test_corrupt_state_get_swallowed(self, tmp_path, monkeypatch):
        # If state.get raises (TypeError / KeyError), start() swallows it
        # and leaves the counters at their zero defaults.
        store, out = _make(tmp_path)
        try:

            def _boom(key, default=None):
                raise TypeError("state backend down")

            monkeypatch.setattr(store.state, "get", _boom)
            await out.start()
            assert out._total_input_tokens == 0
        finally:
            store.close()


# -- _token_metadata defensive total coercion ---------------------


class TestTokenMetadataDefensive:
    def test_unparseable_prompt_falls_back_to_zero_total(self):
        # A prompt value that can't be int()-ed and no explicit total →
        # total defensively coerces to 0 rather than raising.
        out = _token_metadata({"prompt_tokens": ["bad"], "completion_tokens": 2})
        assert out["total_tokens"] == 0


# -- _record defensive append failure -----------------------------


class TestRecordDefensive:
    def test_append_event_failure_is_swallowed(self, tmp_path):
        # If the underlying store.append_event raises, _record logs and
        # swallows it — the SessionOutput keeps working.
        store, out = _make(tmp_path)
        try:

            def _boom(*a, **kw):
                raise RuntimeError("append exploded")

            store.append_event = _boom
            # Must not raise.
            out._record("some_event", {"k": "v"})
        finally:
            store.close()


# -- on_activity / on_activity_with_metadata gating ---------------


class TestActivityMethods:
    def test_on_activity_records_when_capture_enabled(self, tmp_path):
        store, out = _make(tmp_path, capture_activity=True)
        try:
            out.on_activity("custom_step", "[stepname] did a thing")
            store.flush()
            events = store.get_events("alice")
            # An unknown activity type is recorded under ``activity:<type>``.
            assert any(e.get("type") == "activity:custom_step" for e in events)
        finally:
            store.close()

    def test_on_activity_skipped_when_capture_disabled(self, tmp_path):
        store, out = _make(tmp_path, capture_activity=False)
        try:
            out.on_activity("custom_step", "[stepname] ignored")
            store.flush()
            # Capture off → nothing recorded.
            assert store.get_events("alice") == []
        finally:
            store.close()

    def test_on_activity_with_metadata_records_when_enabled(self, tmp_path):
        store, out = _make(tmp_path, capture_activity=True)
        try:
            out.on_activity_with_metadata(
                "custom_step", "[stepname] meta thing", {"weight": 5}
            )
            store.flush()
            events = store.get_events("alice")
            assert any(e.get("type") == "activity:custom_step" for e in events)
        finally:
            store.close()

    def test_on_activity_with_metadata_skipped_when_disabled(self, tmp_path):
        store, out = _make(tmp_path, capture_activity=False)
        try:
            out.on_activity_with_metadata("custom_step", "[x] ignored", {"weight": 5})
            store.flush()
            assert store.get_events("alice") == []
        finally:
            store.close()


# -- defensive store-failure branches -----------------------------


class TestStoreFailureBranches:
    async def test_write_stream_record_failure_swallowed(self, tmp_path):
        # If append_event raises while recording a text chunk,
        # write_stream logs + swallows it (output keeps streaming).
        store, out = _make(tmp_path)
        try:

            def _boom(*a, **kw):
                raise RuntimeError("append exploded")

            store.append_event = _boom
            # Must not raise.
            await out.write_stream("a chunk")
        finally:
            store.close()

    async def test_processing_end_snapshot_id_failure_swallowed(self, tmp_path):
        # If writing snapshot_event_id raises, on_processing_end logs +
        # continues to the state-save block rather than aborting.
        agent = _FakeAgent()
        store, out = _make(tmp_path, agent=agent)
        try:
            original_setitem = type(store.state).__setitem__

            def _flaky_setitem(self_state, key, value):
                if key.endswith(":snapshot_event_id"):
                    raise RuntimeError("state write exploded")
                return original_setitem(self_state, key, value)

            import kohakuterrarium.session.output as out_mod  # noqa: F401

            # Patch the bound state object's class method via the instance.
            store.state.__class__.__setitem__ = _flaky_setitem
            try:
                await out.on_processing_end()
            finally:
                store.state.__class__.__setitem__ = original_setitem
            # State save still ran (scratchpad persisted despite the
            # snapshot-id failure).
            assert store.load_scratchpad("alice") == {"k": "v"}
        finally:
            store.close()

    async def test_processing_end_state_save_failure_swallowed(self, tmp_path):
        agent = _FakeAgent()
        store, out = _make(tmp_path, agent=agent)
        try:

            def _boom(*a, **kw):
                raise RuntimeError("save_state exploded")

            store.save_state = _boom
            # Must not raise even though the state save fails.
            await out.on_processing_end()
        finally:
            store.close()

    async def test_processing_end_flush_failure_swallowed(self, tmp_path):
        agent = _FakeAgent()
        store, out = _make(tmp_path, agent=agent)
        try:

            def _boom():
                raise RuntimeError("flush exploded")

            store.flush = _boom
            # Must not raise even though the end-of-turn flush fails.
            await out.on_processing_end()
        finally:
            store._closed = True
