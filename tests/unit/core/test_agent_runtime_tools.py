"""Unit tests for :mod:`kohakuterrarium.core.agent_runtime_tools`."""

import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from kohakuterrarium.core.agent_runtime_tools import (
    AgentRuntimeToolsMixin,
    _make_job_label,
)
from kohakuterrarium.core.backgroundify import BackgroundifyHandle
from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.parsing.events import (
    CommandResultEvent,
    SubAgentCallEvent,
    ToolCallEvent,
)

# ── stubs ────────────────────────────────────────────────────────


class _Router:
    def __init__(self):
        self.activity_calls: list[tuple] = []
        self.flush_calls = 0
        self.reset_calls = 0
        self.default_output = types.SimpleNamespace(reset=self._do_reset)

    def _do_reset(self):
        self.reset_calls += 1

    def notify_activity(self, kind, msg, metadata=None):
        self.activity_calls.append((kind, msg, metadata))

    def reset(self):
        self.reset_calls += 1

    async def flush(self):
        self.flush_calls += 1


class _Mgr:
    def __init__(self):
        self.spawn = AsyncMock(return_value=("agent_123", True))

    async def spawn_from_event(self, event):
        return await self.spawn(event)


def _make_mixin(*, running=True, processed=None, notify_meta=None):
    class _Agent(AgentRuntimeToolsMixin):
        pass

    a = _Agent()
    a._direct_job_meta = dict(notify_meta or {})
    a._bg_controller_notify = {}
    a.output_router = _Router()
    a.subagent_manager = _Mgr()
    a.config = types.SimpleNamespace(name="alice")
    a._turn_usage_accum = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
    }
    a._running = running
    processed_events = []

    async def proc(evt):
        processed_events.append(evt)

    a._process_event = proc
    a._processed = processed_events
    return a


# ── _make_job_label ──────────────────────────────────────────────


class TestMakeJobLabel:
    def test_with_id(self):
        tool, label = _make_job_label("bash_abcdef123456")
        assert tool == "bash"
        assert label == "bash[abcdef]"

    def test_no_underscore(self):
        tool, label = _make_job_label("bashonly")
        assert tool == "bashonly"
        assert label == "bashonly"


# ── _notify_command_result ───────────────────────────────────────


class TestNotifyCommandResult:
    def test_success(self):
        a = _make_mixin()
        evt = CommandResultEvent(command="read", content="OK", error=None)
        a._notify_command_result(evt)
        kind, msg, _ = a.output_router.activity_calls[0]
        assert kind == "command_done"
        assert "read" in msg

    def test_error(self):
        a = _make_mixin()
        evt = CommandResultEvent(command="read", content="", error="bad arg")
        a._notify_command_result(evt)
        kind, msg, _ = a.output_router.activity_calls[0]
        assert kind == "command_error"
        assert "bad arg" in msg


# ── _notify_tool_start ───────────────────────────────────────────


class TestNotifyToolStart:
    def test_direct_label(self):
        a = _make_mixin()
        evt = ToolCallEvent(name="bash", args={"command": "ls"}, raw="")
        a._notify_tool_start(evt, "bash_abc123", is_direct=True)
        kind, msg, meta = a.output_router.activity_calls[0]
        assert kind == "tool_start"
        assert "bash[abc123]" in msg
        # No "(bg)" tag for direct tools.
        assert "(bg)" not in msg
        assert meta["background"] is False

    def test_background_label_and_underscore_filter(self):
        a = _make_mixin()
        evt = ToolCallEvent(
            name="bash",
            args={"command": "ls", "_hidden": "x"},
            raw="",
        )
        a._notify_tool_start(evt, "bash_xyz", is_direct=False)
        kind, msg, meta = a.output_router.activity_calls[0]
        assert "(bg)" in msg
        assert "_hidden" not in meta["args"]


# ── _emit_token_usage ────────────────────────────────────────────


