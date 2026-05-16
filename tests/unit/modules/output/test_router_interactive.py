"""Unit tests for :mod:`kohakuterrarium.modules.output.router_interactive`.

Behavior-first: the Phase B interactive bus — emit_and_wait registers a
Future, submit_reply resolves it, timeout yields a __timeout__ reply,
races are arbitrated first-reply-wins, and superseded renderers get the
on_supersede broadcast.
"""

import asyncio

import pytest

from kohakuterrarium.modules.output.event import (
    ACTION_TIMEOUT,
    OutputEvent,
    UIReply,
)
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.testing.output import OutputRecorder


class _SupersedeSpy(OutputRecorder):
    def __init__(self):
        super().__init__()
        self.superseded: list[str] = []

    def on_supersede(self, event_id: str) -> None:
        self.superseded.append(event_id)


def _interactive_event(event_id="evt-1", **kwargs):
    return OutputEvent(type="confirm", id=event_id, interactive=True, **kwargs)


class TestEmitAndWaitValidation:
    async def test_rejects_non_interactive_event(self):
        router = OutputRouter(OutputRecorder())
        with pytest.raises(ValueError, match="interactive=True"):
            await router.emit_and_wait(OutputEvent(type="confirm", id="e1"))

    async def test_rejects_event_without_id(self):
        router = OutputRouter(OutputRecorder())
        bad = OutputEvent(type="confirm", interactive=True)
        with pytest.raises(ValueError, match="non-empty event.id"):
            await router.emit_and_wait(bad)


class TestReplyResolution:
    async def test_submit_reply_resolves_the_awaiter(self):
        router = OutputRouter(OutputRecorder())
        event = _interactive_event()

        async def _reply_soon():
            await asyncio.sleep(0.05)
            accepted = router.submit_reply(
                UIReply(event_id="evt-1", action_id="yes", values={"k": 1})
            )
            assert accepted is True

        asyncio.create_task(_reply_soon())
        reply = await asyncio.wait_for(router.emit_and_wait(event), timeout=2)
        assert reply.action_id == "yes"
        assert reply.values == {"k": 1}
        # The pending slot is released after resolution.
        assert "evt-1" not in router._pending_replies

    async def test_timeout_yields_timeout_reply(self):
        router = OutputRouter(OutputRecorder())
        event = _interactive_event(timeout_s=0.05)
        reply = await asyncio.wait_for(router.emit_and_wait(event), timeout=2)
        assert reply.action_id == ACTION_TIMEOUT
        assert reply.is_timeout is True
        # Slot released even on the timeout path.
        assert "evt-1" not in router._pending_replies

    async def test_per_call_timeout_overrides_event_timeout(self):
        router = OutputRouter(OutputRecorder())
        # event.timeout_s would wait long; the per-call arg wins.
        event = _interactive_event(timeout_s=999)
        reply = await asyncio.wait_for(
            router.emit_and_wait(event, timeout_s=0.05), timeout=2
        )
        assert reply.is_timeout is True


class TestSubmitReplyStatuses:
    async def test_unknown_event_id_returns_unknown(self):
        router = OutputRouter(OutputRecorder())
        accepted, status = router.submit_reply_with_status(
            UIReply(event_id="never-emitted", action_id="yes")
        )
        assert accepted is False
        assert status == "unknown"

    async def test_second_reply_after_race_is_superseded(self):
        # Two renderers reply to the same event. The first wins; the second
        # sees 'superseded' and the supersede hook fires on attached outputs.
        spy = _SupersedeSpy()
        router = OutputRouter(spy)
        event = _interactive_event()

        async def _drive():
            await asyncio.sleep(0.05)
            # First reply wins.
            first = router.submit_reply_with_status(
                UIReply(event_id="evt-1", action_id="first")
            )
            assert first == (True, "accepted")

        asyncio.create_task(_drive())
        reply = await asyncio.wait_for(router.emit_and_wait(event), timeout=2)
        assert reply.action_id == "first"
        # Now a late renderer tries to reply to the same (already popped) id.
        # Re-register a *done* future to exercise the superseded branch.
        loop = asyncio.get_event_loop()
        done_future = loop.create_future()
        done_future.set_result(reply)
        router._pending_replies["evt-1"] = done_future
        accepted, status = router.submit_reply_with_status(
            UIReply(event_id="evt-1", action_id="late")
        )
        assert accepted is False
        assert status == "superseded"
        # Supersede broadcast reached the output's on_supersede hook.
        assert "evt-1" in spy.superseded


class TestEmitFailureCleansUp:
    async def test_emit_raising_releases_the_pending_slot(self):
        # Contract: if emit() raises while fanning the interactive event,
        # emit_and_wait must pop the pending Future and re-raise — leaving
        # no orphaned slot behind.
        class _BoomRouter(OutputRouter):
            async def emit(self, event):
                raise RuntimeError("emit exploded")

        router = _BoomRouter(OutputRecorder())
        event = _interactive_event(event_id="evt-boom")
        with pytest.raises(RuntimeError, match="emit exploded"):
            await router.emit_and_wait(event)
        assert "evt-boom" not in router._pending_replies


class TestBroadcastSupersede:
    def test_broadcast_skips_outputs_without_hook(self):
        # An output with no on_supersede must not raise — broadcast is
        # purely advisory.
        router = OutputRouter(OutputRecorder())
        router._broadcast_supersede("evt-x")  # must not raise
