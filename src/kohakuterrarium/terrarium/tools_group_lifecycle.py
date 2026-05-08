"""``group_add_node`` / ``group_remove_node`` / ``group_start_node`` /
``group_stop_node`` — lifecycle tools for non-privileged workers."""

from typing import Any

import kohakuterrarium.terrarium.group_hooks as group_hooks
from kohakuterrarium.builtins.tool_catalog import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.packages.resolve import is_package_ref, resolve_package_path
from kohakuterrarium.terrarium.events import EngineEvent, EventKind
from kohakuterrarium.terrarium.group_tool_context import (
    GroupContext,
    resolve_group_target,
)
from kohakuterrarium.terrarium.tools_group_common import err, ok, resolve_or_error


def _caller_pwd(gctx: GroupContext) -> str:
    executor = getattr(gctx.caller.agent, "executor", None)
    if executor is not None:
        wd = getattr(executor, "_working_dir", None)
        if wd is not None:
            return str(wd)
    return ""


@register_builtin("group_add_node")
class GroupAddNodeTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "group_add_node"

    @property
    def description(self) -> str:
        return (
            "Spawn a new creature (disconnected) into your group; wire it "
            "afterward via group_channel or group_wire"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "config_path": {
                    "type": "string",
                    "description": "Creature config path or @pkg/creatures/<name>",
                },
                "name": {"type": "string"},
                "llm": {"type": "string"},
                "pwd": {"type": "string"},
            },
            "required": ["config_path"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        gctx, err_result = resolve_or_error(context)
        if err_result is not None:
            return err_result
        config_path = (args.get("config_path") or "").strip()
        if not config_path:
            return err("config_path is required")
        if is_package_ref(config_path):
            try:
                config_path = str(resolve_package_path(config_path))
            except FileNotFoundError as exc:
                return err(str(exc))

        pwd = args.get("pwd") or _caller_pwd(gctx)
        try:
            # Spawn directly into the caller's graph so the new creature
            # is a group member from birth — never a homeless singleton.
            # Without ``graph=``, ``add_creature`` mints a fresh graph
            # for every spawn, which (a) leaves the spawned creature
            # outside the caller's group until something wires it,
            # (b) inflates ``_session_stores`` with one empty entry
            # per spawn, and (c) makes the frontend rail show N+1
            # instances. Joining the caller's graph at creation time
            # is the simpler, correct shape.
            new = await gctx.engine.add_creature(
                config_path,
                graph=gctx.caller.graph_id,
                llm_override=args.get("llm"),
                pwd=pwd,
                is_privileged=False,
                parent_creature_id=gctx.caller.creature_id,
            )
        except Exception as exc:
            return err(f"failed to spawn creature from {config_path!r}: {exc}")

        if name := (args.get("name") or "").strip():
            group_hooks.apply_creature_name(new, name)

        group_hooks.attach_session_store(
            gctx.engine, new, config_path=config_path, config_type="agent"
        )

        gctx.engine._emit(
            EngineEvent(
                kind=EventKind.PARENT_LINK_CHANGED,
                creature_id=new.creature_id,
                graph_id=new.graph_id,
                payload={"parent": gctx.caller.creature_id, "change": "added"},
            )
        )
        return ok(
            {
                "creature_id": new.creature_id,
                "name": new.name,
                "graph_id": new.graph_id,
                "parent_creature_id": new.parent_creature_id,
                "caller_graph_id": gctx.caller.graph_id,
            }
        )


@register_builtin("group_remove_node")
class GroupRemoveNodeTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "group_remove_node"

    @property
    def description(self) -> str:
        return "Destroy a non-privileged creature in your group"

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
        gctx, err_result = resolve_or_error(context)
        if err_result is not None:
            return err_result
        target = resolve_group_target(gctx, (args.get("creature_id") or "").strip())
        if target is None:
            return err(f"creature {args.get('creature_id')!r} not in your group")
        if target.is_privileged:
            return err(
                f"cannot remove privileged creature {target.name!r}; "
                "only the user can do that via Studio"
            )
        # Capture parent before removal — the engine drops the creature
        # so we can't read it back afterward.
        parent_id = getattr(target, "parent_creature_id", None)
        try:
            await gctx.engine.remove_creature(target.creature_id)
        except Exception as exc:
            return err(f"remove failed: {exc}")
        if parent_id is not None:
            gctx.engine._emit(
                EngineEvent(
                    kind=EventKind.PARENT_LINK_CHANGED,
                    creature_id=target.creature_id,
                    payload={"change": "removed", "parent": parent_id},
                )
            )
        return ok(
            {
                "removed": target.creature_id,
                "caller_graph_id": gctx.caller.graph_id,
            }
        )


@register_builtin("group_start_node")
class GroupStartNodeTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "group_start_node"

    @property
    def description(self) -> str:
        return "Start a stopped non-privileged creature in your group"

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
        gctx, err_result = resolve_or_error(context)
        if err_result is not None:
            return err_result
        target = resolve_group_target(gctx, (args.get("creature_id") or "").strip())
        if target is None:
            return err(f"creature {args.get('creature_id')!r} not in your group")
        if target.is_privileged:
            return err(
                f"cannot start/stop privileged creature {target.name!r}; "
                "only the user can do that via Studio"
            )
        if target.is_running:
            return err(f"creature {target.name!r} is already running")
        try:
            await gctx.engine.start(target.creature_id)
        except Exception as exc:
            return err(f"start failed: {exc}")
        return ok({"started": target.creature_id})


@register_builtin("group_stop_node")
class GroupStopNodeTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "group_stop_node"

    @property
    def description(self) -> str:
        return (
            "Stop a running non-privileged creature in your group "
            "(does not remove it)"
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
        gctx, err_result = resolve_or_error(context)
        if err_result is not None:
            return err_result
        target = resolve_group_target(gctx, (args.get("creature_id") or "").strip())
        if target is None:
            return err(f"creature {args.get('creature_id')!r} not in your group")
        if target.is_privileged:
            return err(
                f"cannot start/stop privileged creature {target.name!r}; "
                "only the user can do that via Studio"
            )
        if not target.is_running:
            return err(f"creature {target.name!r} is not running")
        try:
            await gctx.engine.stop(target.creature_id)
        except Exception as exc:
            return err(f"stop failed: {exc}")
        return ok({"stopped": target.creature_id})
