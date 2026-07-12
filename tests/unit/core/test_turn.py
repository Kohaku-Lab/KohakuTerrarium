"""Unit tests for the typed turn API (E3) —
:mod:`kohakuterrarium.core.turn` + :mod:`kohakuterrarium.core.agent_turn`.

Pins the headline contracts:

- ``run()`` returns a full :class:`TurnResult`; a failed provider
  RAISES :class:`TurnError` instead of producing a clean empty reply
  (the old ``chat()`` silent-failure).
- ``timeout=`` interrupts the turn (the agent is idle afterwards).
- ``run_stream()`` yields typed events ending in ``TurnEnded``.
- ``attach()`` streams are non-destructive and multi-consumer.
"""

import asyncio

import pytest

from kohakuterrarium.errors import AgentNotRunningError, TurnError, TurnTimeoutError
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config_types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.core.turn import (
    Activity,
    AgentEventStream,
    TextChunk,
    TurnEnded,
)
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry


def _drive_event(delivery_id="d-1", *, corr_key="delivery_id"):
    """A pre-built non-stackable ingress event (a Drive delivery shape)."""
    return TriggerEvent(
        type="drive_ready",
        content="pursue the drive",
        context={corr_key: delivery_id},
        stackable=False,
    )


class _ExplodingLLM(ScriptedLLM):
    """LLM whose chat call raises — the silent-failure reproducer."""

    async def chat(self, *args, **kwargs):
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover - makes this an async generator


def _cfg(tmp_path, name="turn_agent"):
    return AgentConfig(
        name=name,
        system_prompt="You are a test agent.",
        include_hints_in_prompt=False,
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="none"),
    )


async def _build(tmp_path, llm, **kw):
    agent = await Agent.build(_cfg(tmp_path), llm=llm, **kw)
    await agent.start()
    return agent


class TestRun:
    async def test_ok_turn_returns_result(self, tmp_path):
        agent = await _build(tmp_path, ScriptedLLM(["The grade is 87."]))
        try:
            result = await agent.run("grade this")
            assert result.ok
            assert result.status == "ok"
            assert "The grade is 87." in result.text
            assert result.error is None
            assert result.duration_s >= 0
        finally:
            await agent.stop()

    async def test_provider_failure_raises_turn_error(self, tmp_path):
        # THE silent-failure fix: a dead provider used to yield a clean
        # empty chat; run() raises with the real cause.
        agent = await _build(tmp_path, _ExplodingLLM([]))
        try:
            with pytest.raises(TurnError, match="provider exploded"):
                await agent.run("hello")
        finally:
            await agent.stop()

    async def test_raise_on_error_false_returns_status(self, tmp_path):
        agent = await _build(tmp_path, _ExplodingLLM([]))
        try:
            result = await agent.run("hello", raise_on_error=False)
            assert result.status == "error"
            assert "provider exploded" in (result.error or "")
            assert result.text == ""
        finally:
            await agent.stop()

    async def test_timeout_interrupts_and_raises(self, tmp_path):
        slow = ScriptedLLM(
            [ScriptEntry(response="x" * 400, delay_per_chunk=0.05, chunk_size=4)]
        )
        agent = await _build(tmp_path, slow)
        try:
            with pytest.raises(TurnTimeoutError):
                await agent.run("hello", timeout=0.3)
            # The turn was actually unwound — the agent is usable again.
            follow_up = await agent.run("again", raise_on_error=False)
            assert follow_up.status in ("ok", "error")
        finally:
            await agent.stop()

    async def test_timeout_result_without_raise(self, tmp_path):
        slow = ScriptedLLM(
            [ScriptEntry(response="y" * 400, delay_per_chunk=0.05, chunk_size=4)]
        )
        agent = await _build(tmp_path, slow)
        try:
            result = await agent.run("hello", timeout=0.3, raise_on_error=False)
            assert result.status == "timeout"
        finally:
            await agent.stop()

    async def test_not_started_agent_raises_typed(self, tmp_path):
        agent = await Agent.build(_cfg(tmp_path), llm=ScriptedLLM(["x"]))
        with pytest.raises(AgentNotRunningError):
            await agent.run("hello")


