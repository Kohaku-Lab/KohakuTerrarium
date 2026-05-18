"""Tests for :mod:`builtins.cli_rich.multiplex`."""

import pytest

from kohakuterrarium.builtins.cli_rich.multiplex import MultiplexedRichOutput
from kohakuterrarium.modules.output.event import OutputEvent


class _Recorder:
    """Capture every handler dispatch for assertions."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    async def __call__(self, creature_id: str, kind: str, payload: dict):
        self.calls.append((creature_id, kind, payload))


class TestText:
    @pytest.mark.asyncio
    async def test_write_stamps_creature_id(self):
        rec = _Recorder()
        sink = MultiplexedRichOutput(rec, "alice")
        await sink.write("hello")
        assert rec.calls == [("alice", "text", {"text": "hello"})]

    @pytest.mark.asyncio
    async def test_empty_text_is_dropped(self):
        rec = _Recorder()
        sink = MultiplexedRichOutput(rec, "alice")
        await sink.write("")
        await sink.write_stream("")
        assert rec.calls == []

    @pytest.mark.asyncio
    async def test_write_stream_same_shape_as_write(self):
        rec = _Recorder()
        sink = MultiplexedRichOutput(rec, "alice")
        await sink.write_stream("chunk")
        assert rec.calls == [("alice", "text", {"text": "chunk"})]


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_processing_start_and_end(self):
        rec = _Recorder()
        sink = MultiplexedRichOutput(rec, "alice")
        await sink.on_processing_start()
        await sink.on_processing_end()
        assert [c[1] for c in rec.calls] == ["processing_start", "processing_end"]


class TestEmit:
    @pytest.mark.asyncio
    async def test_emit_forwards_full_event_object(self):
        rec = _Recorder()
        sink = MultiplexedRichOutput(rec, "alice")
        ev = OutputEvent(type="text", content="hi")
        await sink.emit(ev)
        assert rec.calls == [("alice", "emit", {"event": ev})]


class TestSeparation:
    @pytest.mark.asyncio
    async def test_two_creatures_route_to_distinct_ids(self):
        rec = _Recorder()
        a = MultiplexedRichOutput(rec, "alice")
        b = MultiplexedRichOutput(rec, "bob")
        await a.write("from-a")
        await b.write("from-b")
        await a.on_processing_end()
        ids = [c[0] for c in rec.calls]
        assert ids == ["alice", "bob", "alice"]


class TestHandlerExceptionsAreSwallowed:
    @pytest.mark.asyncio
    async def test_failing_handler_does_not_crash_sink(self):
        async def boom(_cid, _kind, _payload):
            raise RuntimeError("simulated handler failure")

        sink = MultiplexedRichOutput(boom, "alice")
        # Must not raise — the production sink logs and swallows so
        # one buggy creature's render bug can't take the engine down.
        await sink.write("hi")
        await sink.on_processing_start()
        await sink.on_processing_end()
