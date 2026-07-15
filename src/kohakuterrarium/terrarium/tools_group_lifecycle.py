"""Implement privileged tools that manage non-privileged worker lifecycles."""

import asyncio
from typing import Any

import kohakuterrarium.terrarium.group_hooks as group_hooks
from kohakuterrarium.builtins.tool_catalog import register_builtin
from kohakuterrarium.core.events import create_creature_output_event
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.terrarium.events import EngineEvent, EventKind
from kohakuterrarium.terrarium.group_tool_context import (
    GroupContext,
    cross_cluster_target_error,
    resolve_group_target,
)
from kohakuterrarium.terrarium.tools_group_common import err, ok, resolve_or_error
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _caller_pwd(gctx: GroupContext) -> str:
    executor = getattr(gctx.caller.agent, "executor", None)
    if executor is not None:
        wd = getattr(executor, "_working_dir", None)
        if wd is not None:
            return str(wd)
    return ""


def _resolve_worker_target(
    gctx: GroupContext, ident: str, verb: str
) -> tuple[Any, ToolResult | None]:
    """Resolve a worker while reserving privileged lifecycle control for Studio."""
    target = resolve_group_target(gctx, ident)
    if target is None:
        return None, err(cross_cluster_target_error(gctx.engine, ident))
    if target.is_privileged:
        return None, err(
            f"cannot {verb} privileged creature {target.name!r}; "
            "only the user can do that via Studio"
        )
    return target, None


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
        # Configuration loading resolves and validates package references.

        pwd = args.get("pwd") or _caller_pwd(gctx)
        try:
            # Joining at creation avoids a transient singleton graph and session.
            new = await gctx.engine.add_creature(
                config_path,
                graph=gctx.caller.graph_id,
                llm=args.get("llm"),
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
        ident = (args.get("creature_id") or "").strip()
        target = resolve_group_target(gctx, ident)
        if target is None:
            return err(cross_cluster_target_error(gctx.engine, ident))
        if target.is_privileged:
            return err(
                f"cannot remove privileged creature {target.name!r}; "
                "only the user can do that via Studio"
            )
        # Removal invalidates the creature handle needed for the unlink event.
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
        ident = (args.get("creature_id") or "").strip()
        target = resolve_group_target(gctx, ident)
        if target is None:
            return err(cross_cluster_target_error(gctx.engine, ident))
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
            "Force-stop a running non-privileged creature: full teardown "
            "(NOT a warm pause — use group_pause_node for that). Session "
            "preserved; restart with group_start_node"
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
        ident = (args.get("creature_id") or "").strip()
        target = resolve_group_target(gctx, ident)
        if target is None:
            return err(cross_cluster_target_error(gctx.engine, ident))
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


def _default_link_channel(caller: Any, child: Any) -> str:
    """Return the stable channel name for a creator-child link."""
    return f"link-{caller.creature_id}-{child.creature_id}"


def _log_spawn_task_error(task: asyncio.Task, child_name: str) -> None:
    """Log failures from the detached initial-task delivery."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    logger.warning(
        "group_spawn_child initial task delivery failed inside child",
        child=child_name,
        error=str(exc),
    )


def _deliver_initial_task(caller: Any, child: Any, task: str) -> bool:
    """Deliver the initial task directly before channel listening is ready."""
    prompt = f"[direct from {caller.name}] {task}"
    event = create_creature_output_event(
        source=caller.name,
        target=child.name,
        content=task,
        with_content=True,
        source_event_type="group_spawn_child",
        turn_index=0,
        prompt_override=prompt,
    )
    handle = asyncio.create_task(
        child.agent._process_event(event),
        name=f"spawn_task_{child.creature_id}",
    )
    handle.add_done_callback(lambda t, name=child.name: _log_spawn_task_error(t, name))
    return True


@register_builtin("group_spawn_child")
class GroupSpawnChildTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "group_spawn_child"

    @property
    def description(self) -> str:
        return (
            "Spawn a child creature, auto-wire a default two-way channel "
            "back to you, and optionally hand it a first task — one call "
            "to use a graph creature as an unbounded sub-agent"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        # Tool schema construction falls back to this native definition.
        return {
            "type": "object",
            "properties": {
                "config_ref": {
                    "type": "string",
                    "description": "Creature config path or @pkg/creatures/<name>",
                },
                "task": {
                    "type": "string",
                    "description": "Optional first task, delivered immediately.",
                },
                "name": {
                    "type": "string",
                    "description": "Optional display name for the child.",
                },
                "llm": {"type": "string"},
                "pwd": {"type": "string"},
            },
            "required": ["config_ref"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        gctx, err_result = resolve_or_error(context)
        if err_result is not None:
            return err_result
        config_ref = (args.get("config_ref") or "").strip()
        if not config_ref:
            return err("config_ref is required")

        pwd = args.get("pwd") or _caller_pwd(gctx)
        try:
            child = await gctx.engine.add_creature(
                config_ref,
                graph=gctx.caller.graph_id,
                llm=args.get("llm"),
                pwd=pwd,
                is_privileged=False,
                parent_creature_id=gctx.caller.creature_id,
            )
        except Exception as exc:
            return err(f"failed to spawn creature from {config_ref!r}: {exc}")

        if name := (args.get("name") or "").strip():
            group_hooks.apply_creature_name(child, name)
        group_hooks.attach_session_store(
            gctx.engine, child, config_path=config_ref, config_type="agent"
        )

        # Reciprocal connections make both endpoints senders and listeners.
        channel = _default_link_channel(gctx.caller, child)
        try:
            await gctx.engine.connect(
                gctx.caller.creature_id, child.creature_id, channel=channel
            )
            await gctx.engine.connect(
                child.creature_id, gctx.caller.creature_id, channel=channel
            )
        except Exception as exc:
            return err(
                f"spawned {child.creature_id} but default channel wiring "
                f"failed: {exc}"
            )

        gctx.engine._emit(
            EngineEvent(
                kind=EventKind.PARENT_LINK_CHANGED,
                creature_id=child.creature_id,
                graph_id=child.graph_id,
                payload={"parent": gctx.caller.creature_id, "change": "added"},
            )
        )

        task = (args.get("task") or "").strip()
        delivered = _deliver_initial_task(gctx.caller, child, task) if task else False

        return ok(
            {
                "creature_id": child.creature_id,
                "name": child.name,
                "graph_id": child.graph_id,
                "channel": channel,
                "task_delivered": delivered,
                "caller_graph_id": gctx.caller.graph_id,
            }
        )


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
        gctx, err_result = resolve_or_error(context)
        if err_result is not None:
            return err_result
        ident = (args.get("creature_id") or "").strip()
        target, terr = _resolve_worker_target(gctx, ident, "pause")
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
        gctx, err_result = resolve_or_error(context)
        if err_result is not None:
            return err_result
        ident = (args.get("creature_id") or "").strip()
        target, terr = _resolve_worker_target(gctx, ident, "resume")
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
        gctx, err_result = resolve_or_error(context)
        if err_result is not None:
            return err_result
        ident = (args.get("creature_id") or "").strip()
        target, terr = _resolve_worker_target(gctx, ident, "kill")
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
