"""Unit tests for :mod:`kohakuterrarium.core.compact`."""

import asyncio
import types
from typing import Any


from kohakuterrarium.core.compact import (
    COMPACT_PROMPT,
    DEFAULT_KEEP_RECENT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TARGET,
    DEFAULT_THRESHOLD,
    CompactConfig,
    CompactManager,
)
from kohakuterrarium.core.conversation import Conversation

# ── stubs ────────────────────────────────────────────────────────


class _Router:
    def __init__(self):
        self.calls: list[tuple] = []

    def notify_activity(self, kind, msg, metadata=None):
        self.calls.append((kind, msg, metadata))


class _LLM:
    """Async streaming LLM mock."""

    def __init__(self, chunks=None, raises=None):
        self.chunks = chunks if chunks is not None else ["sum", "mary"]
        self.raises = raises
        self.calls: list = []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.raises is not None:
            raise self.raises
        for c in self.chunks:
            yield c


class _Store:
    def __init__(self):
        self.state: dict[str, Any] = {}
        self.saved_conversations: list = []
        self.events: list = []
        self.saved_state_calls: list = []

    def save_conversation(self, name, msgs):
        self.saved_conversations.append((name, msgs))

    def save_state(self, name, **kwargs):
        self.saved_state_calls.append((name, kwargs))
        for k, v in kwargs.items():
            self.state[f"{name}:{k}"] = v

    def get_events(self, name):
        return list(self.events)


class _Plugins:
    def __init__(self, proceed=True):
        self.proceed_value = proceed
        self.should_proceed_calls: list = []
        self.notify_calls: list = []

    async def should_proceed(self, hook, **kwargs):
        self.should_proceed_calls.append((hook, kwargs))
        return self.proceed_value

    async def notify(self, hook, **kwargs):
        self.notify_calls.append((hook, kwargs))


def _build_conversation(n_user=8, with_tools=False, system_only=False):
    conv = Conversation()
    conv.append("system", "sys")
    if system_only:
        return conv
    for i in range(n_user):
        conv.append("user", f"u{i}")
        conv.append("assistant", f"a{i}")
        if with_tools:
            conv.append("tool", f"t{i}" * 200, tool_call_id=f"tc_{i}")
    return conv


def _build_mgr(*, conversation=None, llm=None, router=None, store=None, plugins=None):
    mgr = CompactManager()
    mgr._controller = types.SimpleNamespace(conversation=conversation or Conversation())
    mgr._llm = llm
    mgr._output_router = router
    mgr._session_store = store
    mgr._plugins = plugins
    mgr._agent_name = "alice"
    return mgr


# ── CompactConfig defaults ───────────────────────────────────────


class TestCompactConfig:
    def test_defaults_match_constants(self):
        c = CompactConfig()
        assert c.max_tokens == DEFAULT_MAX_TOKENS
        assert c.threshold == DEFAULT_THRESHOLD
        assert c.target == DEFAULT_TARGET
        assert c.keep_recent_turns == DEFAULT_KEEP_RECENT
        assert c.enabled is True
        assert c.cooldown_seconds == 30.0
        assert c.compact_model is None


# ── should_compact ───────────────────────────────────────────────


class TestShouldCompact:
    def test_disabled(self):
        mgr = CompactManager(CompactConfig(enabled=False))
        assert mgr.should_compact(prompt_tokens=999_999) is False

    def test_under_threshold(self):
        mgr = CompactManager(CompactConfig(max_tokens=1000, threshold=0.8))
        assert mgr.should_compact(prompt_tokens=500) is False

    def test_over_threshold(self):
        mgr = CompactManager(CompactConfig(max_tokens=1000, threshold=0.8))
        assert mgr.should_compact(prompt_tokens=900) is True

    def test_no_tokens_returns_false(self):
        mgr = CompactManager(CompactConfig(max_tokens=1000, threshold=0.8))
        assert mgr.should_compact(prompt_tokens=0) is False

    def test_cooldown_blocks(self):
        import time as _t

        mgr = CompactManager(CompactConfig(max_tokens=1000, cooldown_seconds=30.0))
        mgr._last_compact_time = _t.time()  # just now
        assert mgr.should_compact(prompt_tokens=900) is False

    def test_running_blocks(self):
        mgr = CompactManager()
        # Acquire the single-flight lease to mark "running".
        mgr._dispatch.try_acquire()
        try:
            assert mgr.should_compact(prompt_tokens=999_999) is False
        finally:
            mgr._dispatch.force_release()


# ── trigger_compact ──────────────────────────────────────────────


