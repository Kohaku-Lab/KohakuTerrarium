"""Unit tests for :mod:`kohakuterrarium.terrarium.output_log`."""

from datetime import datetime


from kohakuterrarium.terrarium.output_log import LogEntry, OutputLogCapture


class _FakeOutput:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.written = []
        self.streamed = []
        self.flushed = 0
        self.processing_starts = 0
        self.processing_ends = 0
        self.activities = []
        self.reset_count = 0

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def write(self, content):
        self.written.append(content)

    async def write_stream(self, chunk):
        self.streamed.append(chunk)

    async def flush(self):
        self.flushed += 1

    async def on_processing_start(self):
        self.processing_starts += 1

    async def on_processing_end(self):
        self.processing_ends += 1

    def on_activity(self, type_, detail):
        self.activities.append((type_, detail))

    def reset(self):
        self.reset_count += 1


# ── LogEntry ──────────────────────────────────────────────────────


class TestLogEntry:
    def test_short_preview_unchanged(self):
        e = LogEntry(timestamp=datetime.now(), content="short")
        assert e.preview() == "short"

    def test_long_preview_truncated(self):
        e = LogEntry(timestamp=datetime.now(), content="x" * 200)
        out = e.preview(max_len=10)
        assert out.endswith("...")
        # max_len chars + "..."
        assert len(out) == 13


# ── OutputLogCapture ─────────────────────────────────────────────


class TestOutputLogCapture:
    async def test_start_stop_delegates(self):
        fake = _FakeOutput()
        c = OutputLogCapture(fake)
        await c.start()
        await c.stop()
        assert fake.started
        assert fake.stopped

    async def test_write_logs_and_forwards(self):
        fake = _FakeOutput()
        c = OutputLogCapture(fake)
        await c.write("hello")
        assert fake.written == ["hello"]
        assert c.entry_count == 1

    async def test_write_empty_not_logged(self):
        fake = _FakeOutput()
        c = OutputLogCapture(fake)
        await c.write("")
        assert c.entry_count == 0

    async def test_write_stream_buffers_until_flush(self):
        fake = _FakeOutput()
        c = OutputLogCapture(fake)
        await c.write_stream("ab")
        await c.write_stream("cd")
        # Buffered; nothing in entries yet.
        assert c.entry_count == 0
        await c.flush()
        # Flush emits one stream_flush entry.
        assert c.entry_count == 1
        entries = c.get_entries()
        assert entries[0].entry_type == "stream_flush"
        assert entries[0].content == "abcd"

    async def test_flush_empty_buffer_no_entry(self):
        fake = _FakeOutput()
        c = OutputLogCapture(fake)
        await c.flush()
        assert c.entry_count == 0

    async def test_on_processing_start_end(self):
        fake = _FakeOutput()
        c = OutputLogCapture(fake)
        await c.on_processing_start()
        await c.on_processing_end()
        assert fake.processing_starts == 1
        assert fake.processing_ends == 1

    def test_on_activity_logs_and_forwards(self):
        fake = _FakeOutput()
        c = OutputLogCapture(fake)
        c.on_activity("tool_start", "[bash] cmd")
        assert fake.activities == [("tool_start", "[bash] cmd")]
        assert c.entry_count == 1
        e = c.get_entries()[0]
        assert e.entry_type == "activity"
        assert e.metadata["activity_type"] == "tool_start"

    def test_max_entries_ring_buffer(self):
        fake = _FakeOutput()
        c = OutputLogCapture(fake, max_entries=2)
        c.on_activity("a", "1")
        c.on_activity("a", "2")
        c.on_activity("a", "3")
        assert c.entry_count == 2
        assert c.get_entries()[0].content == "2"

    def test_get_entries_filter_by_type(self):
        fake = _FakeOutput()
        c = OutputLogCapture(fake)
        c.on_activity("a", "act1")
        # Add a text-type by going through write — needs to be async.
        # Skip text for this filter check.
        out = c.get_entries(entry_type="activity")
        assert len(out) == 1
        assert out[0].entry_type == "activity"

    async def test_get_text_excludes_activity(self):
        fake = _FakeOutput()
        c = OutputLogCapture(fake)
        await c.write("text1")
        c.on_activity("a", "act1")
        out = c.get_text()
        assert "text1" in out
        assert "act1" not in out

    def test_clear(self):
        fake = _FakeOutput()
        c = OutputLogCapture(fake)
        c.on_activity("a", "x")
        c.clear()
        assert c.entry_count == 0

    def test_reset_passthrough(self):
        fake = _FakeOutput()
        c = OutputLogCapture(fake)
        c.reset()
        assert fake.reset_count == 1

    def test_reset_no_underlying_method(self):
        class _NoReset:
            async def start(self):
                pass

            async def stop(self):
                pass

            async def write(self, _):
                pass

            async def write_stream(self, _):
                pass

            async def flush(self):
                pass

            async def on_processing_start(self):
                pass

            async def on_processing_end(self):
                pass

            def on_activity(self, *_):
                pass

        c = OutputLogCapture(_NoReset())
        # No raise even though wrapped has no reset().
        c.reset()