class TestRunEvent:
    """``Agent.run_event`` — the additive public ingress the Drive dispatcher
    delivers over (design §5.1-5.2)."""

    async def test_prebuilt_event_runs_and_echoes_correlation(self, tmp_path):
        agent = await _build(tmp_path, ScriptedLLM(["drive acknowledged"]))
        try:
            result = await agent.run_event(_drive_event("d-42"))
            assert result.status == "ok"
            assert "drive acknowledged" in result.text
            # The delivery id survives context -> finalization -> result.
            assert result.correlation_id == "d-42"
        finally:
            await agent.stop()

    async def test_correlation_id_context_fallback(self, tmp_path):
        agent = await _build(tmp_path, ScriptedLLM(["ok"]))
        try:
            result = await agent.run_event(
                _drive_event("c-9", corr_key="correlation_id")
            )
            assert result.correlation_id == "c-9"
        finally:
            await agent.stop()

    async def test_stopped_agent_rejects_not_silently(self, tmp_path):
        # Never started: the event must not run, and the result says so
        # distinctly (status="rejected"), never a hollow ok.
        agent = await Agent.build(_cfg(tmp_path), llm=ScriptedLLM(["never"]))
        result = await agent.run_event(_drive_event("d-x"), raise_on_error=True)
        assert result.status == "rejected"
        assert result.correlation_id == "d-x"
        assert result.text == ""
        # ScriptedLLM was never invoked.
        assert agent.llm.call_count == 0

    async def test_error_turn_raises_when_requested(self, tmp_path):
        agent = await _build(tmp_path, _ExplodingLLM([]))
        try:
            with pytest.raises(TurnError, match="provider exploded"):
                await agent.run_event(_drive_event("d-err"), raise_on_error=True)
            # Without raise_on_error the settled error is returned.
            result = await agent.run_event(_drive_event("d-err2"))
            assert result.status == "error"
            assert result.correlation_id == "d-err2"
        finally:
            await agent.stop()


class TestRunStream:
    async def test_yields_chunks_then_turn_ended(self, tmp_path):
        agent = await _build(tmp_path, ScriptedLLM(["streamed reply"]))
        try:
            events = []
            async for ev in agent.run_stream("hi"):
                events.append(ev)
            assert isinstance(events[-1], TurnEnded)
            assert events[-1].result.ok
            text = "".join(e.text for e in events if isinstance(e, TextChunk))
            assert "streamed reply" in text
        finally:
            await agent.stop()

    async def test_error_surfaces_in_stream_result(self, tmp_path):
        agent = await _build(tmp_path, _ExplodingLLM([]))
        try:
            events = [ev async for ev in agent.run_stream("hi")]
            ended = events[-1]
            assert isinstance(ended, TurnEnded)
            assert ended.result.status == "error"
            assert any(
                isinstance(e, Activity) and e.kind == "processing_error" for e in events
            )
        finally:
            await agent.stop()


class TestAttach:
    async def test_attach_observes_a_run_non_destructively(self, tmp_path):
        agent = await _build(tmp_path, ScriptedLLM(["observed reply"]))
        try:
            async with AgentEventStream(agent) as stream:
                result = await agent.run("hi")
                # run() got the full text even though a stream observes.
                assert "observed reply" in result.text
                seen_text = ""
                while True:
                    ev = await asyncio.wait_for(stream._queue.get(), timeout=1)
                    if isinstance(ev, TextChunk):
                        seen_text += ev.text
                    if "observed reply" in seen_text:
                        break
                assert "observed reply" in seen_text
        finally:
            await agent.stop()

    async def test_close_stops_iteration(self, tmp_path):
        agent = await _build(tmp_path, ScriptedLLM(["x"]))
        try:
            stream = AgentEventStream(agent)
            async with stream:
                pass  # closed on exit
            collected = [ev async for ev in stream]
            assert collected == []
        finally:
            await agent.stop()
