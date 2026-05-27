"""Delete-trigger tool — stop and remove a previously-installed trigger.

Counterpart to the ``add_timer`` / ``add_schedule`` / ``watch_channel``
creation tools. Triggers live exclusively in
``agent.trigger_manager._triggers`` — ``stop_task`` does not see them
(it only knows the executor / sub-agent manager / direct-job tracker),
so a dedicated tool is the cleanest mirror of the creation API.
"""

from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@register_builtin("delete_trigger")
class DeleteTriggerTool(BaseTool):
    """Stop and remove a trigger by id."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "delete_trigger"

    @property
    def description(self) -> str:
        return (
            "Stop and remove a previously-installed trigger (timer / "
            "schedule / channel watcher) by its trigger_id. Pair with "
            "add_timer / add_schedule / watch_channel."
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "trigger_id": {
                    "type": "string",
                    "description": (
                        "Trigger id returned by add_timer / add_schedule / "
                        "watch_channel, or any id surfaced by the /triggers "
                        "listing."
                    ),
                },
            },
            "required": ["trigger_id"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        trigger_id = (args.get("trigger_id") or "").strip()
        if not trigger_id:
            return ToolResult(error="trigger_id is required", exit_code=1)
        if not context or not context.agent:
            return ToolResult(error="Agent context required", exit_code=1)

        agent = context.agent
        remove = getattr(agent, "remove_trigger", None)
        if not callable(remove):
            return ToolResult(
                error="Agent does not support trigger removal", exit_code=1
            )
        try:
            removed = await remove(trigger_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "delete_trigger failed",
                trigger_id=trigger_id,
                error=str(exc),
                exc_info=True,
            )
            return ToolResult(error=f"Failed to remove trigger: {exc}", exit_code=1)
        if not removed:
            return ToolResult(
                error=f"Trigger not found: {trigger_id}",
                exit_code=1,
            )
        logger.info("Trigger removed", trigger_id=trigger_id)
        return ToolResult(
            output=f"Removed trigger: {trigger_id}",
            exit_code=0,
        )
