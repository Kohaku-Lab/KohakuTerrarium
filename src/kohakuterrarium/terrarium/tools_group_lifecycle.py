"""Implement privileged tools that manage non-privileged worker lifecycles."""

import asyncio
from typing import Any

import kohakuterrarium.terrarium.group_hooks as group_hooks
from kohakuterrarium.builtins.tool_catalog import register_builtin
from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.core.events import create_creature_output_event
from kohakuterrarium.llm.profiles import resolve_controller_llm
from kohakuterrarium.terrarium.child_model import (
    model_payload,
    resolve_spawn_model,
    spawn_config,
    spawn_llm_args,
)
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
from kohakuterrarium.terrarium.tools_group_common import (
    err,
    ok,
    population_cap_error,
    resolve_or_error,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _caller_pwd(gctx: GroupContext) -> str:
    executor = getattr(gctx.caller.agent, "executor", None)
    if executor is not None:
        wd = getattr(executor, "_working_dir", None)
        if wd is not None:
            return str(wd)
    return ""


def _caller_llm(gctx: GroupContext) -> str | None:
    """Return the caller's model selector, or None when it names no profile."""
    get_ident = getattr(gctx.caller.agent, "llm_identifier", None)
    identifier = get_ident() if callable(get_ident) else ""
    if not identifier or resolve_controller_llm({}, llm=identifier) is None:
        return None
    return identifier


_LLM_PARAM = {
    "type": "string",
    "description": "Model selector; default is the config's model, else yours.",
}


@register_builtin("group_add_node")
class GroupAddNodeTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "group_add_node"

    @property
    def description(self) -> str:
        return (
            "Spawn a creature into your group from a config path. Not for "
            "one-shot private work - dispatch a sub-agent."
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
                "llm": _LLM_PARAM,
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
        name = (args.get("name") or "").strip()
        pwd = args.get("pwd") or _caller_pwd(gctx)
        cap_error = population_cap_error(gctx)
        if cap_error is not None:
            return cap_error
        caller = _caller_llm(gctx)
        try:
            loaded = load_agent_config(config_path)
            choice = resolve_spawn_model(loaded, args.get("llm"), caller)
        except Exception as exc:
            return err(f"failed to spawn creature from {config_path!r}: {exc}")
        try:
            # Joining at creation avoids a transient singleton graph and session.
            new = await gctx.engine.add_creature(
                spawn_config(config_path, loaded, choice),
                graph=gctx.caller.graph_id,
                pwd=pwd,
                name=name or None,
                is_privileged=False,
                parent_creature_id=gctx.caller.creature_id,
                io="none",
                **spawn_llm_args(choice, caller),
            )
        except Exception as exc:
            return err(f"failed to spawn creature from {config_path!r}: {exc}")

        await group_hooks.attach_session_store(
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
        checkpoint = getattr(gctx.engine, "checkpoint_graph", None)
        if checkpoint is not None:
            await checkpoint(new.graph_id)
        return ok(
            {
                "creature_id": new.creature_id,
                "name": new.name,
                "graph_id": new.graph_id,
                "parent_creature_id": new.parent_creature_id,
                "caller_graph_id": gctx.caller.graph_id,
                **model_payload(choice),
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
        return "Remove a creature from your group. Not for pausing one temporarily - use group_stop_node."

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
        try:
            target = resolve_group_target(gctx, ident)
        except Exception as exc:
            return err(str(exc))
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
        return "Start a stopped member of your group. Not for adding a new one - use group_add_node."

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
        try:
            target = resolve_group_target(gctx, ident)
        except Exception as exc:
            return err(str(exc))
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
            "Stop a running member without removing it. Not for permanent "
            "removal - use group_remove_node."
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
        try:
            target = resolve_group_target(gctx, ident)
        except Exception as exc:
            return err(str(exc))
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
            "Spawn a child creature linked to you and optionally hand it a "
            "first task; talk to it with group_send by name. Not for one-shot "
            "private work - dispatch a sub-agent."
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
                "llm": _LLM_PARAM,
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
        name = (args.get("name") or "").strip()
        caller = _caller_llm(gctx)
        try:
            loaded = load_agent_config(config_ref)
            choice = resolve_spawn_model(loaded, args.get("llm"), caller)
        except Exception as exc:
            return err(f"failed to spawn creature from {config_ref!r}: {exc}")
        try:
            child = await gctx.engine.add_creature(
                spawn_config(config_ref, loaded, choice),
                graph=gctx.caller.graph_id,
                pwd=pwd,
                name=name or None,
                is_privileged=False,
                parent_creature_id=gctx.caller.creature_id,
                io="none",
                **spawn_llm_args(choice, caller),
            )
        except Exception as exc:
            return err(f"failed to spawn creature from {config_ref!r}: {exc}")

        await group_hooks.attach_session_store(
            gctx.engine, child, config_path=config_ref, config_type="agent"
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
        checkpoint = getattr(gctx.engine, "checkpoint_graph", None)
        if checkpoint is not None:
            await checkpoint(child.graph_id)

        return ok(
            {
                "creature_id": child.creature_id,
                "name": child.name,
                "graph_id": child.graph_id,
                "task_delivered": delivered,
                "caller_graph_id": gctx.caller.graph_id,
                **model_payload(choice),
            }
        )


def _resolve_worker_target(gctx, ident: str, verb: str):
    """Resolve a non-privileged worker, or return a tool error."""
    try:
        target = resolve_group_target(gctx, ident)
    except Exception as exc:
        return None, err(str(exc))
    if target is None:
        return None, err(cross_cluster_target_error(gctx.engine, ident))
    if target.is_privileged:
        return None, err(
            f"cannot {verb} privileged creature {target.name!r}; "
            "only the user can do that via Studio"
        )
    return target, None


from kohakuterrarium.terrarium.tools_group_hold import (  # noqa: E402,F401
    GroupKillNodeTool,
    GroupPauseNodeTool,
    GroupResumeNodeTool,
)
