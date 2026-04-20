"""Think tool - preserve reasoning as a tool event in the session log.

The tool is a deliberate no-op at runtime: it takes a `thought` argument
and echoes it back as the tool result. Its value is purely procedural --
writing the reasoning into the append-only event log so it survives
context compaction and session resume, and gives the controller a
structured place to deliberate without emitting user-visible output.
"""

from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@register_builtin("think")
class ThinkTool(BaseTool):
    """Record a reasoning step as a tool event. No side effects."""

    @property
    def tool_name(self) -> str:
        return "think"

    @property
    def description(self) -> str:
        return "Record a reasoning step (no-op); preserves thought in the event log"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any]) -> ToolResult:
        """Echo the thought back so it lands in the tool_result event."""
        thought = args.get("thought") or args.get("body") or args.get("content") or ""
        if isinstance(thought, str):
            thought = thought.strip()

        if not thought:
            return ToolResult(error="thought is required", exit_code=1)

        logger.debug("Think tool recorded thought", length=len(thought))
        return ToolResult(output=thought, exit_code=0)
