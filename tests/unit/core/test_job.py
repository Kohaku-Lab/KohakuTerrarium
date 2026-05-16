"""Unit tests for :mod:`kohakuterrarium.core.job` (JobStatus / JobResult / JobStore)."""

from datetime import datetime, timedelta

import pytest

from kohakuterrarium.core.job import (
    JobResult,
    JobState,
    JobStatus,
    JobStore,
    JobType,
    generate_job_id,
)


class TestGenerateJobId:
    def test_unique_per_call(self):
        a = generate_job_id()
        b = generate_job_id()
        assert a != b

    def test_default_prefix(self):
        assert generate_job_id().startswith("job_")

    def test_custom_prefix(self):
        assert generate_job_id("tool").startswith("tool_")

    def test_short_uuid_length(self):
        # The implementation uses 8 hex chars after the prefix.
        out = generate_job_id("p")
        suffix = out.split("_", 1)[1]
        assert len(suffix) == 8


class TestJobStatusBasics:
    def test_construct_pending(self):
        s = JobStatus(job_id="j1", job_type=JobType.TOOL, type_name="bash")
        assert s.job_id == "j1"
        assert s.job_type is JobType.TOOL
        assert s.type_name == "bash"
        assert s.state is JobState.PENDING
        assert s.end_time is None
        assert s.output_lines == 0
        assert s.output_bytes == 0
        assert s.preview == ""
        assert s.error is None
        assert s.context == {}

    def test_is_complete_for_terminal_states(self):
        for st in (JobState.DONE, JobState.ERROR, JobState.CANCELLED):
            s = JobStatus(job_id="j", job_type=JobType.TOOL, type_name="x", state=st)
            assert s.is_complete is True
            assert s.is_running is False

    def test_is_running_only_when_running(self):
        s = JobStatus(
            job_id="j", job_type=JobType.TOOL, type_name="x", state=JobState.RUNNING
        )
        assert s.is_running is True
        assert s.is_complete is False

    def test_is_complete_false_for_pending(self):
        s = JobStatus(job_id="j", job_type=JobType.TOOL, type_name="x")
        assert s.is_complete is False
        assert s.is_running is False


class TestJobStatusDuration:
    def test_running_duration_uses_now(self):
        start = datetime.now() - timedelta(seconds=2)
        s = JobStatus(
            job_id="j",
            job_type=JobType.TOOL,
            type_name="x",
            state=JobState.RUNNING,
            start_time=start,
        )
        d = s.duration
        # 2s give-or-take wall-clock noise.
        assert 1.5 <= d <= 5.0

    def test_completed_duration_uses_end_time(self):
        start = datetime.now() - timedelta(seconds=10)
        end = start + timedelta(seconds=5)
        s = JobStatus(
            job_id="j",
            job_type=JobType.TOOL,
            type_name="x",
            state=JobState.DONE,
            start_time=start,
            end_time=end,
        )
        assert 4.9 <= s.duration <= 5.1


class TestJobStatusContextString:
    def test_minimal(self):
        s = JobStatus(job_id="j1", job_type=JobType.TOOL, type_name="bash")
        out = s.to_context_string()
        assert "[j1]" in out
        assert "type=tool/bash" in out
        assert "status=pending" in out

    def test_includes_byte_and_line_counts(self):
        s = JobStatus(
            job_id="j",
            job_type=JobType.TOOL,
            type_name="bash",
            output_lines=10,
            output_bytes=512,
        )
        out = s.to_context_string()
        assert "lines=10" in out
        assert "bytes=512" in out

    def test_preview_truncated_in_context_string(self):
        s = JobStatus(
            job_id="j",
            job_type=JobType.TOOL,
            type_name="bash",
            preview="x" * 200,
        )
        out = s.to_context_string()
        assert "..." in out

    def test_error_truncated_to_50_chars(self):
        s = JobStatus(
            job_id="j",
            job_type=JobType.TOOL,
            type_name="bash",
            error="e" * 200,
        )
        out = s.to_context_string()
        # 50 chars of the error in quotes.
        assert "error=" in out
        assert "e" * 50 in out
        # Should NOT contain all 200.
        assert "e" * 100 not in out

    def test_repr_short_form(self):
        s = JobStatus(job_id="j1", job_type=JobType.TOOL, type_name="bash")
        r = repr(s)
        assert "j1" in r
        assert "bash" in r
        assert "pending" in r


class TestJobResult:
    def test_success_no_error_no_exit_code(self):
        assert JobResult(job_id="j", output="ok").success is True

    def test_success_exit_zero(self):
        assert JobResult(job_id="j", output="ok", exit_code=0).success is True

    def test_failure_with_error(self):
        assert JobResult(job_id="j", error="boom").success is False

    def test_failure_nonzero_exit(self):
        assert JobResult(job_id="j", output="x", exit_code=1).success is False

    def test_get_text_output_string(self):
        r = JobResult(job_id="j", output="line1\nline2")
        assert r.get_text_output() == "line1\nline2"

    def test_get_lines_all(self):
        r = JobResult(job_id="j", output="a\nb\nc\nd")
        assert r.get_lines() == ["a", "b", "c", "d"]

    def test_get_lines_with_start(self):
        r = JobResult(job_id="j", output="a\nb\nc\nd")
        assert r.get_lines(start=1) == ["b", "c", "d"]

    def test_get_lines_with_start_and_count(self):
        r = JobResult(job_id="j", output="a\nb\nc\nd")
        assert r.get_lines(start=1, count=2) == ["b", "c"]

    def test_truncated_short_output(self):
        r = JobResult(job_id="j", output="short")
        assert r.truncated(max_chars=100) == "short"

    def test_truncated_long_output(self):
        long = "x" * 2000
        r = JobResult(job_id="j", output=long)
        t = r.truncated(max_chars=100)
        # 100 chars + "\n... (1900 more chars)" tail.
        assert "1900 more chars" in t
        assert len(t) < 200


