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
import json

import httpx

import pytest

from types import SimpleNamespace

from kohakuterrarium.errors import (
    AgentNotRunningError,
    ConflictError,
    TurnError,
    TurnTimeoutError,
)
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config_types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.core.turn import (
    Activity,
    AgentEventStream,
    TextChunk,
    TurnEnded,
)
from kohakuterrarium.llm.message import ImagePart, TextPart
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry
from kohakuterrarium.terrarium.service import _completed_branch_result
from kohakuterrarium.terrarium.remote_service import _branch_mutation_result


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
    @pytest.mark.real_llm_provider
    async def test_accepted_missing_media_records_failed_turn_without_transport(
        self, tmp_path
    ):
        requests = []

        async def respond(request):
            requests.append(json.loads(request.content))
            chunk = {
                "id": "reply",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "test",
                "choices": [
                    {"index": 0, "delta": {"content": "ok"}, "finish_reason": None}
                ],
            }
            return httpx.Response(
                200,
                text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )

        provider = OpenAIProvider(api_key="test", model="test")
        previous = provider._client
        provider._client = previous.with_options(
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        )
        await previous.close()
        agent = await _build(tmp_path, provider)
        store = SessionStore(tmp_path / "media-error.kohakutr")
        agent.attach_session_store(store)
        parts = [
            TextPart(text="inspect"),
            ImagePart(url=(tmp_path / "invalid_image.png").as_uri()),
        ]
        try:
            result = await agent.run_event(
                TriggerEvent(type="user_input", content=parts), raise_on_error=False
            )
            assert result.status == "error"
            assert "Required local media is unavailable" in result.error
            assert requests == []
            recorded = store.get_events(agent.config.name)
            user = next(e for e in recorded if e["type"] == "user_message")
            assert user["content"] == [p.to_dict() for p in parts]
            assert any(e["type"] == "processing_error" for e in recorded)
            assert not any(e["type"] == "text_chunk" for e in recorded)
            assert agent.controller.conversation.get_messages()[-1].has_images()
            await agent.edit_and_rerun(-1, parts, turn_index=1, request_id="edit-media")
            outcome = _completed_branch_result(agent, "edit-media")
            assert outcome["status"] == "completed"
            assert "Required local media is unavailable" in outcome["execution_error"]
            assert (
                _branch_mutation_result(outcome)["execution_error"]
                == outcome["execution_error"]
            )
            edited_users = [
                e
                for e in store.get_events(agent.config.name)
                if e["type"] == "user_message"
                and e["branch_id"] == outcome["branch_id"]
            ]
            assert edited_users[0]["request_id"] == "edit-media"
            assert edited_users[0]["content"] == [p.to_dict() for p in parts]
            assert requests == []
            await agent.edit_and_rerun(
                -1, "valid after clear", turn_index=1, request_id="edit-good"
            )
            success = _completed_branch_result(agent, "edit-good")
            assert success.get("execution_error") is None
            assert len(requests) == 1
            assert "invalid_image.png" not in json.dumps(requests[0])
            assert store.get_events(agent.config.name)[: len(recorded)] == recorded
        finally:
            await agent.stop()
            await provider.close()
            store.close()

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

    async def test_user_interrupt_marker_reaches_turn_result(self, tmp_path):
        agent = await _build(tmp_path, ScriptedLLM(["ok"]))
        try:
            event = _drive_event("d-stop")
            event.context["interrupted_by_user"] = True
            result = await agent.run_event(event)
            assert result.interrupted_by_user is True
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


class TestAcceptedBranchExecutionError:
    """Accepted edit/rerun keeps the branch and reports execution_error."""

    def test_request_id_free_completed_result_omits_unbound_error(self):
        agent = SimpleNamespace(
            _parent_branch_path=(),
            _branch_execution_error=(None, "stale boom"),
            _turn_index=1,
            _branch_id=2,
        )
        out = _completed_branch_result(agent, None)
        assert out["status"] == "completed"
        assert "execution_error" not in out

    async def test_regenerate_turn_index_binds_request_id_on_provider_error(
        self, tmp_path
    ):
        agent = await _build(tmp_path, _ExplodingLLM([]))
        store = SessionStore(tmp_path / "regen-error.kohakutr")
        agent.attach_session_store(store)
        try:
            result = await agent.run("hello", raise_on_error=False)
            assert result.status == "error"
            await agent.regenerate_last_response(turn_index=1, request_id="retry-1")
            outcome = _completed_branch_result(agent, "retry-1")
            assert outcome["status"] == "completed"
            assert "provider exploded" in outcome["execution_error"]
            assert outcome["request_id"] == "retry-1"
            assert _completed_branch_result(agent, None).get("execution_error") is None
            agent.controller.llm = ScriptedLLM(["recovered"])
            await agent.regenerate_last_response(turn_index=1, request_id="retry-good")
            success = _completed_branch_result(agent, "retry-good")
            assert success.get("execution_error") is None
        finally:
            await agent.stop()
            store.close()

    async def test_paused_regenerate_still_rejects_before_accepting(self, tmp_path):
        agent = await _build(tmp_path, ScriptedLLM(["ok"]))
        store = SessionStore(tmp_path / "paused-regen.kohakutr")
        agent.attach_session_store(store)
        try:
            await agent.run("hello")
            branch_before = (agent._turn_index, agent._branch_id)
            agent._paused = True
            with pytest.raises(ConflictError, match="paused"):
                await agent.regenerate_last_response(
                    turn_index=1, request_id="retry-paused"
                )
            assert (agent._turn_index, agent._branch_id) == branch_before
        finally:
            await agent.stop()
            store.close()

    @pytest.mark.parametrize(
        "seam, message",
        [
            ("_begin_batch", "begin exploded"),
            ("push_event", "push exploded"),
            ("_finalize_processing", "finalize exploded"),
        ],
    )
    async def test_lifecycle_failure_binds_accepted_rerun_request(
        self, tmp_path, seam, message
    ):
        agent = await _build(tmp_path, ScriptedLLM(["ok", "again", "recovered"]))
        store = SessionStore(tmp_path / f"{seam}-error.kohakutr")
        agent.attach_session_store(store)
        try:
            assert (await agent.run("hello")).ok
            accepted_turn = agent._turn_index
            accepted_branch = agent._branch_id
            if seam == "push_event":
                original = agent.controller.push_event

                async def boom(event):
                    raise RuntimeError(message)

                agent.controller.push_event = boom
            else:
                original = getattr(agent, seam)

                async def boom(*args, **kwargs):
                    raise RuntimeError(message)

                setattr(agent, seam, boom)
            await agent.edit_and_rerun(
                -1, "hello again", turn_index=1, request_id="edit-life"
            )
            outcome = _completed_branch_result(agent, "edit-life")
            assert outcome["status"] == "completed"
            assert message in outcome["execution_error"]
            assert outcome["turn_index"] == accepted_turn
            assert outcome["branch_id"] > accepted_branch
            if seam == "push_event":
                agent.controller.push_event = original
            else:
                setattr(agent, seam, original)
            await agent.edit_and_rerun(
                -1, "hello recovered", turn_index=1, request_id="edit-ok"
            )
            success = _completed_branch_result(agent, "edit-ok")
            assert success.get("execution_error") is None
        finally:
            await agent.stop()
            store.close()
