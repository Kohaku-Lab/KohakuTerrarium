"""
Job status tracking for background tasks.

Jobs represent running tools or sub-agents with their status and output.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from kohakuterrarium.core.tool_output import render_content_text

if TYPE_CHECKING:
    from kohakuterrarium.llm.message import ContentPart


class JobType(Enum):
    """Type of job."""

    TOOL = "tool"
    SUBAGENT = "subagent"
    COMMAND = "command"


class JobState(Enum):
    """State of a job."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class JobStatus:
    """
    Status information for a running or completed job.

    Attributes:
        job_id: Unique identifier for this job
        job_type: Type of job (tool, subagent, command)
        type_name: Name of the tool/subagent/command
        state: Current state
        start_time: When the job started
        end_time: When the job completed (if done)
        output_lines: Number of output lines
        output_bytes: Total output size in bytes
        preview: First/last N chars of output (without full content)
        error: Error message if state is ERROR
        context: Additional context data
    """

    job_id: str
    job_type: JobType
    type_name: str
    state: JobState = JobState.PENDING
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None
    output_lines: int = 0
    output_bytes: int = 0
    preview: str = ""
    error: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        """Get duration in seconds."""
        end = self.end_time or datetime.now()
        return (end - self.start_time).total_seconds()

    @property
    def is_complete(self) -> bool:
        """Check if job is complete (done or error)."""
        return self.state in (JobState.DONE, JobState.ERROR, JobState.CANCELLED)

    @property
    def is_running(self) -> bool:
        """Check if job is currently running."""
        return self.state == JobState.RUNNING

    def to_context_string(self) -> str:
        """Format job status for inclusion in controller context."""
        status_str = self.state.value
        duration_str = f"{self.duration:.1f}s"

        parts = [
            f"[{self.job_id}]",
            f"type={self.job_type.value}/{self.type_name}",
            f"status={status_str}",
            f"duration={duration_str}",
        ]

        if self.output_lines > 0:
            parts.append(f"lines={self.output_lines}")

        if self.output_bytes > 0:
            parts.append(f"bytes={self.output_bytes}")

        if self.preview:
            # Truncate preview for context
            preview = self.preview[:100]
            if len(self.preview) > 100:
                preview += "..."
            parts.append(f'preview="{preview}"')

        if self.error:
            parts.append(f'error="{self.error[:50]}"')

        return " ".join(parts)

    def __repr__(self) -> str:
        return (
            f"JobStatus(id={self.job_id!r}, type={self.type_name!r}, "
            f"state={self.state.value}, duration={self.duration:.1f}s)"
        )


@dataclass
class JobResult:
    """
    Complete result of a finished job.

    Attributes:
        job_id: Job identifier
        output: Full output content
        exit_code: Exit code (for tools like bash)
        error: Error message if failed
        metadata: Additional result metadata
    """

    job_id: str
    output: "str | list[ContentPart]" = ""
    exit_code: int | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """Check if job completed successfully."""
        return self.error is None and (self.exit_code is None or self.exit_code == 0)

    def get_text_output(self) -> str:
        """Render output to safe text for commands/logging."""
        return render_content_text(self.output)

    def get_lines(self, start: int = 0, count: int | None = None) -> list[str]:
        """Get rendered text lines with optional slicing."""
        lines = self.get_text_output().split("\n")
        if count is None:
            return lines[start:]
        return lines[start : start + count]

    def truncated(self, max_chars: int = 1000) -> str:
        """Get truncated rendered text output."""
        text = self.get_text_output()
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n... ({len(text) - max_chars} more chars)"


def generate_job_id(prefix: str = "job") -> str:
    """Generate a unique job ID."""
    short_uuid = uuid.uuid4().hex[:8]
    return f"{prefix}_{short_uuid}"


class JobStore:
    """
    In-memory store for job statuses and results.

    Thread-safe storage for job tracking.
    """

    def __init__(self, max_completed: int = 100):
        """
        Initialize job store.

        Args:
            max_completed: Maximum number of completed jobs to keep
        """
        self._statuses: dict[str, JobStatus] = {}
        self._results: dict[str, JobResult] = {}
        self._max_completed = max_completed

    def register(self, status: JobStatus) -> None:
        """Register a new job."""
        self._statuses[status.job_id] = status

    def get_status(self, job_id: str) -> JobStatus | None:
        """Get job status by ID."""
        return self._statuses.get(job_id)

    def update_status(
        self,
        job_id: str,
        state: JobState | None = None,
        output_lines: int | None = None,
        output_bytes: int | None = None,
        preview: str | None = None,
        error: str | None = None,
    ) -> JobStatus | None:
        """Update job status fields."""
        status = self._statuses.get(job_id)
        if status is None:
            return None

        if state is not None:
            status.state = state
            if state in (JobState.DONE, JobState.ERROR, JobState.CANCELLED):
                status.end_time = datetime.now()

        if output_lines is not None:
            status.output_lines = output_lines
        if output_bytes is not None:
            status.output_bytes = output_bytes
        if preview is not None:
            status.preview = preview
        if error is not None:
            status.error = error

        return status

    def store_result(self, result: JobResult) -> None:
        """Store job result."""
        self._results[result.job_id] = result
        self._cleanup_old_jobs()

    def get_result(self, job_id: str) -> JobResult | None:
        """Get job result by ID."""
        return self._results.get(job_id)

    def get_running_jobs(self) -> list[JobStatus]:
        """Get all currently running jobs."""
        return [s for s in self._statuses.values() if s.is_running]

    def get_pending_jobs(self) -> list[JobStatus]:
        """Get all pending jobs."""
        return [s for s in self._statuses.values() if s.state == JobState.PENDING]

    def get_completed_jobs(self) -> list[JobStatus]:
        """Get all completed jobs."""
        return [s for s in self._statuses.values() if s.is_complete]

    def get_all_statuses(self) -> list[JobStatus]:
        """Get all job statuses."""
        return list(self._statuses.values())

    def _cleanup_old_jobs(self) -> None:
        """Remove old completed jobs if over limit."""
        completed = self.get_completed_jobs()
        if len(completed) > self._max_completed:
            # Sort by end_time and remove oldest
            completed.sort(key=lambda j: j.end_time or j.start_time)
            to_remove = completed[: len(completed) - self._max_completed]
            for job in to_remove:
                self._statuses.pop(job.job_id, None)
                self._results.pop(job.job_id, None)

    def format_context(self, include_completed: bool = False) -> str:
        """Format all jobs for controller context."""
        lines = []

        running = self.get_running_jobs()
        if running:
            lines.append("## Running Jobs")
            for job in running:
                lines.append(f"- {job.to_context_string()}")

        pending = self.get_pending_jobs()
        if pending:
            lines.append("\n## Pending Jobs")
            for job in pending:
                lines.append(f"- {job.to_context_string()}")

        if include_completed:
            completed = self.get_completed_jobs()[-10:]  # Last 10
            if completed:
                lines.append("\n## Recent Completed Jobs")
                for job in completed:
                    lines.append(f"- {job.to_context_string()}")

        return "\n".join(lines) if lines else ""
