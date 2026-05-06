"""``group_send`` (point-to-point) and ``send_channel`` (broadcast).

``group_send`` is fire-and-forget: it pushes a synthetic
``creature_output`` event into the target's controller queue, with a
``[direct from <source>] ...`` prompt override. There's no shared
state, no channel history, no listeners. The receiver reads it the
same way it reads an output-wire delivery.

``send_channel`` is the wired-channel broadcast equivalent: writes to
the live :class:`ChannelRegistry`, so every listener gets it and
channel history records it. The caller must be wired as a sender on
the channel — we surface the available outgoing channels in the
rejection message.
"""

import asyncio
import uuid
from typing import Any

from kohakuterrarium.builtins.tool_catalog import register_builtin
from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.core.events import create_creature_output_event
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.terrarium.group_tool_context import resolve_group_target
from kohakuterrarium.terrarium.tools_group_common import err, ok, resolve_or_error
from kohakuterrarium.utils.logging import get_logger

_logger = get_logger(__name__)


def _log_send_error(task: asyncio.Task, source: str, target: str) -> None:
    """Done-callback for ``group_send`` delivery tasks.

    Mirrors ``terrarium.output_wiring._log_task_error`` — surfaces
    receiver-side exceptions at warning-level so they don't disappear
    into the asyncio "Task exception was never retrieved" stream.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    _logger.warning(
        "group_send delivery failed inside receiver",
        source=source,
        target=target,
        error=str(exc),
    )


@register_builtin("group_send")
class GroupSendTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "group_send"

    @property
    def description(self) -> str:
        return (
            "Send a one-shot message directly to a creature in your group "
            "(privileged caller: any target; non-privileged: privileged target only)"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["to", "message"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        # ``group_send`` is registered on every engine creature; the
        # privilege gate is per-call (non-privileged → privileged
        # target only) rather than at resolver time.
        gctx, err_result = resolve_or_error(context, require_privileged=False)
        if err_result is not None:
            return err_result
        to_id = (args.get("to") or "").strip()
        message = args.get("message")
        if not to_id or message is None:
            return err("'to' and 'message' are required")
        target = resolve_group_target(gctx, to_id)
        if target is None:
            return err(f"target {to_id!r} not in your group")
        if not target.is_running:
            return err(f"target {target.name!r} is not running")
        if not gctx.caller.is_privileged and not target.is_privileged:
            return err(
                "non-privileged creatures can only group_send to privileged "
                f"creatures; {target.name!r} is not privileged"
            )
        prompt = f"[direct from {gctx.caller.name}] {message}"
        event = create_creature_output_event(
            source=gctx.caller.name,
            target=target.name,
            content=message,
            with_content=True,
            source_event_type="group_send",
            turn_index=0,
            prompt_override=prompt,
        )
        task = asyncio.create_task(
            target.agent._process_event(event),
            name=f"group_send_{gctx.caller.name}_to_{target.name}",
        )
        task.add_done_callback(
            lambda t, src=gctx.caller.name, tgt=target.name: _log_send_error(
                t, src, tgt
            )
        )
        # Generate a tracking id for the model's reference; the
        # underlying TriggerEvent doesn't carry one of its own.
        message_id = f"direct_{uuid.uuid4().hex[:12]}"
        return ok(
            {
                "delivered": True,
                "to": target.creature_id,
                "message_id": message_id,
            }
        )


@register_builtin("send_channel")
class SendChannelTool(BaseTool):
    needs_context = True

    @property
    def tool_name(self) -> str:
        return "send_channel"

    @property
    def description(self) -> str:
        return "Broadcast a message on a wired channel in your graph"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "message": {"type": "string"},
                "metadata": {"type": "object"},
                "reply_to": {"type": "string"},
            },
            "required": ["channel", "message"],
        }

    async def _execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        # ``send_channel`` is registered on every engine creature; the
        # send-edge requirement (below) gates calls per-channel rather
        # than at resolver time.
        gctx, err_result = resolve_or_error(context, require_privileged=False)
        if err_result is not None:
            return err_result
        channel_name = (args.get("channel") or "").strip()
        message = args.get("message")
        if not channel_name or message is None:
            return err("'channel' and 'message' are required")
        graph = gctx.graph
        if channel_name not in graph.channels:
            return err(
                f"channel {channel_name!r} does not exist in your graph; "
                f"available: {sorted(graph.channels)}"
            )
        sends = graph.send_edges.get(gctx.caller.creature_id, set())
        if channel_name not in sends:
            # Privileged callers can self-wire; non-privileged need a
            # privileged creature to wire them.
            if gctx.caller.is_privileged:
                return err(
                    f"you are not wired as sender on {channel_name!r}; "
                    f"your outgoing channels: {sorted(sends)}. "
                    f"Self-wire via group_channel(action='wire', "
                    f"direction='send', channel={channel_name!r}, "
                    f"creature_id={gctx.caller.creature_id!r})."
                )
            return err(
                f"you are not wired as sender on {channel_name!r}; "
                f"your outgoing channels: {sorted(sends)}. "
                f"Ask the privileged creature to wire you via "
                f"group_channel(action='wire', direction='send', "
                f"channel={channel_name!r}, creature_id={gctx.caller.creature_id!r})."
            )
        env = gctx.engine._environments.get(graph.graph_id)
        registry = getattr(env, "shared_channels", None) if env is not None else None
        if registry is None:
            return err("graph has no live channel registry")
        ch = registry.get(channel_name)
        if ch is None:
            return err(f"channel {channel_name!r} not registered live")
        metadata = args.get("metadata") or {}
        reply_to = args.get("reply_to") or None
        msg = ChannelMessage(
            sender=gctx.caller.name,
            content=message,
            metadata=metadata if isinstance(metadata, dict) else {},
            reply_to=reply_to,
        )
        await ch.send(msg)
        return ok(
            {
                "channel": channel_name,
                "message_id": msg.message_id,
                "caller_graph_id": gctx.caller.graph_id,
            }
        )
