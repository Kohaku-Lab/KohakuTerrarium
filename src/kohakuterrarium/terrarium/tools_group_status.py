"""``group_status`` — read snapshot of caller's group."""

from typing import Any

import kohakuterrarium.terrarium.group_hooks as group_hooks
from kohakuterrarium.builtins.tool_catalog import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
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
    # ``group_status`` owns the team-building paradigm hint for the
    # whole privileged ``group_*`` surface, so its contribution must
    # land at the top of the ``## Tool guidance`` block ahead of any
    # alphabetical neighbours that might also opt in later.
    prompt_contribution_bucket = "first"

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

    def prompt_contribution(self) -> str | None:
        # Anchors the team-building paradigm for any privileged caller.
        # ``group_*`` tools are registered as a set on privileged
        # creatures, so contributing the paradigm prose from a single
        # well-known entry point (``group_status`` — the natural "look
        # at your team" tool) avoids fragmenting guidance across every
        # sibling tool. Non-privileged creatures never see this because
        # the tool itself is not registered for them.
        return (
            "The `group_*` tools let you build and run a **team of "
            "creatures** for heavy or parallel work that benefits from "
            "more than one independent agent context. Reach for a team "
            "(over a sub-agent) when workers need their own toolset / "
            "cwd / model, when they should react to each other "
            "asynchronously through channels rather than as nested "
            "calls, or when the work fans out into many sub-tasks you "
            "want to dispatch in parallel and recombine. Sub-agents "
            "stay better for one-shot delegated computations — "
            "spawning a creature has higher fixed cost.\n\n"
            "**Team-building workflow** (call in order):\n\n"
            "  1. `group_status` — snapshot the current group. Pass "
            "`include_spawnable=true` to see which creature configs "
            "you can spawn (e.g. `@<pkg>/creatures/<name>`). Run this "
            "before dispatching so you know who is already wired and "
            "what is available.\n"
            "  2. `group_add_node(config_path, name?, pwd?)` — spawn a "
            "worker into your graph. Pass `pwd=` to give the worker "
            "its own working directory (sandbox, per-task subdir, …); "
            "omit to inherit yours. The worker starts isolated — "
            "nothing reaches it until you wire it.\n"
            '  3. `group_channel(action="create", channel, '
            "description)` — declare a channel for routing messages. "
            "Channels are broadcast: every listener receives every "
            "send. A creature's own messages are filtered out, so do "
            "not rely on self-loops to drive iteration.\n"
            '  4. `group_channel(action="wire", channel, '
            "creature_id, direction)` — attach each creature as "
            '`"listen"`, `"send"`, or `"both"`. Typical '
            "dispatch pattern: you `send` and workers `listen` on a "
            "task channel; workers `send` and you `listen` on a "
            "results channel.\n"
            '  5. `group_wire(action="add", from_id, to_id, '
            "with_content?)` — optional output-wire when you want a "
            "worker's final turn text auto-delivered to another "
            "creature without an explicit send. Use channels for "
            "dispatch, wires for pipeline hand-off.\n"
            "  6. `send_channel(channel, message)` to broadcast, or "
            "`group_send(to, message)` for a one-shot direct delivery "
            "to a single creature. From this point workers wake up "
            "and run their own loops in parallel; you stay free to "
            "plan, dispatch more work, or read replies on your listen "
            "channels.\n\n"
            "**Lifecycle and teardown.** When a worker is no longer "
            "needed: `group_stop_node(creature_id)` pauses it without "
            "removing (session preserved; resume with "
            '`group_start_node`). `group_channel(action="unwire", '
            "…)` drops a creature's edge on a channel, and "
            '`group_channel(action="delete", channel)` drops the '
            'channel entirely. `group_wire(action="remove", …)` '
            "undoes an output-wire. `group_remove_node(creature_id)` "
            "destroys a non-privileged worker outright. Tear down "
            "channels and wires touching a creature **before** "
            "removing it for a clean snapshot.\n\n"
            "**Reading the snapshot.** Each creature carries a "
            "`status` field — one of `not_started`, `idle`, `busy`, "
            "`stopped`, `error`. `busy` means a controller turn is in "
            "flight (your dispatch queues rather than preempts); "
            "`idle` means the worker is ready for the next message; "
            "`error` means its input loop crashed and it needs a "
            "restart."
        )

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
                    "status": c.status,
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