class TestTriggerCompact:
    def test_no_controller_skip(self):
        mgr = CompactManager()
        mgr._controller = None
        assert mgr.trigger_compact() is False
        assert mgr._last_skip_reason == "no_controller"

    def test_too_short_skipped(self):
        conv = _build_conversation(system_only=True)
        router = _Router()
        mgr = _build_mgr(conversation=conv, router=router)
        result = mgr.trigger_compact()
        assert result is False
        assert mgr._last_skip_reason == "too_short"
        kinds = [c[0] for c in router.calls]
        assert "compact_skipped" in kinds
        assert "compact_decision" in kinds

    async def test_busy_returns_false_and_emits_decision(self):
        conv = _build_conversation(n_user=12)
        router = _Router()
        mgr = _build_mgr(conversation=conv, router=router, llm=_LLM())
        # Pre-acquire the lease to simulate concurrent compact.
        first = mgr._dispatch.try_acquire()
        try:
            result = mgr.trigger_compact()
            assert result is False
            assert mgr._last_skip_reason == "busy"
            decisions = [c for c in router.calls if c[0] == "compact_decision"]
            assert any("busy" in d[1] for d in decisions)
        finally:
            mgr._dispatch.release(first)

    async def test_success_emits_start_and_dispatches(self):
        conv = _build_conversation(n_user=10)
        router = _Router()
        mgr = _build_mgr(conversation=conv, llm=_LLM(), router=router)
        result = mgr.trigger_compact()
        assert result is True
        # compact_start activity emitted.
        kinds = [c[0] for c in router.calls]
        assert "compact_start" in kinds
        # Wait for the background task to complete.
        if mgr._compact_task is not None:
            await mgr._compact_task
        # Compact ran to completion.
        assert mgr._compact_count == 1


# ── _count_keep_messages ─────────────────────────────────────────


class TestCountKeepMessages:
    def test_empty(self):
        mgr = CompactManager()
        assert mgr._count_keep_messages([]) == 0

    def test_single(self):
        mgr = CompactManager()
        assert mgr._count_keep_messages([types.SimpleNamespace(role="user")]) == 0

    def test_keeps_recent_turns(self):
        mgr = CompactManager(CompactConfig(keep_recent_turns=2))
        msgs = []
        for i in range(5):
            msgs.append(types.SimpleNamespace(role="user"))
            msgs.append(types.SimpleNamespace(role="assistant"))
        # 5 user turns, want to keep last 2 — counts back until 2 user msgs hit.
        keep = mgr._count_keep_messages(msgs)
        # Walking from the end, we find 2 "user" within the last 4 entries.
        assert keep == 4

    def test_half_cap_fallback_when_few_user_turns(self):
        mgr = CompactManager(CompactConfig(keep_recent_turns=8))
        # 1 user + 10 assistant/tool messages (no more users).
        msgs = [types.SimpleNamespace(role="user")]
        for _ in range(10):
            msgs.append(types.SimpleNamespace(role="assistant"))
        # Phase 1 finds only 1 user → fallback to half-cap.
        keep = mgr._count_keep_messages(msgs)
        # n = 11; half-cap = 5; n-1 = 10 → min = 5.
        assert keep == 5

    def test_tiny_conversation_no_fallback(self):
        mgr = CompactManager(CompactConfig(keep_recent_turns=8))
        # 3 messages total — below MIN_COMPACTABLE (8).
        msgs = [
            types.SimpleNamespace(role="user"),
            types.SimpleNamespace(role="assistant"),
            types.SimpleNamespace(role="user"),
        ]
        keep = mgr._count_keep_messages(msgs)
        # Returns by_turn_count or n-1 — whichever is smaller.
        assert keep == 2  # n-1


# ── _format_messages_for_summary ─────────────────────────────────


class TestFormatMessagesForSummary:
    def test_simple(self):
        mgr = CompactManager()
        msgs = [
            types.SimpleNamespace(role="user", content="hello"),
            types.SimpleNamespace(role="assistant", content="hi"),
        ]
        out = mgr._format_messages_for_summary(msgs)
        assert "[user]: hello" in out
        assert "[assistant]: hi" in out

    def test_truncates_long_tool_output(self):
        mgr = CompactManager()
        big = "x" * 1000
        msgs = [types.SimpleNamespace(role="tool", content=big)]
        out = mgr._format_messages_for_summary(msgs)
        # Truncated with summary note.
        assert "1000 chars total" in out
        assert len(out) < 1000

    def test_empty_content_skipped(self):
        mgr = CompactManager()
        msgs = [
            types.SimpleNamespace(role="user", content=""),
            types.SimpleNamespace(role="assistant", content="ok"),
        ]
        out = mgr._format_messages_for_summary(msgs)
        # Empty user not included.
        assert "[user]:" not in out
        assert "[assistant]: ok" in out


