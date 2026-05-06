"""``group_channel`` — channel CRUD + per-creature wiring.

Notes on direction semantics:

The ``direction`` argument toggles the *target*'s edge, not the
caller's: ``direction="listen"`` makes ``creature_id`` listen on the
channel, ``direction="send"`` makes them able to send. The privileged
caller is wiring on someone else's behalf.

When the wire crosses graph boundaries (target is in a different
graph — typically a freshly-spawned worker still in its own singleton
graph), the call routes through :meth:`Terrarium.connect` which merges
the two graphs and pairs the caller as the counterparty (caller sends
when target listens; caller listens when target sends).
"""

from typing import Any

import kohakuterrarium.terrarium.channel_lifecycle as _lifecycle
import kohakuterrarium.terrarium.channels as _channels
import kohakuterrarium.terrarium.topology as _topo
from kohakuterrarium.builtins.tool_catalog import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.terrarium.events import EngineEvent, EventKind
from kohakuterrarium.terrarium.group_tool_context import (
    GroupContext,
    resolve_group_target,
)
from kohakuterrarium.terrarium.tools_group_common import err, ok, resolve_or_error


def _emit_intra_graph_wire_event(gctx: GroupContext, target_id: str) -> None:
    """Emit a TOPOLOGY_CHANGED event for an intra-graph wire/unwire so
    runtime-graph prompts on caller + target both refresh. The
    cross-graph case routes through ``engine.connect``/``disconnect``
    which already emit on their own.
    """
    gctx.engine._emit(
        EngineEvent(
            kind=EventKind.TOPOLOGY_CHANGED,
            graph_id=gctx.caller.graph_id,
            payload={
                "kind": "nothing",
                "old_graph_ids": [gctx.caller.graph_id],
                "new_graph_ids": [gctx.caller.graph_id],
                "affected": sorted({gctx.caller.creature_id, target_id}),
            },
        )
    )