class TestEmitTokenUsage:
    def test_no_usage_no_op(self):
        a = _make_mixin()
        ctrl = types.SimpleNamespace()
        a._emit_token_usage(ctrl)
        assert a.output_router.activity_calls == []

    def test_accumulates_into_turn_usage(self):
        a = _make_mixin()
        ctrl = types.SimpleNamespace(
            _last_usage={
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            }
        )
        a._emit_token_usage(ctrl)
        assert a.output_router.activity_calls[0][0] == "token_usage"
        assert a._turn_usage_accum["prompt_tokens"] == 10
        assert a._turn_usage_accum["completion_tokens"] == 5

    def test_emits_cache_stats(self):
        a = _make_mixin()
        ctrl = types.SimpleNamespace(
            _last_usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "cached_tokens": 30,
                "cache_creation_input_tokens": 10,
            }
        )
        a._emit_token_usage(ctrl)
        kinds = [c[0] for c in a.output_router.activity_calls]
        assert "cache_stats" in kinds
        cache_meta = next(
            c[2] for c in a.output_router.activity_calls if c[0] == "cache_stats"
        )
        assert cache_meta["cache_read"] == 30
        assert cache_meta["cache_write"] == 10
        assert cache_meta["cache_hit_ratio"] == pytest.approx(0.3)

    def test_cache_stats_skipped_when_no_cache(self):
        a = _make_mixin()
        ctrl = types.SimpleNamespace(
            _last_usage={"prompt_tokens": 1, "completion_tokens": 1}
        )
        a._emit_token_usage(ctrl)
        kinds = [c[0] for c in a.output_router.activity_calls]
        assert "cache_stats" not in kinds

    def test_no_accumulator_safe(self):
        a = _make_mixin()
        a._turn_usage_accum = None  # type: ignore[assignment]
        ctrl = types.SimpleNamespace(_last_usage={"prompt_tokens": 5})
        a._emit_token_usage(ctrl)
        # No crash; activity still emitted.
        assert a.output_router.activity_calls


# ── _cancel_handles ──────────────────────────────────────────────


class TestCancelHandles:
    def test_cancels_undone_unpromoted(self):
        a = _make_mixin()
        h1 = MagicMock(spec=BackgroundifyHandle)
        h1.promoted = False
        h1.done = False
        h2 = MagicMock(spec=BackgroundifyHandle)
        h2.promoted = True  # skip
        h2.done = False
        h3 = MagicMock(spec=BackgroundifyHandle)
        h3.promoted = False
        h3.done = True  # skip
        a._cancel_handles({"a": h1, "b": h2, "c": h3})
        h1.task.cancel.assert_called_once()
        h2.task.cancel.assert_not_called()
        h3.task.cancel.assert_not_called()


# ── reset / flush ────────────────────────────────────────────────


class TestResetFlush:
    def test_reset_output_state(self):
        a = _make_mixin()
        a._reset_output_state()
        assert a.output_router.reset_calls >= 1

    async def test_flush_output(self):
        a = _make_mixin()
        await a._flush_output()
        assert a.output_router.flush_calls == 1


# ── _start_subagent_async ────────────────────────────────────────


class TestStartSubagentAsync:
    async def test_spawn_succeeds(self):
        a = _make_mixin()
        evt = SubAgentCallEvent(name="explore", args={"task": "x"}, raw="")
        job_id, is_bg = await a._start_subagent_async(evt)
        assert job_id == "agent_123"
        assert is_bg is True

    async def test_spawn_raises_value_error(self):
        a = _make_mixin()
        a.subagent_manager.spawn.side_effect = ValueError("not registered")
        evt = SubAgentCallEvent(name="ghost", args={"task": "x"}, raw="")
        job_id, is_bg = await a._start_subagent_async(evt)
        assert job_id == "error_ghost"
        assert is_bg is True


# ── _should_notify_controller_on_background_complete ─────────────


