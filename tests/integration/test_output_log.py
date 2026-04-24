"""Integration tests for output log capture system."""

from pathlib import Path

from kohakuterrarium.terrarium.config import CreatureConfig
from kohakuterrarium.terrarium.creature import CreatureHandle
from kohakuterrarium.terrarium.output_log import LogEntry, OutputLogCapture
from kohakuterrarium.testing import OutputRecorder


class TestOutputLogCapture:
    """Tests for OutputLogCapture tee wrapper."""

    async def test_write_logged(self):
        """write() goes to wrapped output AND is logged."""
        recorder = OutputRecorder()
        capture = OutputLogCapture(recorder, max_entries=50)

        await capture.write("hello world")

        assert recorder.writes == ["hello world"]
        assert capture.entry_count == 1
        entry = capture.get_entries()[0]
        assert entry.content == "hello world"
        assert entry.entry_type == "text"

    async def test_stream_logged_on_flush(self):
        """Streamed chunks are accumulated and logged on flush."""
        recorder = OutputRecorder()
        capture = OutputLogCapture(recorder, max_entries=50)

        await capture.write_stream("chunk1")
        await capture.write_stream("chunk2")
        assert capture.entry_count == 0  # not logged yet

        await capture.flush()

        assert recorder.streams == ["chunk1", "chunk2"]
        assert capture.entry_count == 1
        entry = capture.get_entries()[0]
        assert entry.content == "chunk1chunk2"
        assert entry.entry_type == "stream_flush"

    async def test_activity_logged(self):
        """on_activity() is forwarded and logged with metadata."""
        recorder = OutputRecorder()
        capture = OutputLogCapture(recorder, max_entries=50)

        capture.on_activity("tool_start", "[bash] running ls")

        assert len(recorder.activities) == 1
        assert recorder.activities[0].activity_type == "tool_start"
        assert capture.entry_count == 1
        entry = capture.get_entries()[0]
        assert entry.entry_type == "activity"
        assert entry.content == "[bash] running ls"
        assert entry.metadata == {"activity_type": "tool_start"}

    async def test_ring_buffer_max(self):
        """Old entries are evicted when max_entries is exceeded."""
        recorder = OutputRecorder()
        capture = OutputLogCapture(recorder, max_entries=5)

        for i in range(10):
            await capture.write(f"msg_{i}")

        assert capture.entry_count == 5
        entries = capture.get_entries()
        assert entries[0].content == "msg_5"
        assert entries[-1].content == "msg_9"

    async def test_get_entries_filter(self):
        """Filter by entry_type returns only matching entries."""
        recorder = OutputRecorder()
        capture = OutputLogCapture(recorder, max_entries=50)

        await capture.write("text1")
        capture.on_activity("tool_start", "doing stuff")
        await capture.write("text2")

        text_entries = capture.get_entries(entry_type="text")
        assert len(text_entries) == 2
        assert text_entries[0].content == "text1"
        assert text_entries[1].content == "text2"

        activity_entries = capture.get_entries(entry_type="activity")
        assert len(activity_entries) == 1
        assert activity_entries[0].content == "doing stuff"

    async def test_get_text(self):
        """get_text returns only text/stream entries, not activity."""
        recorder = OutputRecorder()
        capture = OutputLogCapture(recorder, max_entries=50)

        await capture.write("line1")
        capture.on_activity("tool_done", "done")
        await capture.write_stream("stream_")
        await capture.write_stream("data")
        await capture.flush()

        text = capture.get_text(last_n=10)
        assert "line1" in text
        assert "stream_data" in text
        assert "done" not in text

    async def test_wrapped_receives_all(self):
        """Wrapped output module receives all calls."""
        recorder = OutputRecorder()
        capture = OutputLogCapture(recorder, max_entries=50)

        await capture.start()
        await capture.on_processing_start()
        await capture.write("full")
        await capture.write_stream("part")
        await capture.flush()
        await capture.on_processing_end()
        capture.on_activity("tool_start", "info")
        await capture.stop()

        assert recorder.writes == ["full"]
        assert recorder.streams == ["part"]
        assert recorder.processing_starts == 1
        assert recorder.processing_ends == 1
        assert len(recorder.activities) == 1

    async def test_clear(self):
        """clear() empties buffer and stream accumulator."""
        recorder = OutputRecorder()
        capture = OutputLogCapture(recorder, max_entries=50)

        await capture.write("data")
        await capture.write_stream("buffered")
        assert capture.entry_count == 1
        assert capture._stream_buffer == "buffered"

        capture.clear()

        assert capture.entry_count == 0
        assert capture._stream_buffer == ""

    async def test_empty_write_not_logged(self):
        """Empty string write is forwarded but not logged."""
        recorder = OutputRecorder()
        capture = OutputLogCapture(recorder, max_entries=50)

        await capture.write("")
        assert capture.entry_count == 0

    async def test_flush_with_no_buffer(self):
        """Flush when stream buffer is empty creates no entry."""
        recorder = OutputRecorder()
        capture = OutputLogCapture(recorder, max_entries=50)

        await capture.flush()
        assert capture.entry_count == 0

    async def test_reset_passes_through(self):
        """reset() is forwarded to wrapped module."""
        recorder = OutputRecorder()
        recorder.writes.append("old")
        capture = OutputLogCapture(recorder, max_entries=50)

        capture.reset()
        assert recorder.writes == []  # OutputRecorder.reset clears writes

    async def test_preview_truncation(self):
        """LogEntry.preview truncates long content."""
        entry = LogEntry(
            timestamp=__import__("datetime").datetime.now(),
            content="a" * 200,
        )
        preview = entry.preview(max_len=50)
        assert len(preview) == 53  # 50 + "..."
        assert preview.endswith("...")

    async def test_get_entries_last_n(self):
        """get_entries respects last_n limit."""
        recorder = OutputRecorder()
        capture = OutputLogCapture(recorder, max_entries=50)

        for i in range(10):
            await capture.write(f"msg_{i}")

        entries = capture.get_entries(last_n=3)
        assert len(entries) == 3
        assert entries[0].content == "msg_7"
        assert entries[-1].content == "msg_9"


class TestCreatureOutputLog:
    """Tests for CreatureHandle output log integration."""

    def _make_handle(
        self,
        *,
        output_log: OutputLogCapture | None = None,
    ) -> CreatureHandle:
        """Create a minimal CreatureHandle for testing."""
        # We use a stub for agent since we only test handle accessors
        cfg = CreatureConfig(
            name="test_creature",
            config_data={"base_config": "/tmp/fake"},
            base_dir=Path("."),
        )

        class _StubAgent:
            is_running = True

        return CreatureHandle(
            name="test_creature",
            agent=_StubAgent(),  # type: ignore[arg-type]
            config=cfg,
            listen_channels=[],
            send_channels=[],
            output_log=output_log,
        )

    async def test_creature_with_log_enabled(self):
        """CreatureHandle with output_log exposes log data."""
        recorder = OutputRecorder()
        capture = OutputLogCapture(recorder, max_entries=50)
        await capture.write("hello from creature")

        handle = self._make_handle(output_log=capture)

        entries = handle.get_log_entries(last_n=5)
        assert len(entries) == 1
        assert entries[0].content == "hello from creature"

        text = handle.get_log_text(last_n=5)
        assert "hello from creature" in text

    async def test_creature_without_log(self):
        """CreatureHandle without output_log returns empty."""
        handle = self._make_handle(output_log=None)

        assert handle.get_log_entries() == []
        assert handle.get_log_text() == ""
