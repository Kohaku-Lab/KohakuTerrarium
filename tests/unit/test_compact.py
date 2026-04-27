"""Comprehensive tests for the auto-compact system."""

import asyncio
from unittest.mock import MagicMock

import pytest

from kohakuterrarium.core.compact import (
    DEFAULT_MAX_TOKENS,
    CompactConfig,
    CompactManager,
)
from kohakuterrarium.core.conversation import Conversation


def _make_conversation(n_messages: int, chars_per_msg: int = 100) -> Conversation:
    """Create a conversation with N messages of given size."""
    conv = Conversation()
    conv.append("system", "You are helpful.")
    for i in range(n_messages):
        role = "user" if i % 2 == 0 else "assistant"
        conv.append(role, f"Message {i}: " + "x" * chars_per_msg)
    return conv


def _make_manager(
    max_tokens: int = 1000,
    threshold: float = 0.80,
    keep_recent: int = 2,
) -> tuple[CompactManager, Conversation]:
    """Create a manager with a mock controller and conversation."""
    config = CompactConfig(
        max_tokens=max_tokens,
        threshold=threshold,
        keep_recent_turns=keep_recent,
    )
    mgr = CompactManager(config)

    conv = _make_conversation(20, chars_per_msg=50)
    controller = MagicMock()
    controller.conversation = conv

    mgr._controller = controller
    mgr._agent_name = "test_agent"
    return mgr, conv


class TestCompactConfig:
    def test_defaults(self):
        c = CompactConfig()
        assert c.max_tokens == DEFAULT_MAX_TOKENS
        assert c.threshold == 0.80
        assert c.target == 0.40
        assert c.keep_recent_turns == 8
        assert c.enabled is True

    def test_custom(self):
        c = CompactConfig(max_tokens=100_000, threshold=0.90, keep_recent_turns=4)
        assert c.max_tokens == 100_000
        assert c.threshold == 0.90
        assert c.keep_recent_turns == 4


class TestShouldCompact:
    def test_below_threshold(self):
        mgr, _ = _make_manager(max_tokens=1000, threshold=0.80)
        assert not mgr.should_compact(prompt_tokens=700)

    def test_above_threshold(self):
        mgr, _ = _make_manager(max_tokens=1000, threshold=0.80)
        assert mgr.should_compact(prompt_tokens=850)

    def test_at_threshold(self):
        mgr, _ = _make_manager(max_tokens=1000, threshold=0.80)
        assert mgr.should_compact(prompt_tokens=800)

    def test_disabled(self):
        mgr, _ = _make_manager(max_tokens=1000)
        mgr.config.enabled = False
        assert not mgr.should_compact(prompt_tokens=900)

    def test_already_compacting(self):
        mgr, _ = _make_manager(max_tokens=1000)
        assert mgr._dispatch.try_acquire() is not None
        assert not mgr.should_compact(prompt_tokens=900)

    def test_no_char_estimation_fallback(self):
        mgr, conv = _make_manager(max_tokens=100, threshold=0.50)
        # should_compact is token-based only; prompt_tokens=0 means
        # no token info available, so compaction should NOT trigger
        assert not mgr.should_compact(prompt_tokens=0)

    def test_no_controller(self):
        mgr = CompactManager(CompactConfig(max_tokens=100))
        mgr._controller = None
        assert not mgr.should_compact(prompt_tokens=0)


class TestCountKeepMessages:
    def test_keeps_recent_turns(self):
        mgr, conv = _make_manager(keep_recent=3)
        messages = conv.get_messages()
        keep = mgr._count_keep_messages(messages)
        # Should keep at least 3 user turns + their responses
        assert keep >= 3

    def test_keeps_at_least_one(self):
        mgr, _ = _make_manager(keep_recent=1)
        conv = _make_conversation(2)
        messages = conv.get_messages()
        keep = mgr._count_keep_messages(messages)
        assert keep >= 1

    def test_small_conversation(self):
        mgr, _ = _make_manager(keep_recent=10)
        conv = _make_conversation(4)
        messages = conv.get_messages()
        keep = mgr._count_keep_messages(messages)
        # Can't keep more than we have (minus system)
        assert keep <= len(messages) - 1


class TestFormatMessagesForSummary:
    def test_formats_roles(self):
        mgr, _ = _make_manager()
        conv = _make_conversation(4)
        messages = conv.get_messages()
        text = mgr._format_messages_for_summary(messages[1:])
        assert "[user]:" in text
        assert "[assistant]:" in text

    def test_truncates_long_tool_results(self):
        mgr, _ = _make_manager()
        conv = Conversation()
        conv.append("system", "sys")
        conv.append("tool", "x" * 1000, tool_call_id="t1", name="bash")
        messages = conv.get_messages()
        text = mgr._format_messages_for_summary(messages[1:])
        assert "1000 chars total" in text
        assert len(text) < 1000


