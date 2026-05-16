"""Unit tests for :mod:`kohakuterrarium.modules.output.router_state`."""

from datetime import datetime

from kohakuterrarium.modules.output.router_state import (
    CompletedOutput,
    OutputState,
)


class TestCompletedOutputPreview:
    def test_short_content_returned_whole(self):
        out = CompletedOutput(target="discord", content="short")
        assert out.preview() == "short"

    def test_long_content_truncated_with_ellipsis(self):
        out = CompletedOutput(target="discord", content="x" * 200)
        preview = out.preview(max_len=50)
        assert preview == "x" * 50 + "..."

    def test_to_feedback_line_success_escapes_newlines(self):
        ts = datetime(2026, 5, 14, 9, 30, 0)
        out = CompletedOutput(
            target="tts", content="line one\nline two", timestamp=ts, success=True
        )
        line = out.to_feedback_line()
        assert "[tts]" in line
        assert "09:30:00" in line
        # Newlines collapsed for single-line display.
        assert "\\n" in line
        assert "\n" not in line.replace("\\n", "")

    def test_to_feedback_line_failure_shows_error(self):
        ts = datetime(2026, 5, 14, 9, 30, 0)
        out = CompletedOutput(
            target="discord",
            content="payload",
            timestamp=ts,
            success=False,
            error="network down",
        )
        line = out.to_feedback_line()
        assert "FAILED" in line
        assert "network down" in line


class TestOutputState:
    def test_distinct_states(self):
        # Each parser context maps to a distinct enum member.
        members = {
            OutputState.NORMAL,
            OutputState.TOOL_BLOCK,
            OutputState.SUBAGENT_BLOCK,
            OutputState.COMMAND_BLOCK,
            OutputState.OUTPUT_BLOCK,
        }
        assert len(members) == 5
