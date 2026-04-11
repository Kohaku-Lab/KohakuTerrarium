"""
Terrarium lifecycle tools for the root agent.

Create, status, and stop terrariums.
"""

import asyncio
import json
from typing import Any
from uuid import uuid4

from kohakuterrarium.builtins.tool_catalog import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.runtime import TerrariumRuntime
from kohakuterrarium.terrarium.tool_manager import (
    TERRARIUM_MANAGER_KEY,
    TerrariumToolManager,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _get_manager(context: ToolContext | None) -> TerrariumToolManager:
    """Extract the TerrariumToolManager from tool context."""
    if not context or not context.environment:
        raise RuntimeError(
            "Terrarium tools require an environment context. "
            "The root agent must be running inside a terrarium or "
            "have a TerrariumToolManager registered in its environment."
        )
    manager = context.environment.get(TERRARIUM_MANAGER_KEY)
    if manager is None:
        raise RuntimeError(
            f"No TerrariumToolManager found in environment. "
            f"Register one with environment.register('{TERRARIUM_MANAGER_KEY}', manager)."
        )
    return manager


@register_builtin("terrarium_create")
class TerrariumCreateTool(BaseTool):
    """Create and start a terrarium from a config path or inline YAML."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "terrarium_create"

    @property
    def description(self) -> str:
        return "Create and start a terrarium from a config path"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "config_path": {
                    "type": "string",
                    "description": "Path to terrarium config directory (e.g. terrariums/swe_team)",
                },
            },
            "required": ["config_path"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        manager = _get_manager(context)

        config_path = args.get("config_path", "").strip()
        if not config_path:
            return ToolResult(error="config_path is required")

        try:
            config = load_terrarium_config(config_path)
            runtime = TerrariumRuntime(config)
            terrarium_id = f"{config.name}_{uuid4().hex[:6]}"

            await runtime.start()

            # Run creatures as background task
            task = asyncio.create_task(runtime.run())
            manager.register_runtime(terrarium_id, runtime)
            manager.register_task(terrarium_id, task)

            status = runtime.get_status()
            creature_names = list(status.get("creatures", {}).keys())
            channel_names = [ch["name"] for ch in status.get("channels", [])]

            return ToolResult(
                output=(
                    f"Terrarium '{terrarium_id}' created and running.\n"
                    f"Creatures: {', '.join(creature_names)}\n"
                    f"Channels: {', '.join(channel_names)}"
                ),
                exit_code=0,
                metadata={"terrarium_id": terrarium_id},
            )
        except Exception as e:
            error_msg = str(e)
            logger.error("Failed to create terrarium", error=error_msg)
            return ToolResult(error=f"Failed to create terrarium: {error_msg}")


@register_builtin("terrarium_status")
class TerrariumStatusTool(BaseTool):
    """Get status of a terrarium or list all terrariums."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "terrarium_status"

    @property
    def description(self) -> str:
        return "Get status of a terrarium or list all running terrariums"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "terrarium_id": {
                    "type": "string",
                    "description": "Terrarium ID (omit to list all)",
                },
            },
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        manager = _get_manager(context)

        terrarium_id = args.get("terrarium_id", "").strip()

        if not terrarium_id:
            # List all terrariums
            ids = manager.list_terrariums()
            if not ids:
                return ToolResult(output="No terrariums running.", exit_code=0)

            lines = ["Running terrariums:"]
            for tid in ids:
                try:
                    runtime = manager.get_runtime(tid)
                    status = runtime.get_status()
                    creatures = list(status.get("creatures", {}).keys())
                    lines.append(
                        f"  {tid}: {len(creatures)} creatures ({', '.join(creatures)})"
                    )
                except Exception as e:
                    logger.debug("Error reading terrarium status", error=str(e))
                    lines.append(f"  {tid}: (error reading status)")
            return ToolResult(output="\n".join(lines), exit_code=0)

        # Get specific terrarium status
        try:
            runtime = manager.get_runtime(terrarium_id)
            status = runtime.get_status()
            return ToolResult(
                output=json.dumps(status, indent=2, default=str),
                exit_code=0,
            )
        except KeyError as e:
            return ToolResult(error=str(e))


@register_builtin("terrarium_stop")
class TerrariumStopTool(BaseTool):
    """Stop a running terrarium."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "terrarium_stop"

    @property
    def description(self) -> str:
        return "Stop a running terrarium and all its creatures"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "terrarium_id": {
                    "type": "string",
                    "description": "ID of the terrarium to stop",
                },
            },
            "required": ["terrarium_id"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        manager = _get_manager(context)
        terrarium_id = args.get("terrarium_id", "").strip()

        if not terrarium_id:
            return ToolResult(error="terrarium_id is required")

        try:
            await manager.stop_terrarium(terrarium_id)
            return ToolResult(
                output=f"Terrarium '{terrarium_id}' stopped.",
                exit_code=0,
            )
        except KeyError as e:
            return ToolResult(error=str(e))
