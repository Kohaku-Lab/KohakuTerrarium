"""Unit tests for :mod:`kohakuterrarium.modules.subagent.interactive`.

Behavior-first: InteractiveSubAgent stays alive, processes queued
context updates by generating a response, streams output through the
callback, buffers output for return_as_context, and stops cleanly.
"""

import asyncio


from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.subagent.config import (
    ContextUpdateMode,
    SubAgentConfig,
)
from kohakuterrarium.modules.subagent.interactive import (
    InteractiveOutput,
    InteractiveSubAgent,
)
from kohakuterrarium.testing.llm import ScriptedLLM


def _config(mode=ContextUpdateMode.QUEUE_APPEND, max_turns=1):
    return SubAgentConfig(
        name="chat",
        interactive=True,
        context_mode=mode,
        max_turns=max_turns,
    )


async def _collect_output(agent, expected_complete=1, timeout=2.0):
    """Run the agent until *expected_complete* completion chunks arrive."""
    completions = asyncio.Event()
    chunks: list[InteractiveOutput] = []

    def _on_output(chunk: InteractiveOutput) -> None:
        chunks.append(chunk)
        complete_count = sum(1 for c in chunks if c.is_complete)
        if complete_count >= expected_complete:
            completions.set()

    agent.on_output = _on_output
    await asyncio.wait_for(completions.wait(), timeout=timeout)
    return chunks


class TestLifecycle:
    async def test_start_activates_then_stop_deactivates(self):
        agent = InteractiveSubAgent(_config(), Registry(), ScriptedLLM(["hi"]))
        assert agent.is_active is False
        await agent.start()
        assert agent.is_active is True
        await agent.stop()
        assert agent.is_active is False

    async def test_double_start_is_idempotent(self):
        agent = InteractiveSubAgent(_config(), Registry(), ScriptedLLM(["hi"]))
        await agent.start()
        await agent.start()  # second start is a no-op, must not raise
        assert agent.is_active is True
        await agent.stop()

    async def test_stop_when_inactive_is_a_noop(self):
        agent = InteractiveSubAgent(_config(), Registry(), ScriptedLLM(["hi"]))
        await agent.stop()  # never started — must not raise
        assert agent.is_active is False


class TestQueueAppendMode:
    async def test_context_update_generates_response_and_completes(self):
        agent = InteractiveSubAgent(
            _config(ContextUpdateMode.QUEUE_APPEND),
            Registry(),
            ScriptedLLM(["hello there, user"]),
        )
        await agent.start()
        try:
            await agent.push_context({"message": "hi"})
            chunks = await _collect_output(agent)
            # A completion chunk is emitted once generation finishes.
            assert any(c.is_complete for c in chunks)
            # The generated text is captured in the return-as-context buffer.
            assert "hello there, user" in agent.get_buffered_output()
        finally:
            await agent.stop()

    async def test_push_context_to_inactive_agent_is_ignored(self):
        agent = InteractiveSubAgent(_config(), Registry(), ScriptedLLM(["x"]))
        # Not started — push is dropped with a warning, no crash.
        await agent.push_context({"message": "hi"})
        assert agent.is_active is False


class TestOutputBuffer:
    async def test_buffered_output_collected_and_cleared(self):
        agent = InteractiveSubAgent(
            _config(ContextUpdateMode.QUEUE_APPEND),
            Registry(),
            ScriptedLLM(["buffered response text"]),
        )
        await agent.start()
        try:
            await agent.push_context({"message": "go"})
            await _collect_output(agent)
            buffered = agent.get_buffered_output()
            assert "buffered response text" in buffered
            # Buffer is cleared after the read.
            assert agent.get_buffered_output() == ""
        finally:
            await agent.stop()


class TestInterruptRestartMode:
    async def test_interrupt_restart_generates_response(self):
        # INTERRUPT_RESTART cancels any in-flight generation and starts a
        # fresh one for the new context.
        agent = InteractiveSubAgent(
            _config(ContextUpdateMode.INTERRUPT_RESTART),
            Registry(),
            ScriptedLLM(["restart response"]),
        )
        await agent.start()
        try:
            await agent.push_context({"message": "go"})
            await _collect_output(agent)
            assert "restart response" in agent.get_buffered_output()
        finally:
            await agent.stop()


class TestFlushReplaceMode:
    async def test_flush_replace_generates_response(self):
        # Regression guard for B-modules-4 (fixed): _run_loop now
        # generates a response for FLUSH_REPLACE updates too (not only
        # QUEUE_APPEND), so a flush-replace context update is acted on.
        # Contract (config.py docstring): FLUSH_REPLACE flushes current
        # output and replaces context immediately — the new context must
        # still produce a response.
        agent = InteractiveSubAgent(
            _config(ContextUpdateMode.FLUSH_REPLACE),
            Registry(),
            ScriptedLLM(["flush response"]),
        )
        await agent.start()
        try:
            await agent.push_context({"message": "go"})
            await _collect_output(agent)
            assert "flush response" in agent.get_buffered_output()
        finally:
            await agent.stop()


