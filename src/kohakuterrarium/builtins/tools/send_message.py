"""
Send message tool - send to a named channel.
"""

import json
import weakref
from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.core.session import get_channel_registry
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _check_engine_send_edge(
    context: ToolContext, channel_name: str
) -> tuple[bool, str | None]:
    """Inspect the caller's engine graph for ``channel_name``.

    Returns ``(in_topology, deny_msg)`` where:

    - ``in_topology`` is True iff the caller is an engine-backed
      creature inside a graph that declares ``channel_name`` as a
      topology channel. Callers use this flag to prefer the topology
      channel over a same-name private session-channel shadow.
    - ``deny_msg`` is a non-empty error string iff the caller is in
      such a graph but is not wired as sender on the channel.
      ``None`` means either the caller is outside an engine graph
      (sub-agent path) or is properly wired — both cases allow the
      send to proceed.
    """
    if context is None or context.environment is None:
        return False, None
    engine_ref = context.environment.get("terrarium_engine")
    engine = engine_ref() if isinstance(engine_ref, weakref.ref) else engine_ref
    if engine is None:
        return False, None
    creature = None
    by_id = engine._creatures.get(context.agent_name)
    if by_id is not None:
        creature = by_id
    else:
        for c in engine._creatures.values():
            if c.name == context.agent_name:
                creature = c
                break
            cfg = getattr(c.agent, "config", None)
            if cfg is not None and getattr(cfg, "name", None) == context.agent_name:
                creature = c
                break
    if creature is None:
        return False, None
    graph = engine._topology.graphs.get(creature.graph_id)
    if graph is None or channel_name not in graph.channels:
        return False, None
    sends = graph.send_edges.get(creature.creature_id, set())
    if channel_name in sends:
        return True, None
    return True, (
        f"You are not wired as sender on channel '{channel_name}'. "
        f"Your outgoing channels: {sorted(sends)}. "
        f"Ask the privileged creature to wire you via "
        f"group_channel(action='wire', direction='send', "
        f"channel='{channel_name}', creature_id=you)."
    )


@register_builtin("send_message")
class SendMessageTool(BaseTool):
    """Send a message to a named channel for agent-to-agent communication."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "send_message"

    @property
    def description(self) -> str:
        return "Send a message to a named channel"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        """Send message to channel."""
        channel_name = args.get("channel", "")
        message = args.get("message", "") or args.get("content", "")
        channel_type = args.get("channel_type", "queue")
        reply_to = args.get("reply_to", None) or None

        if not channel_name:
            return ToolResult(error="Channel name is required")
        if not message:
            return ToolResult(error="Message content is required")

        # Determine sender from context or default. ``sender`` is the
        # display name; ``sender_id`` is the stable creature_id used for
        # self-echo filtering when two creatures share a config name.
        sender = "unknown"
        sender_id: str | None = None
        if context:
            sender = context.agent_name
            agent_obj = getattr(context, "agent", None)
            if agent_obj is not None:
                sender_id = getattr(agent_obj, "_creature_id", None) or getattr(
                    agent_obj, "creature_id", None
                )

        # Parse metadata if provided
        metadata: dict[str, Any] = {}
        raw_metadata = args.get("metadata", "")
        if raw_metadata:
            try:
                metadata = (
                    json.loads(raw_metadata)
                    if isinstance(raw_metadata, str)
                    else raw_metadata
                )
            except json.JSONDecodeError:
                pass

        # Resolve channel.  Order is load-bearing for the send-edge
        # gate: if the caller is in a Terrarium engine graph and the
        # channel name is declared in that graph's topology, the gate
        # MUST fire before private-channel resolution can pick a
        # session-level shadow of the same name and quietly bypass it.
        # A creature with a private "ops" channel that collides with a
        # topology "ops" channel they aren't wired to as sender would
        # otherwise broadcast into their own queue and return success.
        channel = None
        chan_registry = None
        in_graph_topology, deny = _check_engine_send_edge(context, channel_name)
        if deny is not None:
            return ToolResult(error=deny)

        # 1. Graph-topology channel wins when the name exists in the
        #    caller's graph — it is the cluster-visible recipient, and
        #    a private session-channel shadow must not silently capture
        #    sends that the LLM intended for the topology channel.
        if in_graph_topology and context and context.environment:
            channel = context.environment.shared_channels.get(channel_name)
            if channel is not None:
                chan_registry = context.environment.shared_channels

        # 2. Check creature's private channels (sub-agent channels)
        if channel is None and context and context.session:
            chan_registry = context.session.channels
            channel = chan_registry.get(channel_name)

        # 3. Check environment's shared channels (inter-creature channels)
        if channel is None and context and context.environment:
            channel = context.environment.shared_channels.get(channel_name)
            if channel is not None:
                chan_registry = context.environment.shared_channels

        # 4. Fallback for no-context usage (standalone / testing)
        if channel is None and not context:
            fallback_registry = get_channel_registry()
            channel = fallback_registry.get(channel_name)
            if channel is None:
                channel = fallback_registry.get_or_create(
                    channel_name, channel_type=channel_type
                )
            chan_registry = fallback_registry

        # 5. Channel didn't resolve. Anyone with an environment-aware
        # context (i.e. an engine-backed creature, top-level OR
        # sub-agent) is talking from inside a graph, and graphs only
        # have channels that were explicitly declared. Silent
        # auto-create for invented names lets LLMs send to dead-letter
        # queues — ``report_to_root``, ``test``, ``tasks`` etc. — and
        # report success without anyone reading the message. Refuse it
        # and surface the real channel list so the agent can correct.
        if channel is None:
            shared_available: list[dict[str, str]] = []
            private_available: list[dict[str, str]] = []
            if context and context.environment:
                shared_available.extend(
                    context.environment.shared_channels.get_channel_info()
                )
            if context and context.session:
                private_available.extend(context.session.channels.get_channel_info())

            if context is not None:
                # Engine-backed path: any unknown name is a confabulation.
                avail_lines = []
                if shared_available:
                    avail_lines.append(
                        "shared: "
                        + ", ".join(
                            f"`{c['name']}` ({c['type']})" for c in shared_available
                        )
                    )
                if private_available:
                    avail_lines.append(
                        "private: "
                        + ", ".join(
                            f"`{c['name']}` ({c['type']})" for c in private_available
                        )
                    )
                avail_str = " | ".join(avail_lines) or "none"
                return ToolResult(
                    error=(
                        f"Channel '{channel_name}' does not exist. "
                        f"Available channels — {avail_str}. "
                        "Pick one of the listed channels exactly as written; "
                        "do NOT invent a name (the tool will keep rejecting "
                        "invented names). If you genuinely need a new "
                        "channel, ask the user to create it via the graph "
                        "editor."
                    )
                )

        # Send message
        # (Engine-context send-edge gate fired earlier — before channel
        # resolution — so a private session-channel cannot shadow a
        # graph-topology channel and bypass the wiring check.)
        msg = ChannelMessage(
            sender=sender,
            sender_id=sender_id,
            content=message,
            metadata=metadata,
            reply_to=reply_to,
        )
        await channel.send(msg)

        logger.debug("Message sent", channel=channel_name, sender=sender)
        content_preview = message[:60].replace("\n", " ")
        return ToolResult(
            output=(
                f"Delivered to '{channel_name}' (id: {msg.message_id}). "
                f"Content: \"{content_preview}{'...' if len(message) > 60 else ''}\". "
                f"Message delivered successfully, no further action needed for this send."
            ),
            exit_code=0,
        )
