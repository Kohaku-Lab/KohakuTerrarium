from __future__ import annotations

"""
Terrarium session persistence helpers.

Handles attaching a SessionStore to a running terrarium (creatures,
root agent, channels) and rebuilding Conversation objects from saved
message dicts on resume.
"""


from typing import TYPE_CHECKING, Any

from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.runtime import TerrariumRuntime

logger = get_logger(__name__)


def build_conversation_from_messages(messages: list[dict]) -> Conversation:
    """Build a Conversation from a list of message dicts (for resume)."""
    conv = Conversation()
    for msg in messages:
        kwargs: dict[str, Any] = {}
        if msg.get("tool_calls"):
            kwargs["tool_calls"] = msg["tool_calls"]
        if msg.get("tool_call_id"):
            kwargs["tool_call_id"] = msg["tool_call_id"]
        if msg.get("name"):
            kwargs["name"] = msg["name"]
        conv.append(msg.get("role", "user"), msg.get("content", ""), **kwargs)
    return conv


def attach_session_store(runtime: TerrariumRuntime, store: Any) -> None:
    """Attach a SessionStore to all creatures, root agent, and channels.

    Must be called AFTER start() (when creatures exist) but works
    at any time during the runtime lifecycle.
    """
    runtime._session_store = store

    # Attach to all creature agents
    for name, handle in runtime._creatures.items():
        handle.agent.attach_session_store(store)

    # Attach to root agent
    if runtime._root_agent is not None:
        runtime._root_agent.attach_session_store(store)

    # Register on_send callbacks for all shared channels
    for ch in runtime.environment.shared_channels._channels.values():

        def _make_cb(ch_name: str):
            def _cb(channel_name: str, message: Any) -> None:
                try:
                    ts = (
                        message.timestamp.isoformat()
                        if hasattr(message.timestamp, "isoformat")
                        else str(message.timestamp)
                    )
                    store.save_channel_message(
                        channel_name,
                        {
                            "sender": message.sender,
                            "content": (
                                message.content
                                if isinstance(message.content, str)
                                else str(message.content)
                            ),
                            "msg_id": message.message_id,
                            "ts": ts,
                        },
                    )
                except Exception as e:
                    logger.debug(
                        "Channel message persistence error", error=str(e), exc_info=True
                    )

            return _cb

        ch.on_send(_make_cb(ch.name))

    # Inject resume data if present (conversations + scratchpads)
    if hasattr(runtime, "_pending_resume_data") and runtime._pending_resume_data:
        for name, data in runtime._pending_resume_data.items():
            agent = runtime.get_creature_agent(name)
            if name == "root" and agent is None:
                agent = runtime._root_agent
            if not agent:
                continue

            saved_messages = data.get("conversation")
            if saved_messages and isinstance(saved_messages, list):
                agent.controller.conversation = build_conversation_from_messages(
                    saved_messages
                )
                logger.info("Conversation restored", agent=name)

            pad = data.get("scratchpad", {})
            if pad and agent.session:
                for k, v in pad.items():
                    agent.session.scratchpad.set(k, v)

        runtime._pending_resume_data = None

    # Set resume events on root agent for output replay
    if (
        hasattr(runtime, "_pending_resume_events")
        and runtime._pending_resume_events
        and runtime._root_agent is not None
    ):
        root_events = runtime._pending_resume_events.get("root")
        if root_events:
            runtime._root_agent._pending_resume_events = root_events
            logger.info("Resume events set on root agent", count=len(root_events))
        runtime._pending_resume_events = None

    # Set resumable triggers on root agent
    if (
        hasattr(runtime, "_pending_resume_triggers")
        and runtime._pending_resume_triggers
        and runtime._root_agent is not None
    ):
        root_triggers = runtime._pending_resume_triggers.get("root")
        if root_triggers:
            runtime._root_agent._pending_resume_triggers = root_triggers
            logger.info(
                "Resumable triggers set on root agent", count=len(root_triggers)
            )
        runtime._pending_resume_triggers = None

    logger.info(
        "Session store attached to terrarium",
        creatures=list(runtime._creatures.keys()),
        channels=len(runtime.environment.shared_channels._channels),
    )
