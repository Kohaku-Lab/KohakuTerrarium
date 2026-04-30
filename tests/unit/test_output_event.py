"""Tests for OutputEvent + the BaseOutputModule.emit() default forwarder.

These tests pin the Phase A invariant: emit() with a typed event
produces byte-identical effects to calling the legacy hook directly.
"""

import pytest

from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.testing import OutputRecorder


@pytest.mark.asyncio
async def test_emit_text_forwards_to_write_stream():
    rec = OutputRecorder()
    await rec.emit(OutputEvent(type="text", content="hello"))
    assert rec.streams == ["hello"]
    assert [e.type for e in rec.events] == ["text"]


@pytest.mark.asyncio
async def test_emit_processing_lifecycle_forwards_to_hooks():
    rec = OutputRecorder()
    await rec.emit(OutputEvent(type="processing_start"))
    await rec.emit(OutputEvent(type="processing_end"))
    assert rec.processing_starts == 1
    assert rec.processing_ends == 1
    assert [e.type for e in rec.events] == ["processing_start", "processing_end"]


@pytest.mark.asyncio
async def test_emit_activity_forwards_to_on_activity():
    rec = OutputRecorder()
    await rec.emit(
        OutputEvent(
            type="tool_start",
            content="[bash[abc]] command=ls",
            payload={"job_id": "abc", "args": {"command": "ls"}},
        )
    )
    assert rec.activity_types() == ["tool_start"]
    assert rec.activities[0].detail == "[bash[abc]] command=ls"
    assert rec.events[0].type == "tool_start"
    assert rec.events[0].payload["job_id"] == "abc"


@pytest.mark.asyncio
async def test_router_emit_text_routes_to_default_and_secondaries():
    primary = OutputRecorder()
    secondary = OutputRecorder()
    router = OutputRouter(default_output=primary)
    router.add_secondary(secondary)
    await router.emit(OutputEvent(type="text", content="streamed"))
    assert primary.streams == ["streamed"]
    assert secondary.streams == ["streamed"]


@pytest.mark.asyncio
async def test_router_emit_activity_routes_to_default_and_secondaries():
    primary = OutputRecorder()
    secondary = OutputRecorder()
    router = OutputRouter(default_output=primary)
    router.add_secondary(secondary)
    await router.emit(
        OutputEvent(
            type="tool_done",
            content="[bash[abc]] ok",
            payload={"job_id": "abc", "result": "ok"},
        )
    )
    assert primary.activity_types() == ["tool_done"]
    assert secondary.activity_types() == ["tool_done"]


@pytest.mark.asyncio
async def test_notify_activity_and_emit_produce_same_effect():
    """Both entry points converge on the same activity dispatch."""
    via_emit = OutputRecorder()
    via_notify = OutputRecorder()
    router_a = OutputRouter(default_output=via_emit)
    router_b = OutputRouter(default_output=via_notify)
    payload = {"job_id": "xyz", "args": {"x": 1}}
    await router_a.emit(
        OutputEvent(type="tool_start", content="[t[xyz]] x=1", payload=payload)
    )
    router_b.notify_activity("tool_start", "[t[xyz]] x=1", payload)
    assert via_emit.activity_types() == via_notify.activity_types() == ["tool_start"]
    assert via_emit.activities[0].detail == via_notify.activities[0].detail


@pytest.mark.asyncio
async def test_emit_processing_start_via_router_fans_to_secondaries():
    primary = OutputRecorder()
    secondary = OutputRecorder()
    router = OutputRouter(default_output=primary)
    router.add_secondary(secondary)
    await router.emit(OutputEvent(type="processing_start"))
    assert primary.processing_starts == 1
    assert secondary.processing_starts == 1


@pytest.mark.asyncio
async def test_emit_resume_batch_forwards_to_on_resume():
    """Default forwarding: resume_batch invokes on_resume with the events list."""

    class _Capture(OutputRecorder):
        def __init__(self):
            super().__init__()
            self.resume_calls: list[list[dict]] = []

        async def on_resume(self, events):
            self.resume_calls.append(events)

    rec = _Capture()
    payload_events = [{"type": "user_input", "content": "hi"}]
    await rec.emit(OutputEvent(type="resume_batch", payload={"events": payload_events}))
    assert rec.resume_calls == [payload_events]


@pytest.mark.asyncio
async def test_emit_unknown_type_falls_back_to_on_activity():
    rec = OutputRecorder()
    await rec.emit(OutputEvent(type="some_new_kind", content="detail"))
    assert rec.activity_types() == ["some_new_kind"]
    assert rec.activities[0].detail == "detail"