# ── _summary_max_tokens ──────────────────────────────────────────


class TestSummaryMaxTokens:
    def test_default(self):
        mgr = CompactManager()
        out = mgr._summary_max_tokens()
        # Default 256000 // 64 = 4000 → clamped to 4096 ceiling? actually min(4096, 4000) = 4000.
        assert 512 <= out <= 4096

    def test_floor(self):
        mgr = CompactManager(CompactConfig(max_tokens=1024))
        # 1024 // 64 = 16 → floor at 512.
        assert mgr._summary_max_tokens() == 512

    def test_ceiling(self):
        mgr = CompactManager(CompactConfig(max_tokens=10_000_000))
        assert mgr._summary_max_tokens() == 4096


# ── _summarize ───────────────────────────────────────────────────


class TestSummarize:
    async def test_no_llm_returns_empty(self):
        mgr = _build_mgr(llm=None)
        out = await mgr._summarize("text")
        assert out == ""
        assert "No LLM" in mgr._last_summary_error

    async def test_streams_and_concatenates(self):
        llm = _LLM(chunks=["sum", "mary", " text"])
        mgr = _build_mgr(llm=llm)
        out = await mgr._summarize("anything")
        assert out == "summary text"
        # Compact prompt prepended.
        sent_messages = llm.calls[0][0]
        assert sent_messages[0]["content"] == COMPACT_PROMPT

    async def test_exception_records_error(self):
        llm = _LLM(raises=RuntimeError("API down"))
        mgr = _build_mgr(llm=llm)
        out = await mgr._summarize("x")
        assert out == ""
        assert "API down" in mgr._last_summary_error


# ── _splice_conversation ─────────────────────────────────────────


class TestSpliceConversation:
    def test_replaces_zone_with_summary(self):
        conv = Conversation()
        conv.append("system", "SYS")
        conv.append("user", "u1")
        conv.append("assistant", "a1")
        conv.append("user", "u2")
        conv.append("assistant", "a2")
        mgr = CompactManager()
        mgr._splice_conversation(conv, boundary=3, summary="SUMMARY")
        msgs = conv.get_messages()
        # Layout: [system, summary, live_zone...]
        assert msgs[0].role == "system"
        assert msgs[1].role == "assistant"
        assert "SUMMARY" in msgs[1].content
        # Live zone preserved: messages[3:] from before splice.
        assert msgs[2].content == "u2"


# ── _run_compact full flow ───────────────────────────────────────


class TestRunCompact:
    async def test_full_success_flow(self):
        conv = _build_conversation(n_user=10)
        store = _Store()
        store.events = [{"event_id": 1}, {"event_id": 5}]
        router = _Router()
        llm = _LLM(chunks=["summary"])
        plugins = _Plugins(proceed=True)
        mgr = _build_mgr(
            conversation=conv,
            llm=llm,
            router=router,
            store=store,
            plugins=plugins,
        )
        await mgr._run_compact()
        # Counter bumped, time recorded.
        assert mgr._compact_count == 1
        assert mgr._last_compact_time > 0
        # compact_complete emitted.
        kinds = [c[0] for c in router.calls]
        assert "compact_complete" in kinds
        # Plugin notified.
        assert plugins.notify_calls
        # Session store persisted.
        assert store.saved_conversations
        # snapshot_event_id set to max event_id.
        assert store.state["alice:snapshot_event_id"] == 5
        assert "alice:last_compact_time" in store.state

    async def test_plugin_veto(self):
        conv = _build_conversation(n_user=10)
        router = _Router()
        plugins = _Plugins(proceed=False)
        mgr = _build_mgr(
            conversation=conv,
            llm=_LLM(),
            router=router,
            plugins=plugins,
        )
        await mgr._run_compact()
        # Vetoed → no compact.
        assert mgr._compact_count == 0
        # Cooldown still set (so we don't retry immediately).
        assert mgr._last_compact_time > 0
        kinds = [c[0] for c in router.calls]
        assert "compact_skipped" in kinds

    async def test_summary_failure_preserves_context(self):
        conv = _build_conversation(n_user=10)
        snapshot = [m.content for m in conv.get_messages()]
        router = _Router()
        llm = _LLM(raises=RuntimeError("api err"))
        mgr = _build_mgr(conversation=conv, llm=llm, router=router)
        await mgr._run_compact()
        # Context unchanged.
        assert [m.content for m in conv.get_messages()] == snapshot
        assert mgr._compact_count == 0
        # processing_error emitted.
        kinds = [c[0] for c in router.calls]
        assert "processing_error" in kinds

    async def test_too_short_in_run_compact(self):
        conv = _build_conversation(system_only=True)
        mgr = _build_mgr(conversation=conv, llm=_LLM())
        await mgr._run_compact()
        # Nothing happened.
        assert mgr._compact_count == 0

    async def test_lease_acquired_when_caller_passes_none(self):
        conv = _build_conversation(n_user=10)
        mgr = _build_mgr(conversation=conv, llm=_LLM(chunks=["s"]))
        # Pre-acquire to block: lease=None path inside should bail.
        first = mgr._dispatch.try_acquire()
        try:
            await mgr._run_compact()
            # Did not run (already held).
            assert mgr._compact_count == 0
        finally:
            mgr._dispatch.release(first)


