"""Unit tests for :mod:`kohakuterrarium.studio.attach._event_stream`."""

import asyncio

import pytest

from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.studio.attach import _event_stream as es_mod
from kohakuterrarium.studio.attach._event_stream import (
    StreamOutput,
    _parse_detail,
    get_event_log,
)

# ── _parse_detail / get_event_log ────────────────────────────


class TestParseDetail:
    def test_no_prefix(self):
        assert _parse_detail("plain text") == ("unknown", "plain text")

    def test_prefix_with_text(self):
        assert _parse_detail("[bash] running ls") == ("bash", "running ls")

    def test_only_bracket(self):
        # Bracketed-then-closed → returns the bracketed name with empty body.
        assert _parse_detail("[bash]") == ("bash", "")


class TestGetEventLog:
    def test_creates_log_on_demand(self):
        es_mod._event_logs.clear()
        log = get_event_log("key-1")
        assert log == []
        # Subsequent call returns same list.
        assert get_event_log("key-1") is log

    def test_independent_logs_per_key(self):
        es_mod._event_logs.clear()
        a = get_event_log("a")
        b = get_event_log("b")
        a.append("x")
        assert b == []


# ── StreamOutput sync hooks ────────────────────────────────


@pytest.fixture
def _stream():
    q = asyncio.Queue()
    log: list = []
    so = StreamOutput("src", q, log)
    return so, q, log


class TestStreamOutputSync:
    async def test_lifecycle_methods_are_noops(self, _stream):
        so, q, log = _stream
        # start/stop/flush are pure lifecycle no-ops — they must not
        # emit any frame onto the queue or the event log.
        await so.start()
        await so.stop()
        await so.flush()
        assert q.empty()
        assert log == []

    async def test_write_basic(self, _stream):
        so, q, log = _stream
        await so.write("hello")
        msg = q.get_nowait()
        assert msg["type"] == "text"
        assert msg["content"] == "hello"
        assert msg["source"] == "src"
        assert log[0]["type"] == "text"

    async def test_write_stream_empty_ignored(self, _stream):
        so, q, _log = _stream
        await so.write_stream("")
        assert q.empty()

    async def test_write_stream_chunk(self, _stream):
        so, q, _log = _stream
        await so.write_stream("chunk")
        assert q.get_nowait()["content"] == "chunk"

    async def test_processing_markers(self, _stream):
        so, q, _log = _stream
        await so.on_processing_start()
        await so.on_processing_end()
        assert q.get_nowait()["type"] == "processing_start"
        assert q.get_nowait()["type"] == "processing_end"

    def test_on_activity(self, _stream):
        so, q, _log = _stream
        so.on_activity("tool_call", "[bash] ls -la")
        msg = q.get_nowait()
        assert msg["activity_type"] == "tool_call"
        assert msg["name"] == "bash"
        assert msg["detail"] == "ls -la"

    def test_on_activity_with_metadata(self, _stream):
        so, q, _log = _stream
        so.on_activity_with_metadata(
            "tool_call",
            "[bash] ls",
            metadata={"args": ["-la"], "job_id": "j1", "unknown_key": "x"},
        )
        msg = q.get_nowait()
        assert msg["args"] == ["-la"]
        assert msg["job_id"] == "j1"
        # Unknown keys are filtered.
        assert "unknown_key" not in msg

    def test_on_assistant_image(self, _stream):
        so, q, _log = _stream
        so.on_assistant_image(
            "http://x.com/img.png",
            detail="high",
            source_type="tool",
            source_name="dalle",
            revised_prompt="new prompt",
        )
        msg = q.get_nowait()
        assert msg["type"] == "image"
        assert msg["url"] == "http://x.com/img.png"
        assert msg["meta"]["source_type"] == "tool"

    def test_on_assistant_image_no_meta(self, _stream):
        so, q, _log = _stream
        so.on_assistant_image("http://x.com/img.png")
        msg = q.get_nowait()
        assert "meta" not in msg

    def test_on_supersede(self, _stream):
        so, q, _log = _stream
        so.on_supersede("evt-1")
        msg = q.get_nowait()
        assert msg["type"] == "ui_supersede"
        assert msg["event_id"] == "evt-1"


# ── StreamOutput.emit (native event consumer) ────────────────


def _evt(type_, **kw):
    return OutputEvent(type=type_, **kw)


class TestStreamOutputEmit:
    async def test_text_event(self, _stream):
        so, q, _log = _stream
        await so.emit(_evt("text", content="hi"))
        msg = q.get_nowait()
        assert msg["type"] == "text"
        assert msg["content"] == "hi"
        assert msg["source"] == "src"

    async def test_text_event_empty_skipped(self, _stream):
        so, q, _log = _stream
        await so.emit(_evt("text", content=""))
        assert q.empty()

    async def test_processing_events(self, _stream):
        so, q, _log = _stream
        await so.emit(_evt("processing_start"))
        await so.emit(_evt("processing_end"))
        assert q.get_nowait()["type"] == "processing_start"
        assert q.get_nowait()["type"] == "processing_end"

    async def test_user_input_skipped(self, _stream):
        so, q, _log = _stream
        await so.emit(_evt("user_input", content="hi"))
        assert q.empty()

    async def test_assistant_image_event(self, _stream):
        so, q, _log = _stream
        await so.emit(
            _evt(
                "assistant_image",
                payload={
                    "url": "http://x.com",
                    "detail": "auto",
                    "source_type": "model",
                },
            )
        )
        msg = q.get_nowait()
        assert msg["type"] == "image"
        assert msg["url"] == "http://x.com"
        assert msg["detail"] == "auto"
        assert msg["meta"]["source_type"] == "model"

    async def test_resume_batch_skipped(self, _stream):
        so, q, _log = _stream
        await so.emit(_evt("resume_batch"))
        assert q.empty()

    @pytest.mark.parametrize(
        "kind",
        ["ask_text", "confirm", "selection", "progress", "notification", "card"],
    )
    async def test_phase_b_kinds_passed_through(self, kind, _stream):
        so, q, _log = _stream
        await so.emit(
            _evt(
                kind,
                id="evt-1",
                interactive=True,
                surface="inline",
                payload={"prompt": "?"},
                update_target=None,
                timeout_s=10,
            )
        )
        msg = q.get_nowait()
        assert msg["type"] == kind
        assert msg["event_id"] == "evt-1"
        assert msg["timeout_s"] == 10

    async def test_ui_supersede(self, _stream):
        so, q, _log = _stream
        await so.emit(_evt("ui_supersede", payload={"event_id": "e1"}))
        msg = q.get_nowait()
        assert msg["type"] == "ui_supersede"
        assert msg["event_id"] == "e1"

    async def test_unknown_kind_no_metadata(self, _stream):
        so, q, _log = _stream
        await so.emit(_evt("custom_kind", content="[name] info"))
        msg = q.get_nowait()
        assert msg["type"] == "activity"
        assert msg["activity_type"] == "custom_kind"

    async def test_unknown_kind_with_metadata(self, _stream):
        so, q, _log = _stream
        await so.emit(
            _evt("custom_kind", content="[name] info", payload={"args": ["x"]})
        )
        msg = q.get_nowait()
        assert msg["args"] == ["x"]
