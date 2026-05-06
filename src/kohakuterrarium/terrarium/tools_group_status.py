"""``group_status`` — read snapshot of caller's group."""

from typing import Any

from kohakuterrarium.builtins.tool_catalog import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
import kohakuterrarium.terrarium.group_hooks as group_hooks
from kohakuterrarium.terrarium.group_tool_context import (
    GroupContext,
    compute_group,
)
from kohakuterrarium.terrarium.tools_group_common import (
    ok,
    resolve_or_error,
    serialize_channel_history,
)


@register_builtin("group_status")
class GroupStatusTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "group_status"

    @property
    def description(self) -> str:
        return (
            "Snapshot the caller's group: creatures, channels, output "
            "wires, spawnable catalog"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "include_history": {"type": "boolean"},
                "history_limit": {"type": "integer"},
                "include_spawnable": {"type": "boolean"},
            },
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        gctx, err_result = resolve_or_error(context)
        if err_result is not None:
            return err_result
        include_history = bool(args.get("include_history", False))
        history_limit = int(args.get("history_limit", 10) or 10)
        include_spawnable = bool(args.get("include_spawnable", True))

        engine = gctx.engine
        graph = gctx.graph
        env = engine._environments.get(graph.graph_id)
        registry = getattr(env, "shared_channels", None) if env is not None else None

        group = compute_group(gctx)
        creatures: list[dict[str, Any]] = []
        for cid, c in group.items():
            creatures.append(
                {
                    "creature_id": cid,
                    "name": c.name,
                    "running": c.is_running,
                    "is_privileged": c.is_privileged,
                    "in_my_graph": cid in graph.creature_ids,
                    "is_my_child": (
                        getattr(c, "parent_creature_id", None)
                        == gctx.caller.creature_id
                    ),
                    "graph_id": c.graph_id,
                    "listen_channels": list(c.listen_channels),
                    "send_channels": list(c.send_channels),
                }
            )

        channels: list[dict[str, Any]] = []
        for name in sorted(graph.channels):
            info = graph.channels[name]
            ch = registry.get(name) if registry is not None else None
            entry: dict[str, Any] = {
                "name": name,
                "description": info.description,
                "listeners": sorted(
                    cid
                    for cid, listens in graph.listen_edges.items()
                    if name in listens
                ),
                "senders": sorted(
                    cid for cid, sends in graph.send_edges.items() if name in sends
                ),
            }
            if include_history and ch is not None:
                entry["history"] = serialize_channel_history(ch, history_limit)
            channels.append(entry)

        output_edges: list[dict[str, Any]] = []
        for cid in graph.creature_ids:
            try:
                edges = engine.list_output_wiring(cid)
            except Exception:
                edges = []
            for edge in edges:
                ed = dict(edge)
                ed["from"] = cid
                output_edges.append(ed)

        result: dict[str, Any] = {
            "graph_id": graph.graph_id,
            "self": {
                "creature_id": gctx.caller.creature_id,
                "name": gctx.caller.name,
                "is_privileged": gctx.caller.is_privileged,
            },
            "creatures": creatures,
            "channels": channels,
            "output_edges": output_edges,
        }

        if include_spawnable:
            result["spawnable"] = _list_spawnable_for_caller(gctx)

        return ok(result)


def _list_spawnable_for_caller(gctx: GroupContext) -> list[dict[str, Any]]:
    workspace = group_hooks.resolve_workspace(gctx.engine, gctx.caller)
    return group_hooks.list_spawnable(workspace)