# ── JobStore ────────────────────────────────────────────────────────


@pytest.fixture
def store() -> JobStore:
    return JobStore(max_completed=5)


def _make_status(jid: str, state: JobState = JobState.PENDING) -> JobStatus:
    return JobStatus(job_id=jid, job_type=JobType.TOOL, type_name="bash", state=state)


class TestJobStoreRegisterAndLookup:
    def test_register_then_get(self, store):
        s = _make_status("j1")
        store.register(s)
        assert store.get_status("j1") is s

    def test_get_unknown_returns_none(self, store):
        assert store.get_status("nope") is None

    def test_get_result_unknown_returns_none(self, store):
        assert store.get_result("nope") is None


class TestJobStoreUpdate:
    def test_update_state_sets_end_time_on_done(self, store):
        s = _make_status("j", JobState.RUNNING)
        store.register(s)
        before = datetime.now()
        out = store.update_status("j", state=JobState.DONE)
        assert out is not None
        assert out.state is JobState.DONE
        assert out.end_time is not None
        assert out.end_time >= before

    def test_update_state_sets_end_time_on_error(self, store):
        s = _make_status("j", JobState.RUNNING)
        store.register(s)
        out = store.update_status("j", state=JobState.ERROR)
        assert out.end_time is not None

    def test_update_state_running_keeps_end_time_none(self, store):
        s = _make_status("j", JobState.PENDING)
        store.register(s)
        out = store.update_status("j", state=JobState.RUNNING)
        assert out.end_time is None

    def test_update_unknown_returns_none(self, store):
        assert store.update_status("missing", state=JobState.DONE) is None

    def test_partial_update_only_changes_passed_fields(self, store):
        s = _make_status("j", JobState.RUNNING)
        s.output_bytes = 100
        store.register(s)
        out = store.update_status("j", output_lines=10)
        # Only output_lines changed; bytes preserved.
        assert out.output_lines == 10
        assert out.output_bytes == 100

    def test_update_error_field(self, store):
        store.register(_make_status("j"))
        out = store.update_status("j", error="boom")
        assert out.error == "boom"

    def test_update_preview_field(self, store):
        store.register(_make_status("j"))
        out = store.update_status("j", preview="text")
        assert out.preview == "text"


class TestJobStoreFilters:
    def test_running_pending_completed_split(self, store):
        store.register(_make_status("r1", JobState.RUNNING))
        store.register(_make_status("p1", JobState.PENDING))
        store.register(_make_status("d1", JobState.DONE))
        store.register(_make_status("e1", JobState.ERROR))
        store.register(_make_status("c1", JobState.CANCELLED))

        running_ids = {j.job_id for j in store.get_running_jobs()}
        pending_ids = {j.job_id for j in store.get_pending_jobs()}
        completed_ids = {j.job_id for j in store.get_completed_jobs()}
        assert running_ids == {"r1"}
        assert pending_ids == {"p1"}
        assert completed_ids == {"d1", "e1", "c1"}

    def test_get_all_statuses_returns_everything(self, store):
        for jid in ("a", "b", "c"):
            store.register(_make_status(jid))
        assert {j.job_id for j in store.get_all_statuses()} == {"a", "b", "c"}


class TestJobStoreResults:
    def test_store_then_get(self, store):
        r = JobResult(job_id="j", output="x")
        store.store_result(r)
        assert store.get_result("j") is r


class TestJobStoreCleanup:
    def test_cleanup_keeps_max_completed_only(self):
        store = JobStore(max_completed=2)
        # Five DONE jobs with monotonically increasing end_time.
        base = datetime.now()
        for i in range(5):
            s = _make_status(f"j{i}", JobState.DONE)
            s.end_time = base + timedelta(seconds=i)
            store.register(s)
            store.store_result(JobResult(job_id=f"j{i}", output="x"))
        completed = store.get_completed_jobs()
        # Oldest three pruned; newest two kept.
        kept = sorted(j.job_id for j in completed)
        assert kept == ["j3", "j4"]
        # And the matching results are pruned too.
        assert store.get_result("j0") is None
        assert store.get_result("j4") is not None


class TestJobStoreFormatContext:
    def test_only_running_shown_by_default(self, store):
        store.register(_make_status("r", JobState.RUNNING))
        store.register(_make_status("d", JobState.DONE))
        out = store.format_context()
        assert "Running Jobs" in out
        assert "r" in out
        # Completed not included unless asked.
        assert "Recent Completed" not in out

    def test_pending_section_emitted(self, store):
        store.register(_make_status("p", JobState.PENDING))
        out = store.format_context()
        assert "Pending Jobs" in out
        assert "p" in out

    def test_include_completed_flag(self):
        store = JobStore(max_completed=10)
        base = datetime.now()
        s = _make_status("d", JobState.DONE)
        s.end_time = base
        store.register(s)
        out = store.format_context(include_completed=True)
        assert "Recent Completed Jobs" in out
        assert "d" in out

    def test_empty_store_returns_empty_string(self, store):
        assert store.format_context() == ""