class TestSpliceConversation:
    def test_preserves_system_prompt(self):
        mgr, conv = _make_manager()
        messages_before = len(conv.get_messages())
        boundary = messages_before - 4  # Keep last 4

        mgr._splice_conversation(conv, boundary, "This is the summary")

        messages_after = conv.get_messages()
        assert messages_after[0].role == "system"
        assert messages_after[0].content == "You are helpful."

    def test_inserts_summary(self):
        mgr, conv = _make_manager()
        messages_before = len(conv.get_messages())
        boundary = messages_before - 4

        mgr._splice_conversation(conv, boundary, "Summary content here")

        messages_after = conv.get_messages()
        # Second message should be the summary
        assert "Summary content here" in messages_after[1].content
        assert "compact round" in messages_after[1].content.lower()

    def test_preserves_live_zone(self):
        mgr, conv = _make_manager()
        messages = conv.get_messages()
        live_messages = messages[-4:]
        live_contents = [m.content for m in live_messages]
        boundary = len(messages) - 4

        mgr._splice_conversation(conv, boundary, "Summary")

        new_messages = conv.get_messages()
        # Skip system + summary, rest should match live zone
        restored_contents = [m.content for m in new_messages[2:]]
        assert restored_contents == live_contents

    def test_reduces_message_count(self):
        mgr, conv = _make_manager()
        before_count = len(conv.get_messages())
        boundary = before_count - 4

        mgr._splice_conversation(conv, boundary, "Summary")

        after_count = len(conv.get_messages())
        # Should be: 1 system + 1 summary + 4 live = 6
        assert after_count == 6
        assert after_count < before_count


class TestRunCompact:
    @pytest.mark.asyncio
    async def test_full_compact_flow(self):
        mgr, conv = _make_manager(keep_recent=2)
        before_count = len(conv.get_messages())

        # Mock LLM
        async def mock_chat(messages, stream=True, max_tokens=None):
            assert max_tokens == mgr._summary_max_tokens()
            yield "### Current Goal\nTest goal\n### Key Facts\nFact 1"

        mgr._llm = MagicMock()
        mgr._llm.chat = mock_chat

        await mgr._run_compact()

        after_count = len(conv.get_messages())
        assert after_count < before_count
        assert mgr._compact_count == 1
        assert not mgr.is_compacting

    @pytest.mark.asyncio
    async def test_compact_with_session_store(self):
        mgr, conv = _make_manager(keep_recent=2)

        async def mock_chat(messages, stream=True, max_tokens=None):
            yield "Summary"

        mgr._llm = MagicMock()
        mgr._llm.chat = mock_chat

        store = MagicMock()
        mgr._session_store = store

        await mgr._run_compact()

        # Should have saved conversation snapshot (compact_complete event
        # is recorded by SessionOutput via notify_activity, not here)
        store.save_conversation.assert_called_once()

    @pytest.mark.asyncio
    async def test_compact_with_llm_failure(self):
        mgr, conv = _make_manager(keep_recent=2)
        before_count = len(conv.get_messages())

        async def mock_chat_fail(messages, stream=True, max_tokens=None):
            raise RuntimeError("LLM error")
            yield  # make it an async generator

        mgr._llm = MagicMock()
        mgr._llm.chat = mock_chat_fail

        await mgr._run_compact()

        # On LLM failure, context should be preserved unchanged (no truncation)
        after_count = len(conv.get_messages())
        assert after_count == before_count
        assert "LLM error" in mgr._last_summary_error

    @pytest.mark.asyncio
    async def test_compact_preserves_live_zone(self):
        mgr, conv = _make_manager(keep_recent=3)

        # Get the live zone content before compact
        messages = conv.get_messages()
        keep = mgr._count_keep_messages(messages)
        live_before = [m.content for m in messages[-keep:]]

        async def mock_chat(messages, stream=True, max_tokens=None):
            yield "Summary of old messages"

        mgr._llm = MagicMock()
        mgr._llm.chat = mock_chat

        await mgr._run_compact()

        # Check live zone is preserved
        new_messages = conv.get_messages()
        live_after = [m.content for m in new_messages[2:]]  # Skip system + summary
        assert live_after == live_before

    @pytest.mark.asyncio
    async def test_incremental_compact(self):
        """Round 2 should include Round 1's summary in its input."""
        mgr, conv = _make_manager(keep_recent=2)

        call_log = []

        async def mock_chat(messages, stream=True, max_tokens=None):
            # Capture the user message content (what's being summarized)
            user_msg = messages[-1]["content"]
            call_log.append(user_msg)
            yield f"Summary round {len(call_log)}"

        mgr._llm = MagicMock()
        mgr._llm.chat = mock_chat

        # Round 1
        await mgr._run_compact()
        assert mgr._compact_count == 1

        # Add more messages to trigger round 2
        for i in range(10):
            role = "user" if i % 2 == 0 else "assistant"
            conv.append(role, f"New message {i}: " + "y" * 50)

        # Round 2
        await mgr._run_compact()
        assert mgr._compact_count == 2

        # Round 2's input should contain Round 1's summary
        assert len(call_log) == 2
        assert "Summary round 1" in call_log[1]

    def test_summary_max_tokens_is_conservative(self):
        mgr, _ = _make_manager(max_tokens=256000)
        assert mgr._summary_max_tokens() == 4000

        tiny_mgr, _ = _make_manager(max_tokens=8000)
        assert tiny_mgr._summary_max_tokens() == 512


