"""Unit tests for :mod:`kohakuterrarium.modules.subagent.result`.

Behavior-first: SubAgentResult truncation, framework-hint generation per
tool format, and SubAgentJob's job-status / job-result derivation from
the underlying SubAgentResult.
"""

from kohakuterrarium.core.job import JobState, JobType
from kohakuterrarium.modules.subagent.result import (
    SUBAGENT_FRAMEWORK_HINTS,
    SubAgentJob,
    SubAgentResult,
    build_subagent_framework_hints,
)
from kohakuterrarium.parsing.format import BRACKET_FORMAT


class TestSubAgentResultTruncation:
    def test_short_output_returned_whole(self):
        result = SubAgentResult(output="short text")
        assert result.truncated(max_chars=100) == "short text"

    def test_long_output_truncated_with_note(self):
        result = SubAgentResult(output="x" * 5000)
        out = result.truncated(max_chars=2000)
        assert out.startswith("x" * 2000)
        assert "3000 more chars" in out


class TestFrameworkHints:
    def test_native_mode_omits_format_examples(self):
        hints = build_subagent_framework_hints("native")
        assert "native function calling" in hints
        # No bracket/xml examples in native mode.
        assert "```" not in hints

    def test_custom_mode_includes_format_examples(self):
        hints = build_subagent_framework_hints("bracket", BRACKET_FORMAT)
        assert "## Tool Calling Format" in hints
        assert "```" in hints
        # The concrete example tool names appear.
        assert "glob" in hints and "grep" in hints

    def test_none_format_defaults_to_bracket(self):
        # parser_format None → bracket examples are still generated.
        hints = build_subagent_framework_hints("bracket", None)
        assert "```" in hints

    def test_module_level_alias_is_bracket_hints(self):
        assert "## Tool Calling Format" in SUBAGENT_FRAMEWORK_HINTS


class _FakeSubAgentConfig:
    name = "explore"


class _FakeSubAgent:
    """Minimal SubAgent stand-in for SubAgentJob status derivation."""

    def __init__(self, running=False, cancelled=False):
        self.config = _FakeSubAgentConfig()
        self._cancelled = cancelled
        self._running = running

    @property
    def is_running(self):
        return self._running


class TestSubAgentJobStatus:
    def test_done_status_when_finished_successfully(self):
        job = SubAgentJob(_FakeSubAgent(), "j1")
        job._result = SubAgentResult(output="line1\nline2", success=True)
        status = job.to_job_status()
        assert status.job_id == "j1"
        assert status.job_type is JobType.SUBAGENT
        assert status.state is JobState.DONE
        # output_lines counts newlines + 1.
        assert status.output_lines == 2
        assert status.output_bytes == len("line1\nline2")

    def test_error_status_when_failed(self):
        job = SubAgentJob(_FakeSubAgent(), "j2")
        job._result = SubAgentResult(success=False, error="boom")
        status = job.to_job_status()
        assert status.state is JobState.ERROR
        assert status.error == "boom"

    def test_cancelled_status_when_interrupted(self):
        job = SubAgentJob(_FakeSubAgent(), "j3")
        job._result = SubAgentResult(success=False, interrupted=True)
        assert job.to_job_status().state is JobState.CANCELLED

    def test_running_status_when_no_result_yet(self):
        job = SubAgentJob(_FakeSubAgent(running=True), "j4")
        # No result, sub-agent still running → RUNNING.
        assert job.to_job_status().state is JobState.RUNNING

    def test_cancelled_status_when_no_result_but_cancelled_flag(self):
        job = SubAgentJob(_FakeSubAgent(cancelled=True), "j5")
        assert job.to_job_status().state is JobState.CANCELLED


class TestSubAgentJobResult:
    def test_to_job_result_none_before_completion(self):
        job = SubAgentJob(_FakeSubAgent(), "j1")
        assert job.to_job_result() is None

    def test_to_job_result_success_maps_exit_code_zero(self):
        job = SubAgentJob(_FakeSubAgent(), "j1")
        job._result = SubAgentResult(output="done", success=True, turns=3, duration=1.5)
        jr = job.to_job_result()
        assert jr.job_id == "j1"
        assert jr.output == "done"
        assert jr.exit_code == 0
        assert jr.metadata["turns"] == 3
        assert jr.metadata["duration"] == 1.5

    def test_to_job_result_failure_maps_exit_code_one(self):
        job = SubAgentJob(_FakeSubAgent(), "j1")
        job._result = SubAgentResult(success=False, error="failed")
        jr = job.to_job_result()
        assert jr.exit_code == 1
        assert jr.error == "failed"
