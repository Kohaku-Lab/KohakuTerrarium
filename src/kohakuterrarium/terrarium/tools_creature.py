"""Creature management tools for terrarium root agents."""

from pathlib import Path
from typing import Any

from kohakuterrarium.builtins.tool_catalog import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.terrarium.config import CreatureConfig
from kohakuterrarium.terrarium.tool_helpers import get_manager
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@register_builtin("creature_start")
class CreatureStartTool(BaseTool):
    """Add and start a new creature in a running terrarium."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "creature_start"

    @property
    def description(self) -> str:
        return "Add a new creature to a running terrarium via hot-plug"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "terrarium_id": {"type": "string", "description": "Terrarium ID"},
                "name": {"type": "string", "description": "Name for the new creature"},
                "config_path": {
                    "type": "string",
                    "description": "Path to creature config (e.g. creatures/swe)",
                },
                "listen_channels": {
                    "type": "string",
                    "description": "Comma-separated channel names to listen on",
                },
                "send_channels": {
                    "type": "string",
                    "description": "Comma-separated channel names to send to",
                },
            },
            "required": ["terrarium_id", "name", "config_path"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        manager = get_manager(context)
        terrarium_id = args.get("terrarium_id", "").strip()
        name = args.get("name", "").strip()
        config_path = args.get("config_path", "").strip()
        listen_raw = args.get("listen_channels", "")
        send_raw = args.get("send_channels", "")
        if not all([terrarium_id, name, config_path]):
            return ToolResult(error="terrarium_id, name, and config_path are required")
        listen = (
            [ch.strip() for ch in listen_raw.split(",") if ch.strip()]
            if listen_raw
            else []
        )
        send = (
            [ch.strip() for ch in send_raw.split(",") if ch.strip()] if send_raw else []
        )
        try:
            runtime = manager.get_runtime(terrarium_id)
            creature_cfg = CreatureConfig(
                name=name,
                config_data={"base_config": config_path},
                base_dir=Path.cwd(),
                listen_channels=listen,
                send_channels=send,
            )
            await runtime.add_creature(creature_cfg)
            return ToolResult(
                output=(
                    f"Creature '{name}' added to {terrarium_id}.\n"
                    f"Config: {config_path}\n"
                    f"Listening: {listen or '(none)'}\n"
                    f"Sending: {send or '(none)'}"
                ),
                exit_code=0,
            )
        except KeyError as e:
            return ToolResult(error=str(e))
        except Exception as e:
            error_msg = str(e)
            logger.error("Failed to start creature", error=error_msg)
            return ToolResult(error=f"Failed to start creature: {error_msg}")


@register_builtin("creature_stop")
class CreatureStopTool(BaseTool):
    """Stop and remove a creature from a running terrarium."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "creature_stop"

    @property
    def description(self) -> str:
        return "Stop and remove a creature from a running terrarium"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "terrarium_id": {"type": "string", "description": "Terrarium ID"},
                "name": {
                    "type": "string",
                    "description": "Name of the creature to stop",
                },
            },
            "required": ["terrarium_id", "name"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        manager = get_manager(context)
        terrarium_id = args.get("terrarium_id", "").strip()
        name = args.get("name", "").strip()
        if not terrarium_id or not name:
            return ToolResult(error="terrarium_id and name are required")
        try:
            runtime = manager.get_runtime(terrarium_id)
            removed = await runtime.remove_creature(name)
            if removed:
                return ToolResult(
                    output=f"Creature '{name}' removed from {terrarium_id}.",
                    exit_code=0,
                )
            return ToolResult(error=f"Creature '{name}' not found in {terrarium_id}.")
        except KeyError as e:
            return ToolResult(error=str(e))


@register_builtin("creature_interrupt")
class CreatureInterruptTool(BaseTool):
    """Interrupt a creature's current processing without stopping it."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "creature_interrupt"

    @property
    def description(self) -> str:
        return "Interrupt a creature's current LLM turn (creature stays alive)"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "terrarium_id": {"type": "string", "description": "Terrarium ID"},
                "name": {"type": "string", "description": "Creature name to interrupt"},
                "cancel_background": {
                    "type": "boolean",
                    "description": "Also cancel background tools/sub-agents (default: false)",
                },
            },
            "required": ["terrarium_id", "name"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        manager = get_manager(context)
        terrarium_id = args.get("terrarium_id", "").strip()
        name = args.get("name", "").strip()
        cancel_bg = args.get("cancel_background", False)
        if not terrarium_id or not name:
            return ToolResult(error="terrarium_id and name are required")
        try:
            runtime = manager.get_runtime(terrarium_id)
            agent = runtime.get_creature_agent(name)
            if not agent:
                return ToolResult(error=f"Creature '{name}' not found")
            agent.interrupt()
            if cancel_bg:
                running = agent.executor.get_running_jobs()
                cancelled = 0
                for job in running:
                    if await agent.executor.cancel(job.job_id):
                        cancelled += 1
                return ToolResult(
                    output=f"Interrupted '{name}' and cancelled {cancelled} background tasks.",
                    exit_code=0,
                )
            return ToolResult(
                output=f"Interrupted '{name}'. Background tasks continue running.",
                exit_code=0,
            )
        except KeyError as e:
            return ToolResult(error=str(e))
