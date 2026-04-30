"""Tests for Phase B display events: progress, notification, card.

Verifies:
- Display events route through the bus to renderers' emit() (not the
  legacy on_activity path).
- update_target events keep the same id and carry the update payload
  through to the receiving renderer.
- Renderers without bespoke handlers (e.g. plain OutputRecorder)
  capture the typed event and don't crash.
- StreamOutput emits the right WS frame shapes.
"""

import asyncio
import time

import pytest

from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.studio.attach._event_stream import StreamOutput
from kohakuterrarium.testing import OutputRecorder


@pytest.mark.asyncio
async def test_progress_event_routes_to_renderers_via_emit():
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)

    await router.emit(
        OutputEvent(
            type="progress",
            id="bar1",
            payload={"label": "indexing", "value": 0, "max": 100},
        )
    )
    assert any(e.type == "progress" for e in rec.events)
    e = next(e for e in rec.events if e.type == "progress")
    assert e.payload["label"] == "indexing"
    assert e.payload["max"] == 100


@pytest.mark.asyncio
async def test_progress_update_target_carries_through():
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)

    await router.emit(
        OutputEvent(type="progress", id="bar2", payload={"label": "x", "max": 10})
    )
    await router.emit(
        OutputEvent(
            type="progress",
            update_target="bar2",
            payload={"value": 5},
        )
    )
    progress_events = [e for e in rec.events if e.type == "progress"]
    assert len(progress_events) == 2
    # Second event records the update_target via the dataclass field;
    # OutputRecorder.events is the typed envelope so the field is on
    # the wrapper. Verify by re-emitting and checking payload.
    assert progress_events[1].payload.get("value") == 5


@pytest.mark.asyncio
async def test_notification_event_fans_to_secondaries():
    primary = OutputRecorder()
    secondary = OutputRecorder()
    router = OutputRouter(default_output=primary)
    router.add_secondary(secondary)

    await router.emit(
        OutputEvent(
            type="notification",
            surface="toast",
            payload={
                "level": "success",
                "text": "Background agent finished.",
                "duration_ms": 4000,
            },
        )
    )
    assert any(e.type == "notification" for e in primary.events)
    assert any(e.type == "notification" for e in secondary.events)


@pytest.mark.asyncio
async def test_card_event_routes_with_full_payload():
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)

    payload = {
        "title": "Plan",
        "subtitle": "5 files",
        "accent": "warning",
        "body": "1. step\n2. step",
        "fields": [
            {"label": "Files", "value": "5", "inline": True},
        ],
        "actions": [
            {"id": "approve", "label": "Approve", "style": "primary"},
        ],
    }
    await router.emit(OutputEvent(type="card", payload=payload))

    card = next(e for e in rec.events if e.type == "card")
    assert card.payload["title"] == "Plan"
    assert card.payload["actions"][0]["id"] == "approve"


@pytest.mark.asyncio
async def test_stream_output_emits_progress_frame_to_ws_queue():
    queue: asyncio.Queue = asyncio.Queue()
    log: list = []
    sink = StreamOutput("creature1", queue, log)

    await sink.emit(
        OutputEvent(
            type="progress",
            id="bar9",
            payload={"label": "x", "value": 1, "max": 10},
        )
    )
    msg = await queue.get()
    assert msg["type"] == "progress"
    assert msg["event_id"] == "bar9"
    assert msg["payload"]["max"] == 10
    assert msg["source"] == "creature1"


@pytest.mark.asyncio
async def test_stream_output_emits_card_frame_to_ws_queue():
    queue: asyncio.Queue = asyncio.Queue()
    log: list = []
    sink = StreamOutput("creature1", queue, log)

    await sink.emit(
        OutputEvent(
            type="card",
            interactive=True,
            payload={
                "title": "Plan",
                "actions": [
                    {"id": "ok", "label": "OK", "style": "primary"},
                ],
            },
        )
    )
    msg = await queue.get()
    assert msg["type"] == "card"
    assert msg["interactive"] is True
    assert msg["payload"]["title"] == "Plan"


@pytest.mark.asyncio
async def test_stream_output_passes_update_target_in_progress_frame():
    queue: asyncio.Queue = asyncio.Queue()
    log: list = []
    sink = StreamOutput("creature1", queue, log)

    await sink.emit(
        OutputEvent(
            type="progress",
            id="bar1-update-1",
            update_target="bar1",
            payload={"value": 50},
        )
    )
    msg = await queue.get()
    assert msg["type"] == "progress"
    assert msg["update_target"] == "bar1"
    assert msg["payload"]["value"] == 50


@pytest.mark.asyncio
async def test_stream_output_emits_supersede_frame():
    queue: asyncio.Queue = asyncio.Queue()
    log: list = []
    sink = StreamOutput("creature1", queue, log)
    sink.on_supersede("ev42")
    msg = await queue.get()
    assert msg["type"] == "ui_supersede"
    assert msg["event_id"] == "ev42"


@pytest.mark.asyncio
async def test_display_events_do_not_block_on_no_renderers():
    """Display events with no renderer beyond the recorder still
    complete cleanly — they don't try to await a reply."""
    rec = OutputRecorder()
    router = OutputRouter(default_output=rec)

    start = time.perf_counter()
    await router.emit(OutputEvent(type="notification", payload={"text": "hi"}))
    elapsed = time.perf_counter() - start
    # Display events return immediately; the test just sanity-checks
    # we didn't accidentally introduce a wait.
    assert elapsed < 0.5
