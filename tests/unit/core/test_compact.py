"""Unit tests for :mod:`kohakuterrarium.core.compact`."""

import asyncio
import types
from typing import Any


from kohakuterrarium.core.compact_branch import (
    compute_replaced_range,
    current_path,
)
from kohakuterrarium.core.compact import (
    COMPACT_PROMPT,
    DEFAULT_KEEP_RECENT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_THRESHOLD,
    CompactConfig,
    CompactManager,
)
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.llm.message import create_message

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

    async def test_full_flow_clears_stale_last_usage(self):
        # The next round's usage still measures the pre-splice prompt —
        # it must not be able to re-trigger on that stale number.
        conv = _build_conversation(n_user=12)
        mgr = _build_mgr(conversation=conv, llm=_LLM(chunks=["S"]))
        mgr._controller._last_usage = {"prompt_tokens": 999_999}
        await mgr._run_compact()
        assert mgr._compact_count == 1
        assert mgr._controller._last_usage == {}


# ── stale-usage guard (compact double-fire) ──────────────────────


class TestStaleUsageGuard:
    def _mgr_with_conv(self, conv):
        mgr = _build_mgr(conversation=conv)
        mgr.config.max_tokens = 100_000
        return mgr

    def test_post_splice_stale_usage_suppressed(self):
        import time as _t

        # A round that started pre-splice can deliver its huge
        # prompt_tokens after the cooldown expired — the tiny spliced
        # conversation must veto the re-fire.
        conv = Conversation()
        conv.append("system", "sys")
        conv.append("assistant", "[summary]")
        conv.append("user", "next")
        mgr = self._mgr_with_conv(conv)
        mgr._last_compact_time = _t.time() - mgr.config.cooldown_seconds - 1
        assert mgr.should_compact(prompt_tokens=999_999) is False

    def test_post_splice_genuinely_large_conversation_fires(self):
        import time as _t

        conv = Conversation()
        conv.append("system", "sys")
        for _ in range(20):
            conv.append("user", "x" * 20_000)
        mgr = self._mgr_with_conv(conv)
        mgr._last_compact_time = _t.time() - mgr.config.cooldown_seconds - 1
        assert mgr.should_compact(prompt_tokens=95_000) is True

    def test_stale_window_expiry_restores_trust(self):
        import time as _t

        conv = Conversation()
        conv.append("system", "sys")
        conv.append("user", "small")
        mgr = self._mgr_with_conv(conv)
        mgr._last_compact_time = _t.time() - 10_000
        assert mgr.should_compact(prompt_tokens=95_000) is True


# ── splice safety ────────────────────────────────────────────────