@register_builtin("group_channel")
class GroupChannelTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "group_channel"

    @property
    def description(self) -> str:
        return "Create / delete / wire / unwire a channel in your group"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "delete", "wire", "unwire"],
                },
                "channel": {"type": "string"},
                "description": {"type": "string"},
                "creature_id": {"type": "string"},
                "direction": {"type": "string", "enum": ["send", "listen"]},
            },
            "required": ["action", "channel"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        gctx, err_result = resolve_or_error(context)
        if err_result is not None:
            return err_result
        action = (args.get("action") or "").strip()
        channel = (args.get("channel") or "").strip()
        if not action or not channel:
            return err("action and channel are required")

        if action == "create":
            if channel in gctx.graph.channels:
                return err(f"channel {channel!r} already exists in your graph")
            try:
                info = await gctx.engine.add_channel(
                    gctx.graph.graph_id,
                    channel,
                    description=args.get("description", "") or "",
                )
            except Exception as exc:
                return err(f"add_channel failed: {exc}")
            return ok(
                {
                    "created": info.name,
                    "caller_graph_id": gctx.caller.graph_id,
                }
            )

        if action == "delete":
            if channel not in gctx.graph.channels:
                return err(
                    f"channel {channel!r} not in your graph; "
                    f"available: {sorted(gctx.graph.channels)}"
                )
            try:
                delta = await gctx.engine.remove_channel(gctx.graph.graph_id, channel)
            except Exception as exc:
                return err(f"remove_channel failed: {exc}")
            return ok(
                {
                    "deleted": channel,
                    "caller_graph_id": gctx.caller.graph_id,
                    "delta_kind": delta.kind,
                }
            )

        # wire / unwire need a creature_id and direction
        target = resolve_group_target(gctx, (args.get("creature_id") or "").strip())
        if target is None:
            return err(f"creature {args.get('creature_id')!r} not in your group")
        direction = (args.get("direction") or "").strip()
        if direction not in ("send", "listen"):
            return err("direction must be 'send' or 'listen'")

        if action == "wire":
            return await _wire(gctx, target, channel, direction)
        if action == "unwire":
            return await _unwire(gctx, target, channel, direction)
        return err(f"unknown action {action!r}")


async def _wire(
    gctx: GroupContext,
    target: Any,
    channel: str,
    direction: str,
) -> ToolResult:
    # Cross-graph wiring: route through ``engine.connect`` which merges
    # the two graphs as a side effect. ``direction`` controls *target*'s
    # edge, so caller takes the opposite role: target listens → caller
    # sends, target sends → caller listens.
    if target.graph_id != gctx.graph.graph_id:
        sender = gctx.caller if direction == "listen" else target
        receiver = target if direction == "listen" else gctx.caller
        try:
            await gctx.engine.connect(
                sender.creature_id,
                receiver.creature_id,
                channel=channel,
            )
        except Exception as exc:
            return err(f"cross-graph wire failed: {exc}")
        return ok(
            {
                "wired": channel,
                "creature_id": target.creature_id,
                "direction": direction,
                "caller_graph_id": gctx.caller.graph_id,
                "merged": True,
            }
        )

    # Intra-graph wire: declare-channel-then-toggle-edge.
    graph = gctx.graph
    if channel not in graph.channels:
        try:
            await gctx.engine.add_channel(graph.graph_id, channel)
        except Exception as exc:
            return err(f"channel auto-create failed: {exc}")

    env = gctx.engine._environments.get(graph.graph_id)
    registry = getattr(env, "shared_channels", None) if env is not None else None
    if direction == "listen":
        _topo.set_listen(
            gctx.engine._topology, target.creature_id, channel, listening=True
        )
        if registry is not None:
            _channels.inject_channel_trigger(
                target.agent,
                subscriber_id=target.name,
                channel_name=channel,
                registry=registry,
                ignore_sender=target.name,
            )
        if channel not in target.listen_channels:
            target.listen_channels.append(channel)
    else:
        _topo.set_send(gctx.engine._topology, target.creature_id, channel, sending=True)
        if channel not in target.send_channels:
            target.send_channels.append(channel)
    _emit_intra_graph_wire_event(gctx, target.creature_id)
    return ok(
        {
            "wired": channel,
            "creature_id": target.creature_id,
            "direction": direction,
            "caller_graph_id": gctx.caller.graph_id,
        }
    )


async def _unwire(
    gctx: GroupContext,
    target: Any,
    channel: str,
    direction: str,
) -> ToolResult:
    if target.graph_id != gctx.graph.graph_id:
        return err(f"target {target.name!r} is not in your graph; nothing to unwire")
    graph = gctx.graph
    if channel not in graph.channels:
        return err(
            f"channel {channel!r} not in your graph; "
            f"available: {sorted(graph.channels)}"
        )

    if direction == "listen":
        _topo.set_listen(
            gctx.engine._topology, target.creature_id, channel, listening=False
        )
        _channels.remove_channel_trigger(
            target.agent,
            subscriber_id=target.name,
            channel_name=channel,
        )
        if channel in target.listen_channels:
            target.listen_channels.remove(channel)
    else:
        _topo.set_send(
            gctx.engine._topology, target.creature_id, channel, sending=False
        )
        if channel in target.send_channels:
            target.send_channels.remove(channel)

    # Removing one direction of an edge can disconnect two clusters
    # within the graph. ``engine.disconnect`` removes paired edges and
    # would over-shoot; we drop one direction only and then re-check
    # connectivity manually via the same private helper the topology
    # module uses internally. ``apply_split_bookkeeping`` then mirrors
    # the engine's split path — fresh envs, repointed creatures,
    # session-store coordination, and a ``TOPOLOGY_CHANGED`` event.
    # When no split happens we still emit so the runtime-graph prompt
    # block refreshes both peers' listen/send lists.
    delta = _topo._normalize_components(
        gctx.engine._topology,
        graph,
        affected={gctx.caller.creature_id, target.creature_id},
    )
    if delta.kind == "split":
        _lifecycle.apply_split_bookkeeping(gctx.engine, delta)
    else:
        _emit_intra_graph_wire_event(gctx, target.creature_id)

    return ok(
        {
            "unwired": channel,
            "creature_id": target.creature_id,
            "direction": direction,
            "caller_graph_id": gctx.caller.graph_id,
            "delta_kind": delta.kind,
        }
    )