class TestContextFormatting:
    def test_message_key_used_directly(self):
        agent = InteractiveSubAgent(_config(), Registry(), ScriptedLLM(["x"]))
        assert agent._format_context_as_message({"message": "hi"}) == "hi"

    def test_input_key_used_when_no_message(self):
        agent = InteractiveSubAgent(_config(), Registry(), ScriptedLLM(["x"]))
        assert agent._format_context_as_message({"input": "typed"}) == "typed"

    def test_text_key_used_when_no_message_or_input(self):
        agent = InteractiveSubAgent(_config(), Registry(), ScriptedLLM(["x"]))
        assert agent._format_context_as_message({"text": "from text"}) == ("from text")

    def test_fallback_renders_key_value_pairs(self):
        agent = InteractiveSubAgent(_config(), Registry(), ScriptedLLM(["x"]))
        rendered = agent._format_context_as_message({"a": 1, "b": 2})
        assert "a: 1" in rendered
        assert "b: 2" in rendered


from kohakuterrarium.modules.tool.base import BaseTool, ToolResult


class _EchoTool(BaseTool):
    """Minimal tool for the interactive tool-call path."""

    def __init__(self):
        super().__init__()
        self.calls: list[dict] = []

    @property
    def tool_name(self):
        return "echo"

    @property
    def description(self):
        return "echoes"

    async def _execute(self, args, **kwargs):
        self.calls.append(args)
        return ToolResult(output=f"echoed {args.get('text', '')}")


class TestInteractiveToolExecution:
    async def test_tool_call_in_generation_is_executed(self):
        from kohakuterrarium.core.registry import Registry as _Reg

        echo = _EchoTool()
        reg = _Reg()
        reg.register_tool(echo)
        # Turn 1 emits a bracket tool call; turn 2 finishes.
        llm = ScriptedLLM(["[/echo]\n@@text=hi\n[echo/]", "tool done, finishing"])
        cfg = SubAgentConfig(
            name="chat",
            interactive=True,
            context_mode=ContextUpdateMode.QUEUE_APPEND,
            tools=["echo"],
            max_turns=3,
        )
        agent = InteractiveSubAgent(cfg, reg, llm, tool_format="bracket")
        await agent.start()
        try:
            await agent.push_context({"message": "use echo"})
            await _collect_output(agent)
            # The tool was actually invoked during the interactive turn.
            assert echo.calls == [{"text": "hi"}]
        finally:
            await agent.stop()


class TestStopDuringGeneration:
    async def test_stop_cancels_in_flight_generation(self):
        import asyncio

        class _SlowLLM:
            model = "slow"

            async def chat(self, messages, *, stream=True, **kwargs):
                yield "first chunk "
                await asyncio.sleep(5)  # would hang without cancellation
                yield "never reached"

        agent = InteractiveSubAgent(
            _config(ContextUpdateMode.QUEUE_APPEND, max_turns=2),
            Registry(),
            _SlowLLM(),
        )
        await agent.start()
        await agent.push_context({"message": "go"})
        await asyncio.sleep(0.1)  # let generation start
        # stop() must cancel the in-flight generation cleanly.
        await asyncio.wait_for(agent.stop(), timeout=2)
        assert agent.is_active is False

    async def test_deactivation_mid_stream_aborts_generation(self):
        # Contract: _generate_response checks self._active on each chunk
        # and raises CancelledError when the agent is deactivated — the
        # generation returns a failed "Cancelled" result.

        class _ChunkyLLM:
            model = "x"

            def __init__(self, agent_ref):
                self._ref = agent_ref

            async def chat(self, messages, *, stream=True, **kwargs):
                yield "chunk one "
                # Deactivate between chunks — the next iteration aborts.
                self._ref[0]._active = False
                yield "chunk two"

        holder: list = []
        agent = InteractiveSubAgent(
            _config(ContextUpdateMode.QUEUE_APPEND, max_turns=2),
            Registry(),
            _ChunkyLLM(holder),
        )
        holder.append(agent)
        await agent.start()
        # Drive one generation directly; deactivation mid-stream aborts it.
        result = await agent._generate_response({"message": "go"})
        assert result.success is False
        assert result.error == "Cancelled"


class TestClearConversation:
    async def test_clear_conversation_keeps_system_message(self):
        agent = InteractiveSubAgent(_config(), Registry(), ScriptedLLM(["x"]))
        await agent.start()
        try:
            agent.conversation.append("user", "a question")
            agent.conversation.append("assistant", "an answer")
            agent.clear_conversation()
            messages = agent.conversation.to_messages()
            # Only the system message survives.
            assert len(messages) == 1
            assert messages[0]["role"] == "system"
        finally:
            await agent.stop()


class TestOutputCallbackErrors:
    async def test_raising_callback_does_not_break_generation(self):
        # A callback that raises must be logged and swallowed — generation
        # continues and the buffer still fills.
        agent = InteractiveSubAgent(
            _config(ContextUpdateMode.QUEUE_APPEND),
            Registry(),
            ScriptedLLM(["resilient output"]),
        )

        def _bad_callback(chunk):
            raise RuntimeError("callback crashed")

        agent.on_output = _bad_callback
        await agent.start()
        try:
            await agent.push_context({"message": "go"})
            # Give the loop time to process despite the raising callback.
            await asyncio.sleep(0.3)
            assert "resilient output" in agent.get_buffered_output()
        finally:
            await agent.stop()


class TestInteractiveOutputDataclass:
    def test_defaults(self):
        out = InteractiveOutput(text="chunk")
        assert out.text == "chunk"
        assert out.is_complete is False
        assert out.context == {}
