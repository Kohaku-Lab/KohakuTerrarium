"""``group_wire`` — direct output-wire add/remove."""

from typing import Any

import kohakuterrarium.terrarium.channels as _channels
from kohakuterrarium.builtins.tool_catalog import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.terrarium.group_tool_context import resolve_group_target
from kohakuterrarium.terrarium.tools_group_common import err, ok, resolve_or_error


@register_builtin("group_wire")
class GroupWireTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "group_wire"

    @property
    def description(self) -> str:
        return "Add or remove a direct output-wire edge between creatures in your group"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "remove"]},
                "to": {"type": "string"},
                "from": {"type": "string"},
                "with_content": {"type": "boolean"},
                "prompt": {"type": "string"},
                "prompt_format": {
                    "type": "string",
                    "enum": ["simple", "jinja"],
                },
                "allow_self_trigger": {"type": "boolean"},
                "edge_id": {"type": "string"},
            },
            "required": ["action"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        gctx, err_result = resolve_or_error(context)
        if err_result is not None:
            return err_result
        action = (args.get("action") or "").strip()
        from_id = (args.get("from") or "").strip() or gctx.caller.creature_id
        from_creature = resolve_group_target(gctx, from_id)
        if from_creature is None:
            return err(f"from creature {from_id!r} not in your group")

        if action == "add":
            to_id = (args.get("to") or "").strip()
            if not to_id:
                return err("'to' is required for action='add'")
            to_creature = resolve_group_target(gctx, to_id)
            if to_creature is None:
                return err(f"to creature {to_id!r} not in your group")
            if from_creature.graph_id != to_creature.graph_id:
                try:
                    await _channels.ensure_same_graph(
                        gctx.engine, from_creature, to_creature
                    )
                except Exception as exc:
                    return err(f"cross-graph merge failed: {exc}")
            target: dict[str, Any] = {
                "to": to_creature.name,
                "with_content": bool(args.get("with_content", True)),
            }
            if args.get("prompt"):
                target["prompt"] = args["prompt"]
            if args.get("prompt_format"):
                target["prompt_format"] = args["prompt_format"]
            if args.get("allow_self_trigger") is not None:
                target["allow_self_trigger"] = bool(args["allow_self_trigger"])
            try:
                edge_id = await gctx.engine.wire_output(
                    from_creature.creature_id, target
                )
            except Exception as exc:
                return err(f"wire_output failed: {exc}")
            return ok(
                {
                    "edge_id": edge_id,
                    "from": from_creature.creature_id,
                    "to": to_creature.creature_id,
                    "caller_graph_id": gctx.caller.graph_id,
                }
            )

        if action == "remove":
            edge_id = (args.get("edge_id") or "").strip()
            if not edge_id:
                return err("'edge_id' is required for action='remove'")
            try:
                removed = await gctx.engine.unwire_output(
                    from_creature.creature_id, edge_id
                )
            except Exception as exc:
                return err(f"unwire_output failed: {exc}")
            return ok(
                {
                    "removed": removed,
                    "edge_id": edge_id,
                    "from": from_creature.creature_id,
                    "caller_graph_id": gctx.caller.graph_id,
                }
            )

        return err(f"unknown action {action!r}")
