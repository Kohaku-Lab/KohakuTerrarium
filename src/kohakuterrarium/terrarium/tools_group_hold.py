"""Pause, resume, and force-stop non-privileged group members."""

from typing import Any

from kohakuterrarium.builtins.tool_catalog import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.terrarium import tools_group_lifecycle as lifecycle
from kohakuterrarium.terrarium.tools_group_common import err, ok


@register_builtin("group_pause_node")
class GroupPauseNodeTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "group_pause_node"

    @property
    def description(self) -> str:
        return (
            "Warm-pause a non-privileged creature: it stops admitting new "
            "turns but stays live (resume with group_resume_node). Not a "
            "teardown — use group_stop_node to force-stop"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"creature_id": {"type": "string"}},
            "required": ["creature_id"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        gctx, err_result = lifecycle.resolve_or_error(context)
        if err_result is not None:
            return err_result
        ident = (args.get("creature_id") or "").strip()
        target, terr = lifecycle._resolve_worker_target(gctx, ident, "pause")
        if terr is not None:
            return terr
        if not target.is_running:
            return err(f"creature {target.name!r} is not running")
        if target.paused:
            return err(f"creature {target.name!r} is already paused")
        target.pause()
        return ok({"paused": target.creature_id})


@register_builtin("group_resume_node")
class GroupResumeNodeTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "group_resume_node"

    @property
    def description(self) -> str:
        return "Resume a warm-paused non-privileged creature (undoes group_pause_node)"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"creature_id": {"type": "string"}},
            "required": ["creature_id"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        gctx, err_result = lifecycle.resolve_or_error(context)
        if err_result is not None:
            return err_result
        ident = (args.get("creature_id") or "").strip()
        target, terr = lifecycle._resolve_worker_target(gctx, ident, "resume")
        if terr is not None:
            return terr
        if not target.paused:
            return err(f"creature {target.name!r} is not paused")
        target.resume()
        return ok({"resumed": target.creature_id})


@register_builtin("group_kill_node")
class GroupKillNodeTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "group_kill_node"

    @property
    def description(self) -> str:
        return (
            "Force-stop a non-privileged creature and mark it killed. Hard "
            "kill is NOT supported in a shared process — this is an honest "
            "force-stop, not a fake SIGKILL"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"creature_id": {"type": "string"}},
            "required": ["creature_id"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        gctx, err_result = lifecycle.resolve_or_error(context)
        if err_result is not None:
            return err_result
        ident = (args.get("creature_id") or "").strip()
        target, terr = lifecycle._resolve_worker_target(gctx, ident, "kill")
        if terr is not None:
            return terr
        if not target.is_running:
            return err(f"creature {target.name!r} is not running")
        try:
            await gctx.engine.stop(target.creature_id)
        except Exception as exc:
            return err(f"kill failed: {exc}")
        target._killed = True
        return ok({"killed": target.creature_id, "hard_kill_supported": False})
