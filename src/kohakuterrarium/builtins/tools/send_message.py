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


def _enforce_send_edge_in_engine_context(
    context: ToolContext, channel_name: str
) -> str | None:
    """When the caller is part of a Terrarium engine graph, require a
    declared send edge for ``channel_name``. Returns an error message
    when the caller cannot send on the channel, or ``None`` to allow.

    The sub-agent path (no engine in env) returns ``None`` — private
    channels in :class:`Session.channels` keep their permissive
    behavior.
    """
    if context is None or context.environment is None:
        return None
    engine_ref = context.environment.get("terrarium_engine")
    engine = engine_ref() if isinstance(engine_ref, weakref.ref) else engine_ref
    if engine is None:
        return None
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
        return None
    graph = engine._topology.graphs.get(creature.graph_id)
    if graph is None or channel_name not in graph.channels:
        return None
    sends = graph.send_edges.get(creature.creature_id, set())
    if channel_name in sends:
        return None
    return (
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

        # Determine sender from context or default
        sender = "unknown"
        if context:
            sender = context.agent_name

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

        # Resolve channel: private session first, shared environment second
        channel = None
        chan_registry = None

        # 1. Check creature's private channels (sub-agent channels)
        if context and context.session:
            chan_registry = context.session.channels
            channel = chan_registry.get(channel_name)

        # 2. Check environment's shared channels (inter-creature channels)
        if channel is None and context and context.environment:
            channel = context.environment.shared_channels.get(channel_name)
            if channel is not None:
                chan_registry = context.environment.shared_channels

        # 3. Fallback for no-context usage (standalone / testing)
        if channel is None and not context:
            fallback_registry = get_channel_registry()
            channel = fallback_registry.get(channel_name)
            if channel is None:
                channel = fallback_registry.get_or_create(
                    channel_name, channel_type=channel_type
                )
            chan_registry = fallback_registry

        # 4. Channel didn't resolve. Anyone with an environment-aware
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

        # Engine-context send-edge gate: when the caller is in a
        # Terrarium engine graph and the channel is one of the graph's
        # shared channels, require a declared send edge. Sub-agent
        # private channels in ``Session.channels`` are unaffected.
        if context is not None and context.environment is not None:
            shared = context.environment.shared_channels.get(channel_name)
            if shared is not None:
                deny = _enforce_send_edge_in_engine_context(context, channel_name)
                if deny is not None:
                    return ToolResult(error=deny)

        # Send message
        msg = ChannelMessage(
            sender=sender,
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