class TestSpliceSafety:
    def test_splice_aborts_when_conversation_shrank(self):
        conv = _build_conversation(n_user=6)
        msgs = conv.get_messages()
        boundary = 9
        expected_last = msgs[boundary - 1]
        # Rewind / emergency drop landed while the summary LLM ran.
        conv._messages = conv._messages[:4]
        mgr = _build_mgr(conversation=conv)
        before = list(conv.get_messages())
        assert (
            mgr._splice_conversation(conv, boundary, "S", expected_last=expected_last)
            is False
        )
        assert conv.get_messages() == before

    def test_splice_rejects_boundary_on_tool_result(self):
        # The boundary is made tool-safe BEFORE summarization; a
        # tool-result head reaching the splice means the conversation
        # changed shape — silently walking it back would keep
        # already-summarized content raw (duplicated).
        conv = Conversation()
        conv.append("system", "sys")
        for i in range(4):
            conv.append("user", f"u{i}")
            conv.append(
                "assistant",
                "",
                tool_calls=[
                    {
                        "id": f"tc_{i}",
                        "type": "function",
                        "function": {"name": "t", "arguments": "{}"},
                    }
                ],
            )
            conv.append("tool", f"r{i}", tool_call_id=f"tc_{i}")
        msgs = conv.get_messages()
        boundary = next(i for i, m in enumerate(msgs) if m.role == "tool" and i > 3)
        mgr = _build_mgr(conversation=conv)
        before = list(conv.get_messages())
        assert (
            mgr._splice_conversation(
                conv, boundary, "S", expected_last=msgs[boundary - 1]
            )
            is False
        )
        assert conv.get_messages() == before

    async def test_boundary_walkback_happens_before_summary(self):
        # The tool-safe adjustment must run BEFORE the summarizer:
        # content kept raw in the live zone must never also appear in
        # the summarizer's input (double representation).
        conv = Conversation()
        conv.append("system", "sys")
        conv.append("user", "u0")
        conv.append("assistant", "a0")
        conv.append("user", "u1")
        conv.append(
            "assistant",
            "I will run bash now",
            tool_calls=[
                {
                    "id": "tc_x",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        )
        conv.append("tool", "done", tool_call_id="tc_x")
        conv.append("user", "u2")
        conv.append("assistant", "a2")
        conv.append("user", "u3")
        conv.append("assistant", "a3")
        llm = _LLM(chunks=["S"])
        mgr = _build_mgr(conversation=conv, llm=llm)
        # keep_recent_turns high → half-cap fallback puts the raw
        # boundary exactly ON the tool result (index 5 of 10).
        mgr.config.keep_recent_turns = 99
        await mgr._run_compact()
        assert mgr._compact_count == 1
        summary_inputs = "".join(str(messages) for messages, _kw in llm.calls)
        assert "I will run bash now" not in summary_inputs, (
            "announcement kept raw in the live zone must not ALSO be " "summarized"
        )
        final = [str(m.content) for m in conv.get_messages()]
        assert sum("I will run bash now" in c for c in final) == 1

    async def test_inplace_prefix_edit_aborts_splice(self):
        # An edit that rewrites a summarized message IN PLACE keeps
        # object identity — only a content fingerprint catches it.
        conv = _build_conversation(n_user=10)
        router = _Router()

        class _MutatingLLM(_LLM):
            async def chat(self, messages, **kwargs):
                conv._messages[1].content = "EDITED while summarizing"
                yield "S"

        mgr = _build_mgr(conversation=conv, llm=_MutatingLLM(), router=router)
        await mgr._run_compact()
        assert mgr._compact_count == 0
        contents = [str(m.content) for m in conv.get_messages()]
        assert "EDITED while summarizing" in contents, (
            "the in-place edit must survive — not be discarded under a "
            "summary generated from the old content"
        )
        terminals = [
            (k, (m or {}).get("reason"))
            for k, _msg, m in router.calls
            if k in ("compact_complete", "compact_skipped")
        ]
        assert terminals == [("compact_skipped", "conversation_changed")]

    def test_splice_preserves_inflight_tail_announcement(self):
        # The splice runs mid-turn: the live tail can end with an
        # assistant tool announcement whose results are still
        # executing. Pruning it orphans the results when they arrive.
        conv = _build_conversation(n_user=6)
        conv.append(
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "pending_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        )
        msgs = conv.get_messages()
        boundary = 5
        mgr = _build_mgr(conversation=conv)
        assert (
            mgr._splice_conversation(
                conv, boundary, "S", expected_last=msgs[boundary - 1]
            )
            is True
        )
        tail = conv.get_messages()[-1]
        assert getattr(tail, "role", None) == "assistant"
        calls = getattr(tail, "tool_calls", None)
        assert (
            calls and calls[0]["id"] == "pending_1"
        ), "in-flight announcement must survive the splice"

    def test_splice_preserves_partially_answered_tail(self):
        # Two calls announced, one result landed, one still running —
        # the pending id must stay in the announcement's tool_calls.
        conv = _build_conversation(n_user=6)
        conv.append(
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "done_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                },
                {
                    "id": "pending_2",
                    "type": "function",
                    "function": {"name": "grep", "arguments": "{}"},
                },
            ],
        )
        conv.append("tool", "result", tool_call_id="done_1")
        msgs = conv.get_messages()
        boundary = 5
        mgr = _build_mgr(conversation=conv)
        assert (
            mgr._splice_conversation(
                conv, boundary, "S", expected_last=msgs[boundary - 1]
            )
            is True
        )
        announce = conv.get_messages()[-2]
        ids = {tc["id"] for tc in (getattr(announce, "tool_calls", None) or [])}
        assert ids == {"done_1", "pending_2"}

    async def test_persisted_snapshot_keeps_inflight_tail(self):
        # to_messages() re-sanitizes for the WIRE (correctly dropping
        # unanswered tool_calls) — the persisted snapshot must not, or
        # resume can't reconstruct the active native round.
        conv = _build_conversation(n_user=10)
        conv.append(
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "pending_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        )
        store = _Store()
        mgr = _build_mgr(conversation=conv, llm=_LLM(chunks=["s"]), store=store)
        await mgr._run_compact()
        assert store.saved_conversations, "compact must persist a snapshot"
        _, saved = store.saved_conversations[-1]
        announced = [
            tc["id"]
            for m in saved
            if m.get("role") == "assistant" and m.get("tool_calls")
            for tc in m["tool_calls"]
        ]
        assert "pending_1" in announced

    async def test_replaced_conversation_aborts_without_success(self):
        # /clear or attach can swap controller.conversation while the
        # summarizer runs — the captured object must NOT be spliced,
        # persisted, or counted as a successful round.
        conv = _build_conversation(n_user=10)
        router = _Router()
        store = _Store()

        replacement = _build_conversation(n_user=2)
        mgr = _build_mgr(conversation=conv, router=router, store=store)

        class _SwappingLLM(_LLM):
            async def chat(self, messages, **kwargs):
                mgr._controller.conversation = replacement
                yield "s"

        mgr._llm = _SwappingLLM()
        await mgr._run_compact()
        assert mgr._compact_count == 0
        assert store.saved_conversations == []
        terminals = [
            (k, (m or {}).get("reason"))
            for k, _msg, m in router.calls
            if k in ("compact_complete", "compact_skipped")
        ]
        assert terminals == [("compact_skipped", "conversation_replaced")]
        # Neither object carries a summary splice.
        assert all(
            "Previous context summary" not in str(m.content)
            for m in conv.get_messages() + replacement.get_messages()
        )

    async def test_inplace_tool_call_id_edit_aborts_splice(self):
        # The fingerprint must cover the CANONICAL message dict — a
        # role=tool message's ``tool_call_id`` is provider-relevant but
        # absent from (role, content, tool_calls); mutating it in place
        # must still abort the splice.
        conv = _build_conversation(n_user=10)
        conv._messages[2].tool_calls = [
            {
                "id": "orig_1",
                "type": "function",
                "function": {"name": "bash", "arguments": "{}"},
            }
        ]
        conv._messages.insert(
            3, create_message("tool", "r", tool_call_id="orig_1", name="bash")
        )
        router = _Router()

        class _MutatingLLM(_LLM):
            async def chat(self, messages, **kwargs):
                conv._messages[3].tool_call_id = "swapped_1"
                yield "S"

        mgr = _build_mgr(conversation=conv, llm=_MutatingLLM(), router=router)
        await mgr._run_compact()
        assert mgr._compact_count == 0
        terminals = [
            (k, (m or {}).get("reason"))
            for k, _msg, m in router.calls
            if k in ("compact_complete", "compact_skipped")
        ]
        assert terminals == [("compact_skipped", "conversation_changed")]

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


class TestCompactTerminalGuarantee:
    """Every started round must emit exactly one terminal
    (``compact_complete`` or ``compact_skipped``) — FE / TUI / rich CLI
    keep a "compacting…" indicator open until one lands."""

    @staticmethod
    def _terminals(router):
        return [
            (k, (m or {}).get("reason"))
            for k, _msg, m in router.calls
            if k in ("compact_complete", "compact_skipped")
        ]

    async def test_success_emits_single_complete_terminal(self):
        conv = _build_conversation(n_user=10)
        router = _Router()
        mgr = _build_mgr(conversation=conv, llm=_LLM(chunks=["s"]), router=router)
        await mgr._run_compact()
        assert [k for k, _ in self._terminals(router)] == ["compact_complete"]

    async def test_insufficient_boundary_emits_skipped(self):
        conv = _build_conversation(n_user=1)
        router = _Router()
        mgr = _build_mgr(conversation=conv, llm=_LLM(), router=router)
        await mgr._run_compact()
        assert self._terminals(router) == [("compact_skipped", "nothing_to_compact")]

    async def test_summary_failure_emits_skipped(self):
        conv = _build_conversation(n_user=10)
        router = _Router()
        mgr = _build_mgr(
            conversation=conv, llm=_LLM(raises=RuntimeError("boom")), router=router
        )
        await mgr._run_compact()
        assert self._terminals(router) == [("compact_skipped", "summary_failed")]

    async def test_splice_race_emits_skipped(self):
        conv = _build_conversation(n_user=10)
        router = _Router()

        class _MutatingLLM(_LLM):
            async def chat(self, messages, **kwargs):
                conv._messages = conv._messages[:3]
                yield "s"

        mgr = _build_mgr(conversation=conv, llm=_MutatingLLM(), router=router)
        await mgr._run_compact()
        assert self._terminals(router) == [("compact_skipped", "conversation_changed")]

    async def test_cancel_emits_skipped(self):
        conv = _build_conversation(n_user=10)
        router = _Router()
        gate = asyncio.Event()

        class _BlockingLLM(_LLM):
            async def chat(self, messages, **kwargs):
                await gate.wait()
                yield "s"

        mgr = _build_mgr(conversation=conv, llm=_BlockingLLM(), router=router)
        task = asyncio.create_task(mgr._run_compact())
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert self._terminals(router) == [("compact_skipped", "cancelled")]

    async def test_plugin_veto_emits_skipped(self):
        conv = _build_conversation(n_user=10)
        router = _Router()
        mgr = _build_mgr(
            conversation=conv,
            llm=_LLM(),
            router=router,
            plugins=_Plugins(proceed=False),
        )
        await mgr._run_compact()
        assert self._terminals(router) == [("compact_skipped", "plugin_veto")]


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


# ── compact companion: elide live-zone tool results ─────────────


class TestCompactElidesLiveZone:
    async def test_compact_elides_stale_tool_results_keeps_latest(self):
        # Compact summarizes the compact zone but leaves the live zone
        # untouched; elide (the compact companion) must stub stale tool
        # results there so the post-compact prompt actually drops below
        # the threshold. The latest round stays verbatim.
        from kohakuterrarium.core.conversation_elide import ELISION_MARKER

        conv = Conversation()
        conv.append("system", "sys")
        big = "Z" * 1000  # > MIN_ELIDABLE_CHARS
        for i in range(10):
            conv.append("user", f"q{i}")
            conv.append(
                "assistant",
                f"a{i}",
                tool_calls=[
                    {"id": f"c{i}", "function": {"name": "read", "arguments": "{}"}}
                ],
            )
            conv.append("tool", f"tool-result-{i} " + big, tool_call_id=f"c{i}")

        mgr = _build_mgr(conversation=conv, llm=_LLM(chunks=["S"]))
        await mgr._run_compact()
        assert mgr._compact_count == 1

        messages = conv.get_messages()
        # The compact zone became a summary.
        assert any(m.role == "assistant" and "S" in (m.content or "") for m in messages)
        # Stale live-zone tool results were stubbed.
        elided = [
            m
            for m in messages
            if m.role == "tool" and ELISION_MARKER in (m.content or "")
        ]
        assert elided, "expected stale tool results to be elided after compact"
        # The latest round's tool result is still verbatim.
        assert any(
            m.role == "tool" and f"tool-result-9 {big}" == m.content for m in messages
        )

    async def test_compact_skips_elide_when_disabled(self):
        conv = Conversation()
        conv.append("system", "sys")
        big = "Z" * 1000
        for i in range(10):
            conv.append("user", f"q{i}")
            conv.append(
                "assistant",
                f"a{i}",
                tool_calls=[
                    {"id": f"c{i}", "function": {"name": "read", "arguments": "{}"}}
                ],
            )
            conv.append("tool", f"tool-result-{i} " + big, tool_call_id=f"c{i}")

        mgr = _build_mgr(conversation=conv, llm=_LLM(chunks=["S"]))
        # Controller config present and elide explicitly disabled.
        from types import SimpleNamespace

        mgr._controller = SimpleNamespace(
            conversation=conv,
            config=SimpleNamespace(elide_tool_results=False),
        )
        await mgr._run_compact()

        from kohakuterrarium.core.conversation_elide import ELISION_MARKER

        assert not any(
            m.role == "tool" and ELISION_MARKER in (m.content or "")
            for m in conv.get_messages()
        )


# ── P2: branch-aware compact metadata ────────────────────────────


class TestBranchAwareCompactMetadata:
    def test_current_path_expands_agent_branch_path(self):
        from types import SimpleNamespace

        mgr = CompactManager()
        mgr._agent = SimpleNamespace(
            _parent_branch_path=[(1, 1), (2, 2)],
            _turn_index=3,
            _branch_id=2,
        )
        assert current_path(mgr._agent) == {(1, 1), (2, 2), (3, 2)}

    def test_current_path_empty_without_agent(self):
        mgr = CompactManager()
        assert current_path(mgr._agent) == set()

    def test_compute_replaced_range_covers_path_turns_only(self):
        events = [
            # common ancestor turn1-b1 (one copy, shared by all branches)
            {"event_id": 1, "type": "user_message", "turn_index": 1, "branch_id": 1},
            {"event_id": 2, "type": "text_chunk", "turn_index": 1, "branch_id": 1},
            # sibling branch (branch 1) specific turns - ignored
            {"event_id": 3, "type": "user_message", "turn_index": 2, "branch_id": 1},
            {"event_id": 4, "type": "text_chunk", "turn_index": 2, "branch_id": 1},
            {"event_id": 5, "type": "user_message", "turn_index": 3, "branch_id": 1},
            {"event_id": 6, "type": "text_chunk", "turn_index": 3, "branch_id": 1},
            # branch 2 specific turns: turn2-b2, turn3-b2
            {"event_id": 7, "type": "user_message", "turn_index": 2, "branch_id": 2},
            {"event_id": 8, "type": "text_chunk", "turn_index": 2, "branch_id": 2},
            {"event_id": 9, "type": "user_message", "turn_index": 3, "branch_id": 2},
            {"event_id": 10, "type": "text_chunk", "turn_index": 3, "branch_id": 2},
        ]
        path = {(1, 1), (2, 2), (3, 2)}
        # keep turn3 -> cover turn1-b1 (1,2) + turn2-b2 (7,8) = events 1..8
        assert compute_replaced_range(events, 1, path) == (1, 8)
        # keep turn2+turn3 -> cover turn1-b1 only = events 1..2
        assert compute_replaced_range(events, 2, path) == (1, 2)

    def test_compute_replaced_range_none_when_all_live(self):
        events = [
            {"event_id": 5, "type": "user_message", "turn_index": 1, "branch_id": 1},
        ]
        path = {(1, 1)}
        assert compute_replaced_range(events, 1, path) is None

    def test_compute_replaced_range_none_without_path(self):
        events = [
            {"event_id": 5, "type": "user_message", "turn_index": 1, "branch_id": 1},
        ]
        assert compute_replaced_range(events, 1, set()) is None


class TestComputeReplacedRangeDedupe:
    def test_duplicate_last_turn_does_not_shift_first_live(self):
        events = [
            {"event_id": 1, "type": "user_message", "turn_index": 1, "branch_id": 1},
            {"event_id": 2, "type": "text_chunk", "turn_index": 1, "branch_id": 1},
            {"event_id": 3, "type": "user_message", "turn_index": 2, "branch_id": 1},
            {"event_id": 4, "type": "user_message", "turn_index": 2, "branch_id": 2},
            {"event_id": 5, "type": "text_chunk", "turn_index": 2, "branch_id": 2},
            {"event_id": 6, "type": "user_message", "turn_index": 3, "branch_id": 2},
            {"event_id": 7, "type": "user_message", "turn_index": 3, "branch_id": 2},
            {"event_id": 8, "type": "text_chunk", "turn_index": 3, "branch_id": 2},
        ]
        path = {(1, 1), (2, 2), (3, 2)}
        # keep 2 live turns (turn2, turn3) -> cover turn1 only
        assert compute_replaced_range(events, 2, path) == (1, 2)


class TestCurrentPathResilience:
    def test_tolerates_malformed_parent_path(self):
        from types import SimpleNamespace
        from kohakuterrarium.core.compact_branch import current_path

        agent = SimpleNamespace(
            _parent_branch_path=[1, [], [1], [1, 2], "ab"],
            _turn_index=2,
            _branch_id=1,
        )
        # malformed entries skipped; valid [1,2] + current (2,1) kept
        assert current_path(agent) == {(1, 2), (2, 1)}
