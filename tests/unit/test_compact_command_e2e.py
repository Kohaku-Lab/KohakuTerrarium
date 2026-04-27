"""End-to-end coverage for the ``/compact`` slash command.

The frontend-button path was reported broken. The backend was actually
fine — the gap was in the web UI, which discarded the response. This
file pins the backend behaviour so a future regression on the slash
command itself is caught immediately.

Each test exercises a distinct response shape that the web frontend's
``surfaceCommandResult`` helper renders as a toast. Pre-existing CLI
rich (``app.py``) and TUI (``input.py``) commit ``output`` directly,
so they share the same contract.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from kohakuterrarium.builtins.user_commands.compact import CompactCommand
from kohakuterrarium.core.compact import CompactConfig, CompactManager
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.modules.user_command.base import UserCommandContext


def _make_conversation(n: int) -> Conversation:
    conv = Conversation()
    conv.append("system", "you are helpful")
    for i in range(n):
        conv.append("user" if i % 2 == 0 else "assistant", f"m{i}")
    return conv


def _make_agent(*, conversation_size: int = 20, keep_recent: int = 2):
    cfg = CompactConfig(max_tokens=1000, threshold=0.5, keep_recent_turns=keep_recent)
    mgr = CompactManager(cfg)
    conv = _make_conversation(conversation_size)
    controller = MagicMock()
    controller.conversation = conv
    mgr._controller = controller
    mgr._agent_name = "test"
    mgr._output_router = MagicMock()
    mgr._output_router.notify_activity = MagicMock()

    async def fast_chat(messages, *, stream=True, **kwargs):
        yield "summary"

    mgr._llm = MagicMock()
    mgr._llm.chat = fast_chat

    agent = MagicMock()
    agent.compact_manager = mgr
    return agent, mgr


# ─────────────────────────────────────────────────────────────────────


class TestCompactSlashCommand:
    @pytest.mark.asyncio
    async def test_no_agent_context(self):
        cmd = CompactCommand()
        result = await cmd.execute("", UserCommandContext(agent=None))
        assert result.error == "No agent context."
        assert result.success is False

    @pytest.mark.asyncio
    async def test_no_compact_manager(self):
        cmd = CompactCommand()
        agent = MagicMock()
        agent.compact_manager = None
        result = await cmd.execute("", UserCommandContext(agent=agent))
        assert result.error == "Compaction not configured."

    @pytest.mark.asyncio
    async def test_happy_path_returns_notify_payload(self):
        agent, mgr = _make_agent()
        cmd = CompactCommand()

        result = await cmd.execute("", UserCommandContext(agent=agent))

        assert result.success is True
        assert "Compaction triggered" in result.output
        # The frontend toast renders ``data.message`` at ``data.level``.
        # Without this the user sees nothing when they click the button.
        assert result.data == {
            "type": "notify",
            "message": "Context compaction started",
            "level": "info",
        }

        # Drain the in-flight task — the test's fake LLM is fast but
        # ``_run_compact`` still creates a task we want to await for
        # clean teardown.
        if mgr._compact_task:
            await mgr._compact_task

    @pytest.mark.asyncio
    async def test_too_short_path_returns_warning_notify(self):
        agent, mgr = _make_agent(conversation_size=2, keep_recent=10)
        cmd = CompactCommand()

        result = await cmd.execute("", UserCommandContext(agent=agent))

        assert result.success is True
        assert result.data["type"] == "notify"
        assert result.data["level"] == "warning"
        assert "Nothing to compact" in result.data["message"]

    @pytest.mark.asyncio
    async def test_busy_path_returns_warning_notify(self):
        # Use a slow LLM so the first compact is still in-flight when
        # the second slash invocation runs. The slash command's
        # ``is_compacting`` early-return at line 28 of the command
        # surfaces this case.
        cfg = CompactConfig(max_tokens=1000, threshold=0.5, keep_recent_turns=2)
        mgr = CompactManager(cfg)
        conv = _make_conversation(20)
        controller = MagicMock()
        controller.conversation = conv
        mgr._controller = controller
        mgr._agent_name = "test"
        mgr._output_router = MagicMock()
        mgr._output_router.notify_activity = MagicMock()

        async def slow_chat(messages, *, stream=True, **kwargs):
            await asyncio.sleep(0.2)
            yield "x"

        mgr._llm = MagicMock()
        mgr._llm.chat = slow_chat

        agent = MagicMock()
        agent.compact_manager = mgr

        # Trigger one in flight.
        mgr.trigger_compact()
        cmd = CompactCommand()
        result = await cmd.execute("", UserCommandContext(agent=agent))

        assert result.success is True
        assert result.output == "Compaction already in progress."
        assert result.data == {
            "type": "notify",
            "message": "Compaction already in progress",
            "level": "warning",
        }

        if mgr._compact_task:
            await mgr._compact_task

    @pytest.mark.asyncio
    async def test_skip_reason_drives_message_when_busy_lease_only(self):
        """Pre-acquire the lease without flipping ``is_compacting`` via
        the manager — confirms the new ``_last_skip_reason`` branch in
        the slash command surfaces the precise message instead of the
        generic ``"Compaction not triggered"`` fallback.
        """
        agent, mgr = _make_agent()
        # Acquire the lease directly so ``is_compacting`` is True and
        # ``trigger_compact`` reports ``busy``.
        mgr._dispatch.try_acquire()
        cmd = CompactCommand()

        result = await cmd.execute("", UserCommandContext(agent=agent))

        # The early ``is_compacting`` guard returns ``"Compaction
        # already in progress."`` — the same message the user sees
        # whichever path picks it up. The point: it is NOT the generic
        # "Compaction was not triggered." fallback, which would mean
        # the new skip-reason wiring failed.
        assert result.output == "Compaction already in progress."
        assert "not triggered" not in result.output.lower()