class TestShouldNotify:
    def test_meta_override_true(self):
        a = _make_mixin(
            notify_meta={"j1": {"notify_controller_on_background_complete": True}}
        )
        assert a._should_notify_controller_on_background_complete("j1") is True

    def test_meta_override_false(self):
        a = _make_mixin(
            notify_meta={"j1": {"notify_controller_on_background_complete": False}}
        )
        assert a._should_notify_controller_on_background_complete("j1") is False

    def test_default_true(self):
        a = _make_mixin()
        # Unknown job_id → default True.
        assert a._should_notify_controller_on_background_complete("j2") is True

    def test_bg_notify_override(self):
        a = _make_mixin()
        a._bg_controller_notify["j3"] = False
        assert a._should_notify_controller_on_background_complete("j3") is False


# ── _on_bg_complete ──────────────────────────────────────────────


class TestOnBgComplete:
    async def test_not_running_skips(self):
        a = _make_mixin(running=False)
        evt = TriggerEvent(
            type=EventType.TOOL_COMPLETE,
            job_id="bash_abc",
            content="ok",
            context={},
        )
        a._on_bg_complete(evt)
        # Nothing happened.
        assert a.output_router.activity_calls == []

    async def test_tool_done(self):
        a = _make_mixin()
        evt = TriggerEvent(
            type=EventType.TOOL_COMPLETE,
            job_id="bash_abc",
            content="result text",
            context={},
        )
        a._on_bg_complete(evt)
        kinds = [c[0] for c in a.output_router.activity_calls]
        assert "tool_done" in kinds
        # No reschedule when notify is on.
        await asyncio.sleep(0.01)
        # _process_event invoked.
        assert a._processed

    async def test_subagent_done(self):
        a = _make_mixin()
        evt = TriggerEvent(
            type=EventType.TOOL_COMPLETE,
            job_id="agent_x_y",
            content="result",
            context={
                "subagent_metadata": {
                    "tools_used": ["bash", "read"],
                    "turns": 3,
                    "duration": 1.5,
                    "total_tokens": 100,
                }
            },
        )
        a._on_bg_complete(evt)
        kinds = [c[0] for c in a.output_router.activity_calls]
        assert "subagent_done" in kinds
        meta = next(
            c[2] for c in a.output_router.activity_calls if c[0] == "subagent_done"
        )
        assert meta["turns"] == 3

    async def test_error_path(self):
        a = _make_mixin()
        evt = TriggerEvent(
            type=EventType.TOOL_COMPLETE,
            job_id="bash_x",
            content="",
            context={"error": "boom"},
        )
        a._on_bg_complete(evt)
        kinds = [c[0] for c in a.output_router.activity_calls]
        assert "tool_error" in kinds

    async def test_interrupted_flag(self):
        a = _make_mixin()
        evt = TriggerEvent(
            type=EventType.TOOL_COMPLETE,
            job_id="bash_x",
            content="",
            context={"error": "x", "interrupted": True},
        )
        a._on_bg_complete(evt)
        meta = next(
            c[2] for c in a.output_router.activity_calls if c[0] == "tool_error"
        )
        assert meta["final_state"] == "interrupted"

    async def test_cancelled_flag(self):
        a = _make_mixin()
        evt = TriggerEvent(
            type=EventType.TOOL_COMPLETE,
            job_id="bash_x",
            content="",
            context={"error": "x", "cancelled": True},
        )
        a._on_bg_complete(evt)
        meta = next(
            c[2] for c in a.output_router.activity_calls if c[0] == "tool_error"
        )
        assert meta["final_state"] == "cancelled"

    async def test_subagent_error(self):
        a = _make_mixin()
        evt = TriggerEvent(
            type=EventType.TOOL_COMPLETE,
            job_id="agent_x",
            content="",
            context={"error": "fail"},
        )
        a._on_bg_complete(evt)
        kinds = [c[0] for c in a.output_router.activity_calls]
        assert "subagent_error" in kinds

    async def test_notify_off_pops_state(self):
        a = _make_mixin()
        a._bg_controller_notify["bash_x"] = False
        evt = TriggerEvent(
            type=EventType.TOOL_COMPLETE,
            job_id="bash_x",
            content="r",
            context={},
        )
        a._on_bg_complete(evt)
        # State cleared.
        assert "bash_x" not in a._bg_controller_notify
        # _process_event NOT invoked.
        await asyncio.sleep(0.01)
        assert not a._processed
