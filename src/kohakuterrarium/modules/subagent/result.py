"""Sub-agent result types and job wrapper."""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kohakuterrarium.core.constants import TOOL_OUTPUT_PREVIEW_CHARS
from kohakuterrarium.core.job import JobResult, JobState, JobStatus, JobType
from kohakuterrarium.parsing.format import (
    BRACKET_FORMAT,
    ToolCallFormat,
    format_tool_call_example,
)

_SUBAGENT_CRITICAL_RULES = """
## CRITICAL: You MUST use tools to complete your task

- After calling a tool, STOP and wait for results
- Do NOT just describe what you would do - actually DO it
- Continue calling tools until task is complete
""".strip()


def build_subagent_framework_hints(
    tool_format: str | None,
    parser_format: ToolCallFormat | None = None,
) -> str:
    """Build format-aware framework hints for sub-agents.

    Native mode: no format examples (API handles it).
    Custom mode: generate examples from the actual ToolCallFormat.
    """
    if tool_format == "native":
        return (
            "## Tool Calling\n\n"
            "Tools are called via the API's native function calling mechanism.\n"
            "You do not need to format tool calls manually.\n\n"
            + _SUBAGENT_CRITICAL_RULES
        )

    if parser_format is None:
        parser_format = BRACKET_FORMAT

    lines = ["## Tool Calling Format", ""]
    generic = format_tool_call_example(
        parser_format, "tool_name", {"arg": "value"}, "content here"
    )
    lines.append(f"```\n{generic}\n```")
    lines.append("")
    lines.append("Examples:")
    lines.append("")

    for name, args in [
        ("glob", {"pattern": "**/*.py"}),
        ("grep", {"pattern": "class.*Config"}),
        ("read", {"path": "src/main.py"}),
    ]:
        ex = format_tool_call_example(parser_format, name, args)
        lines.append(f"```\n{ex}\n```")
        lines.append("")

    lines.append(_SUBAGENT_CRITICAL_RULES)
    return "\n".join(lines)


# Backward-compatible alias (bracket format)
SUBAGENT_FRAMEWORK_HINTS = build_subagent_framework_hints("bracket", BRACKET_FORMAT)


@dataclass
class SubAgentResult:
    """Result from sub-agent execution."""

    output: str = ""
    success: bool = True
    error: str | None = None
    interrupted: bool = False
    cancelled: bool = False
    turns: int = 0
    duration: float = 0.0
    total_tokens: int = 0  # Total tokens used across all turns
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Prompt-cache hit tokens (Wave B audit finding A). Populated from
    # the provider's ``last_usage["cached_tokens"]`` by SubAgent and
    # surfaced through the parent's ``subagent_done`` activity so the
    # session store can record it.
    cached_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def truncated(self, max_chars: int = 2000) -> str:
        """Get truncated output with note if truncated."""
        if len(self.output) <= max_chars:
            return self.output
        return f"{self.output[:max_chars]}\n... ({len(self.output) - max_chars} more chars)"


class SubAgentJob:
    """Wrapper for running a sub-agent as a background job.

    Integrates with the executor's job tracking system.
    """

    def __init__(self, subagent: "SubAgent", job_id: str):
        self.subagent = subagent
        self.job_id = job_id
        self._task: asyncio.Task | None = None
        self._result: SubAgentResult | None = None

    async def run(self, task: str) -> SubAgentResult:
        """Run the sub-agent and return result."""
        self._result = await self.subagent.run(task)
        return self._result

    def to_job_status(self) -> JobStatus:
        """Create job status for this sub-agent run."""
        if self._result and (self._result.interrupted or self._result.cancelled):
            state = JobState.CANCELLED
        elif self._result and not self._result.success:
            state = JobState.ERROR
        elif not self._result and self.subagent._cancelled:
            state = JobState.CANCELLED
        elif self.subagent.is_running:
            state = JobState.RUNNING
        else:
            state = JobState.DONE

        return JobStatus(
            job_id=self.job_id,
            job_type=JobType.SUBAGENT,
            type_name=self.subagent.config.name,
            state=state,
            output_lines=self._result.output.count("\n") + 1 if self._result else 0,
            output_bytes=len(self._result.output) if self._result else 0,
            preview=(
                self._result.output[:TOOL_OUTPUT_PREVIEW_CHARS] if self._result else ""
            ),
            error=self._result.error if self._result else None,
        )

    def to_job_result(self) -> JobResult | None:
        """Convert to JobResult for compatibility."""
        if not self._result:
            return None

        return JobResult(
            job_id=self.job_id,
            output=self._result.output,
            exit_code=0 if self._result.success else 1,
            error=self._result.error,
            metadata={
                "turns": self._result.turns,
                "duration": self._result.duration,
            },
        )


if TYPE_CHECKING:
    from kohakuterrarium.modules.subagent.base import SubAgent
