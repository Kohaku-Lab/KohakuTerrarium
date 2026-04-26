"""
Terrarium session persistence helpers.

Handles attaching a SessionStore to a running terrarium (creatures,
root agent, channels) and rebuilding Conversation objects from saved
message dicts on resume.
"""

from typing import TYPE_CHECKING, Any

from kohakuterrarium.core.agent_native_tools import NATIVE_TOOL_OPTIONS_KEY
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


def attach_session_store(runtime: "TerrariumRuntime", store: Any) -> None:
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
                legacy_native_options = pad.get(NATIVE_TOOL_OPTIONS_KEY)
                if legacy_native_options:
                    agent.session.scratchpad.set(
                        NATIVE_TOOL_OPTIONS_KEY, legacy_native_options
                    )
                for k, v in pad.items():
                    if k.startswith("__") and k.endswith("__"):
                        continue
                    agent.session.scratchpad.set(k, v)
            native_tool_options = getattr(agent, "native_tool_options", None)
            if native_tool_options is not None:
                try:
                    native_tool_options.apply()
                except Exception as exc:
                    logger.warning(
                        "Failed to reapply native tool options",
                        agent=name,
                        error=str(exc),
                    )

        runtime._pending_resume_data = None

    # Set resume events on every agent that has them — root AND each
    # creature. Previously we only propagated root's events, so
    # creature output replay + hot-plugged triggers were silently
    # dropped on resume.
    if hasattr(runtime, "_pending_resume_events") and runtime._pending_resume_events:
        for name, events in runtime._pending_resume_events.items():
            if not events:
                continue
            if name == "root":
                target = runtime._root_agent
            else:
                target = runtime.get_creature_agent(name)
            if target is None:
                logger.debug(
                    "Resume events target missing, skipped",
                    agent=name,
                    count=len(events),
                )
                continue
            target._pending_resume_events = events
            logger.info(
                "Resume events set",
                agent=name,
                count=len(events),
            )
        runtime._pending_resume_events = None

    # Same for resumable triggers (hot-plugged channel listeners etc.)
    if (
        hasattr(runtime, "_pending_resume_triggers")
        and runtime._pending_resume_triggers
    ):
        for name, triggers in runtime._pending_resume_triggers.items():
            if not triggers:
                continue
            if name == "root":
                target = runtime._root_agent
            else:
                target = runtime.get_creature_agent(name)
            if target is None:
                logger.debug(
                    "Resume triggers target missing, skipped",
                    agent=name,
                    count=len(triggers),
                )
                continue
            target._pending_resume_triggers = triggers
            logger.info(
                "Resumable triggers set",
                agent=name,
                count=len(triggers),
            )
        runtime._pending_resume_triggers = None

    logger.info(
        "Session store attached to terrarium",
        creatures=list(runtime._creatures.keys()),
        channels=len(runtime.environment.shared_channels._channels),
    )
