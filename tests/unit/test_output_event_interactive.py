"""Tests for the Phase B interactive bus protocol.

Covers:
- emit_and_wait happy path: reply lands, future resolves
- emit_and_wait timeout sentinel
- submit_reply for unknown event_id triggers supersede broadcast
- first-reply-wins across multiple submitters
- supersede broadcast reaches the on_supersede hook
"""

import asyncio

import pytest

from kohakuterrarium.modules.output.event import (
    ACTION_SUPERSEDED,
    ACTION_TIMEOUT,
    OutputEvent,
    UIReply,
)
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.testing import OutputRecorder


@pytest.mark.asyncio
async def test_emit_and_wait_resolves_on_reply():
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)

    async def deliver_reply():
        # Wait long enough for emit() to register the future.
        await asyncio.sleep(0.05)
        ok = router.submit_reply(UIReply(event_id="ev1", action_id="allow", values={}))
        assert ok is True

    asyncio.create_task(deliver_reply())
    reply = await router.emit_and_wait(
        OutputEvent(
            type="confirm",
            interactive=True,
            id="ev1",
            payload={"prompt": "ok?"},
        ),
        timeout_s=2.0,
    )
    assert reply.event_id == "ev1"
    assert reply.action_id == "allow"
    assert not reply.is_timeout
    assert not reply.is_superseded


@pytest.mark.asyncio
async def test_emit_and_wait_returns_timeout_sentinel():
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)
    reply = await router.emit_and_wait(
        OutputEvent(
            type="confirm",
            interactive=True,
            id="ev_to",
            payload={"prompt": "hi"},
        ),
        timeout_s=0.05,
    )
    assert reply.action_id == ACTION_TIMEOUT
    assert reply.is_timeout
    assert reply.event_id == "ev_to"


@pytest.mark.asyncio
async def test_emit_and_wait_rejects_non_interactive():
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)
    with pytest.raises(ValueError, match="interactive=True"):
        await router.emit_and_wait(
            OutputEvent(type="confirm", id="x", payload={}), timeout_s=0.1
        )


@pytest.mark.asyncio
async def test_emit_and_wait_rejects_missing_id():
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)
    with pytest.raises(ValueError, match="event.id"):
        await router.emit_and_wait(
            OutputEvent(type="confirm", interactive=True, payload={}),
            timeout_s=0.1,
        )


@pytest.mark.asyncio
async def test_first_reply_wins_late_submitter_gets_false():
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)

    async def winner():
        await asyncio.sleep(0.02)
        return router.submit_reply(
            UIReply(event_id="ev_race", action_id="allow", values={})
        )

    async def loser():
        await asyncio.sleep(0.05)
        return router.submit_reply(
            UIReply(event_id="ev_race", action_id="deny", values={})
        )

    winner_task = asyncio.create_task(winner())
    loser_task = asyncio.create_task(loser())

    reply = await router.emit_and_wait(
        OutputEvent(
            type="confirm",
            interactive=True,
            id="ev_race",
            payload={"prompt": "race"},
        ),
        timeout_s=2.0,
    )
    assert reply.action_id == "allow"
    assert await winner_task is True
    assert await loser_task is False


@pytest.mark.asyncio
async def test_unknown_event_id_returns_unknown_status_no_broadcast():
    """A reply for an unknown event id reports ``unknown`` and does
    NOT trigger an ``on_supersede`` broadcast — the reply is just
    stale, not a race loss.
    """

    seen: list[str] = []

    class SuperseedeAware(OutputRecorder):
        def on_supersede(self, event_id: str) -> None:
            seen.append(event_id)

    primary = SuperseedeAware()
    secondary = SuperseedeAware()
    router = OutputRouter(default_output=primary)
    router.add_secondary(secondary)

    accepted, status = router.submit_reply_with_status(
        UIReply(event_id="ghost", action_id="x", values={})
    )
    assert accepted is False
    assert status == "unknown"
    assert seen == []  # no broadcast for unknown event_id


@pytest.mark.asyncio
async def test_success_does_not_broadcast_supersede_to_self():
    """The winning renderer must NOT receive a supersede signal for
    its own reply — only losers in a race get it.
    """

    seen: list[str] = []

    class SuperseedeAware(OutputRecorder):
        def on_supersede(self, event_id: str) -> None:
            seen.append(event_id)

    rec = SuperseedeAware()
    router = OutputRouter(default_output=rec)

    async def deliver_reply():
        await asyncio.sleep(0.02)
        accepted, status = router.submit_reply_with_status(
            UIReply(event_id="ev_solo", action_id="ok", values={})
        )
        assert accepted is True
        assert status == "accepted"

    asyncio.create_task(deliver_reply())
    reply = await router.emit_and_wait(
        OutputEvent(
            type="confirm", interactive=True, id="ev_solo", payload={"prompt": "x"}
        ),
        timeout_s=2.0,
    )
    assert reply.action_id == "ok"
    assert seen == []  # winning renderer never sees its own supersede


@pytest.mark.asyncio
async def test_uireply_is_timeout_and_is_superseded_helpers():
    r1 = UIReply(event_id="x", action_id=ACTION_TIMEOUT)
    assert r1.is_timeout
    assert not r1.is_superseded

    r2 = UIReply(event_id="x", action_id=ACTION_SUPERSEDED)
    assert r2.is_superseded
    assert not r2.is_timeout

    r3 = UIReply(event_id="x", action_id="allow")
    assert not r3.is_timeout
    assert not r3.is_superseded


@pytest.mark.asyncio
async def test_emit_and_wait_uses_event_timeout_s_when_none_passed():
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)
    reply = await router.emit_and_wait(
        OutputEvent(
            type="confirm",
            interactive=True,
            id="ev_default",
            timeout_s=0.05,
            payload={"prompt": "hi"},
        ),
    )
    assert reply.is_timeout


@pytest.mark.asyncio
async def test_outputevent_phase_b_fields_default_correctly():
    """Phase A behaviour preserved: new fields all have defaults."""
    e = OutputEvent(type="text", content="hello")
    assert e.surface == "chat"
    assert e.interactive is False
    assert e.update_target is None
    assert e.timeout_s is None
    assert e.correlation_id is None