class TestNonBlocking:
    @pytest.mark.asyncio
    async def test_trigger_is_non_blocking(self):
        mgr, conv = _make_manager(keep_recent=2)

        # Slow LLM that takes time
        async def slow_chat(messages, stream=True, max_tokens=None):
            await asyncio.sleep(0.2)
            yield "Summary"

        mgr._llm = MagicMock()
        mgr._llm.chat = slow_chat

        # Trigger should return immediately
        mgr.trigger_compact()
        assert mgr.is_compacting

        # Agent can continue working (simulate by appending messages)
        conv.append("user", "New message during compact")
        conv.append("assistant", "Response during compact")

        # Wait for compact to finish
        await asyncio.sleep(0.5)
        assert not mgr.is_compacting
        assert mgr._compact_count == 1

    @pytest.mark.asyncio
    async def test_cancel_compact(self):
        mgr, conv = _make_manager(keep_recent=2)

        async def very_slow_chat(messages, stream=True, max_tokens=None):
            await asyncio.sleep(10)
            yield "Summary"

        mgr._llm = MagicMock()
        mgr._llm.chat = very_slow_chat

        mgr.trigger_compact()
        assert mgr.is_compacting

        await mgr.cancel()
        assert not mgr.is_compacting

    @pytest.mark.asyncio
    async def test_no_double_compact(self):
        mgr, conv = _make_manager(keep_recent=2)

        async def slow_chat(messages, stream=True, max_tokens=None):
            await asyncio.sleep(0.2)
            yield "Summary"

        mgr._llm = MagicMock()
        mgr._llm.chat = slow_chat

        assert mgr.trigger_compact() is True
        assert mgr.trigger_compact() is False  # Ignored while busy
        assert mgr.is_compacting

        await asyncio.sleep(0.5)
        assert mgr._compact_count == 1  # Only one compact ran

    @pytest.mark.asyncio
    async def test_direct_run_ignored_while_background_compact_active(self):
        mgr, conv = _make_manager(keep_recent=2)

        started = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def gated_chat(messages, stream=True, max_tokens=None):
            calls.append(True)
            started.set()
            await release.wait()
            yield "Summary"

        mgr._llm = MagicMock()
        mgr._llm.chat = gated_chat

        assert mgr.trigger_compact() is True
        await asyncio.wait_for(started.wait(), timeout=1)

        await mgr._run_compact()
        assert len(calls) == 1

        release.set()
        await asyncio.sleep(0.1)
        assert mgr._compact_count == 1


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_tiny_conversation(self):
        mgr = CompactManager(CompactConfig(keep_recent_turns=10))
        conv = _make_conversation(2)
        controller = MagicMock()
        controller.conversation = conv
        mgr._controller = controller
        mgr._agent_name = "test"

        async def mock_chat(messages, stream=True, max_tokens=None):
            yield "Summary"

        mgr._llm = MagicMock()
        mgr._llm.chat = mock_chat

        await mgr._run_compact()
        # Should not compact (not enough messages)
        assert mgr._compact_count == 0

    @pytest.mark.asyncio
    async def test_no_llm(self):
        mgr, conv = _make_manager(keep_recent=2)
        mgr._llm = None
        before_count = len(conv.get_messages())

        await mgr._run_compact()
        # On LLM failure/missing, context should be preserved unchanged
        after_count = len(conv.get_messages())
        assert after_count == before_count

    def test_is_compacting_property(self):
        mgr = CompactManager()
        assert not mgr.is_compacting
        assert mgr._dispatch.try_acquire() is not None
        assert mgr.is_compacting
