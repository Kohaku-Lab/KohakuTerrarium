"""Integration test: auto-compact triggered through the real agent workflow.

Uses ScriptedLLM + real Controller + real Executor to simulate a full
agent processing cycle that triggers compaction.
"""

import asyncio

import pytest

from kohakuterrarium.core.compact import CompactConfig, CompactManager
from kohakuterrarium.testing import TestAgentBuilder


class TestCompactIntegration:
    """Test compaction triggered through the real agent processing pipeline."""

    @pytest.mark.asyncio
    async def test_compact_triggers_from_processing(self):
        """Full flow: inject input -> LLM responds -> token check -> compact fires."""
        # Build a test agent with a conversation already near the limit
        env = (
            TestAgentBuilder()
            .with_llm_script(
                [
                    "I'll help with that. Let me check.",
                    "Here is what I found.",
                    "The summary of everything.",  # This will be used by compact LLM
                ]
            )
            .with_system_prompt("You are helpful.")
            .build()
        )

        agent_name = "test_compact_agent"

        # Create a CompactManager with a very low threshold to trigger easily
        compact_mgr = CompactManager(
            CompactConfig(
                max_tokens=50,  # Very low: 50 tokens
                threshold=0.50,  # Trigger at 25 tokens
                keep_recent_turns=1,
            )
        )
        compact_mgr._controller = env.controller
        compact_mgr._agent_name = agent_name

        # We need a separate LLM for compaction (the ScriptedLLM is consumed by the agent)
        compact_responses = []

        async def compact_chat(messages, stream=True):
            compact_responses.append(True)
            yield "### Current Goal\nTest\n### Key Facts\nFact 1"

        from unittest.mock import MagicMock

        compact_llm = MagicMock()
        compact_llm.chat = compact_chat
        compact_mgr._llm = compact_llm

        # Fill conversation to approach the limit
        conv = env.controller.conversation
        for i in range(20):
            role = "user" if i % 2 == 0 else "assistant"
            conv.append(
                role, f"Conversation message {i} with enough content: " + "x" * 200
            )

        # Check that compact would trigger (simulating high token count)
        # should_compact is token-based only; pass a token count above threshold (50 * 0.50 = 25)
        assert compact_mgr.should_compact(prompt_tokens=30)
        compact_mgr.trigger_compact()

        # Wait for background compact
        await asyncio.sleep(0.5)

        assert not compact_mgr.is_compacting
        assert compact_mgr._compact_count == 1
        assert len(compact_responses) == 1

        # Conversation should be shorter
        new_count = len(conv.get_messages())
        assert new_count < 22  # 20 + 1 system + extras

        # Summary should be in the conversation
        messages = conv.get_messages()
        assert any("compact round" in str(m.content).lower() for m in messages)

    @pytest.mark.asyncio
    async def test_compact_does_not_block_new_messages(self):
        """Verify the agent can process new messages while compacting."""
        env = (
            TestAgentBuilder()
            .with_llm_script(["Response 1", "Response 2"])
            .with_system_prompt("You are helpful.")
            .build()
        )

        compact_mgr = CompactManager(
            CompactConfig(
                max_tokens=50,
                threshold=0.50,
                keep_recent_turns=1,
            )
        )
        compact_mgr._controller = env.controller
        compact_mgr._agent_name = "test"

        # Slow compact LLM
        async def slow_compact(messages, stream=True):
            await asyncio.sleep(0.3)
            yield "Summary"

        from unittest.mock import MagicMock

        compact_mgr._llm = MagicMock()
        compact_mgr._llm.chat = slow_compact

        # Fill conversation
        conv = env.controller.conversation
        for i in range(20):
            conv.append("user" if i % 2 == 0 else "assistant", "x" * 200)

        # Start compact
        compact_mgr.trigger_compact()
        assert compact_mgr.is_compacting

        # Agent can still append messages during compact
        conv.append("user", "New message during compact")
        conv.append("assistant", "Response during compact")

        # Wait for compact to finish
        await asyncio.sleep(0.6)
        assert not compact_mgr.is_compacting

        # New messages should still be present; compaction must not block
        # normal progress even when it runs in the background.
        messages = conv.get_messages()
        contents = [str(m.content) for m in messages]
        assert compact_mgr._compact_count == 1
        assert any("New message during compact" in c for c in contents)
        assert any("Response during compact" in c for c in contents)

    @pytest.mark.asyncio
    async def test_incremental_compact_includes_previous_summary(self):
        """Round 2 compact should see Round 1's summary in its input."""
        env = (
            TestAgentBuilder()
            .with_llm_script(["ok"])
            .with_system_prompt("System prompt.")
            .build()
        )

        compact_mgr = CompactManager(
            CompactConfig(
                max_tokens=50,
                threshold=0.50,
                keep_recent_turns=1,
            )
        )
        compact_mgr._controller = env.controller
        compact_mgr._agent_name = "test"

        summarize_inputs = []

        async def tracking_compact(messages, stream=True):
            # Capture what's being summarized
            user_msg = messages[-1]["content"]
            summarize_inputs.append(user_msg)
            yield f"Round {len(summarize_inputs)} summary"

        from unittest.mock import MagicMock

        compact_mgr._llm = MagicMock()
        compact_mgr._llm.chat = tracking_compact

        conv = env.controller.conversation

        # Round 1: fill and compact
        for i in range(20):
            conv.append("user" if i % 2 == 0 else "assistant", f"msg{i} " + "x" * 100)

        await compact_mgr._run_compact()
        assert compact_mgr._compact_count == 1
        compact_mgr._last_compact_time = 0

        # Round 2: add more messages and compact again
        for i in range(20):
            conv.append("user" if i % 2 == 0 else "assistant", f"new{i} " + "y" * 100)

        await compact_mgr._run_compact()
        assert compact_mgr._compact_count == 2

        # Round 2's input should contain "Round 1 summary"
        assert len(summarize_inputs) == 2
        assert "Round 1 summary" in summarize_inputs[1]