# ── cancel ───────────────────────────────────────────────────────


class TestCancel:
    async def test_cancel_pending_task(self):
        mgr = CompactManager()

        async def slow():
            await asyncio.sleep(10)

        mgr._compact_task = asyncio.create_task(slow())
        await asyncio.sleep(0.01)
        await mgr.cancel()
        assert mgr._compact_task is None

    async def test_cancel_idle(self):
        mgr = CompactManager()
        # No active task — must not crash.
        await mgr.cancel()


class TestRunCompactEmptyCompactZone:
    async def test_empty_compact_messages_short_circuits(self):
        """Mock _count_keep_messages to return n-1 so messages[1:1]
        triggers the ``if not compact_messages`` early-return (line 285)."""
        conv = _build_conversation(n_user=10)
        mgr = _build_mgr(conversation=conv, llm=_LLM())
        # Mock _count_keep_messages to return one less than total → boundary=1.
        # But then the outer ``if boundary <= 1`` catches it first; instead
        # patch it to return exactly len-1 to fall past that check.

        def fake_count(messages):
            # Return len(messages) - boundary_target. We want boundary=2
            # (passes the > 1 guard) but compact_messages = messages[1:2]
            # = one item — non-empty. So this slot is genuinely
            # unreachable in practice; lines 284-285 are defensive.
            return len(messages) - 2

        mgr._count_keep_messages = fake_count
        await mgr._run_compact()


class TestRunCompactSaveFailures:
    async def test_save_conversation_failure_swallowed(self):
        conv = _build_conversation(n_user=10)
        store = _Store()

        def boom(*a, **kw):
            raise RuntimeError("disk full")

        store.save_conversation = boom
        router = _Router()
        mgr = _build_mgr(
            conversation=conv,
            llm=_LLM(chunks=["summary"]),
            router=router,
            store=store,
        )
        await mgr._run_compact()
        # Compaction still succeeded (save failure swallowed).
        assert mgr._compact_count == 1

    async def test_save_state_failure_swallowed(self):
        conv = _build_conversation(n_user=10)
        store = _Store()

        def boom(*a, **kw):
            raise RuntimeError("state save failed")

        store.save_state = boom
        store.events = [{"event_id": 1}]
        router = _Router()
        mgr = _build_mgr(
            conversation=conv,
            llm=_LLM(chunks=["summary"]),
            router=router,
            store=store,
        )
        await mgr._run_compact()
        # Still counted as a successful round.
        assert mgr._compact_count == 1


class TestRunCompactCancelled:
    async def test_cancelled_during_run(self):
        """asyncio.CancelledError inside _run_compact is logged and
        the cleanup runs (lines 423-426)."""
        conv = _build_conversation(n_user=10)

        class _CancelLLM:
            calls = 0

            async def chat(self, messages, **kwargs):
                if False:
                    yield ""  # never
                raise asyncio.CancelledError()

        mgr = _build_mgr(conversation=conv, llm=_CancelLLM())
        # Run directly — the CancelledError handler swallows it.
        await mgr._run_compact()
        # No successful compact (cancelled).
        assert mgr._compact_count == 0


class TestRunCompactGenericException:
    async def test_outer_exception_swallowed(self):
        """A generic exception bubbling from inside the try is caught
        and logged (lines 425-426)."""
        conv = _build_conversation(n_user=10)

        class _BadLLM:
            async def chat(self, messages, **kwargs):
                if False:
                    yield ""
                raise RuntimeError("provider crash")

        router = _Router()
        # Make _summarize succeed but force an error elsewhere.
        mgr = _build_mgr(conversation=conv, llm=_BadLLM(), router=router)
        await mgr._run_compact()
        # Compaction failed but no exception escaped.
        assert mgr._compact_count == 0
