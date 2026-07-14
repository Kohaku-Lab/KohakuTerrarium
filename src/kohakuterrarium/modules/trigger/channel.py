"""Channel trigger - fires when a message arrives on a named channel."""

import asyncio
from collections import defaultdict
from typing import Any

from kohakuterrarium.core.channel import (
    AgentChannel,
    ChannelRegistry,
    ChannelSubscription,
)
from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.core.session import get_channel_registry
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class ChannelTrigger(BaseTrigger):
    """
    Trigger that fires when a message arrives on a named channel.

    Supports both queue (SubAgentChannel) and broadcast (AgentChannel) channels.
    For broadcast channels, a subscriber_id is used to create a subscription.

    Usage:
        trigger = ChannelTrigger(
            channel_name="inbox",
            prompt="Handle incoming message: {content}",
        )
        await trigger.start()
        event = await trigger.wait_for_trigger()
    """

    resumable = True
    universal = True

    setup_tool_name = "watch_channel"
    setup_description = (
        "Listen on a named channel and wake the agent when a message arrives."
    )
    setup_param_schema = {
        "type": "object",
        "properties": {
            "channel_name": {
                "type": "string",
                "description": "Channel name to listen on (creature or shared).",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Prompt injected when a message arrives. Supports "
                    "{content}, {channel}, {sender}, {message_id}, and "
                    "message metadata placeholders."
                ),
            },
            "filter_sender": {
                "type": "string",
                "description": "Only fire when sender matches (whitelist).",
            },
        },
        "required": ["channel_name"],
    }
    setup_full_doc = (
        "Installs a ChannelTrigger. The agent wakes up whenever a message is "
        "delivered to `channel_name`. Use `{content}`, `{channel}`, `{sender}`, "
        "`{message_id}`, or message metadata placeholders inside `prompt` to "
        "inline incoming message details. `filter_sender` limits the trigger "
        "to a specific sender. The creature's own name is auto-registered as "
        "the `ignore_sender` to prevent self-triggering."
    )

    @classmethod
    def post_setup(cls, trigger: "ChannelTrigger", context: Any) -> None:
        """Wire registry + self-ignore from the invoking agent's context."""
        agent = getattr(context, "agent", None) if context else None
        if getattr(trigger, "_registry", None) is None and agent is not None:
            env = getattr(agent, "environment", None)
            if env is not None and getattr(env, "shared_channels", None) is not None:
                trigger._registry = env.shared_channels
            else:
                session = getattr(agent, "session", None)
                if (
                    session is not None
                    and getattr(session, "channels", None) is not None
                ):
                    trigger._registry = session.channels
        if not trigger.ignore_sender:
            agent_name = getattr(context, "agent_name", None)
            if agent_name:
                trigger.ignore_sender = agent_name
        if not trigger.ignore_sender_id and agent is not None:
            creature_id = getattr(agent, "_creature_id", None) or getattr(
                agent, "creature_id", None
            )
            if creature_id:
                trigger.ignore_sender_id = creature_id

    def __init__(
        self,
        channel_name: str,
        subscriber_id: str | None = None,
        prompt: str | None = None,
        filter_sender: str | None = None,
        ignore_sender: str | None = None,
        ignore_sender_id: str | None = None,
        registry: ChannelRegistry | None = None,
        session: Any | None = None,
        **options: Any,
    ):
        """
        Initialize channel trigger.

        Args:
            channel_name: Name of the channel to listen on
            subscriber_id: Subscriber ID for broadcast channels (auto-generated if None)
            prompt: Prompt template to include in event. Supports
                ``{content}``, ``{channel}``, ``{sender}``, ``{message_id}``,
                and message metadata placeholders.
            filter_sender: Only fire for messages from this sender (whitelist)
            ignore_sender: Skip messages whose ``sender`` (display name) matches.
                Kept for backward compat — when two creatures share a config
                name, prefer ``ignore_sender_id`` instead.
            ignore_sender_id: Skip messages whose ``sender_id`` (stable creature
                identity) matches. Robust against display-name collisions.
            registry: Optional channel registry (defaults to global singleton)
            session: Optional session whose channel registry to use
            **options: Additional options
        """
        super().__init__(prompt=prompt, **options)
        self.channel_name = channel_name
        self.subscriber_id = subscriber_id
        self.filter_sender = filter_sender
        self.ignore_sender = ignore_sender
        self.ignore_sender_id = ignore_sender_id
        self._registry = registry
        self._session = session
        self._subscription: ChannelSubscription | None = None

    async def _on_start(self) -> None:
        """Resolve registry on start."""
        if self._registry is None:
            if self._session is not None:
                self._registry = self._session.channels
            else:
                self._registry = get_channel_registry()
        logger.debug("Channel trigger started", channel=self.channel_name)

    def to_resume_dict(self) -> dict[str, Any]:
        """Serialize for session persistence."""
        return {
            "channel_name": self.channel_name,
            "subscriber_id": self.subscriber_id,
            "prompt": self.prompt,
            "filter_sender": self.filter_sender,
            "ignore_sender": self.ignore_sender,
            "ignore_sender_id": self.ignore_sender_id,
        }

    @classmethod
    def from_resume_dict(cls, data: dict[str, Any]) -> "ChannelTrigger":
        """Re-create from saved config. Registry/session set later by caller."""
        return cls(
            channel_name=data["channel_name"],
            subscriber_id=data.get("subscriber_id"),
            prompt=data.get("prompt"),
            filter_sender=data.get("filter_sender"),
            ignore_sender=data.get("ignore_sender"),
            ignore_sender_id=data.get("ignore_sender_id"),
        )

    async def _on_stop(self) -> None:
        """Clean up subscription and log stop."""
        if self._subscription is not None:
            self._subscription.unsubscribe()
            self._subscription = None
        logger.debug("Channel trigger stopped", channel=self.channel_name)

    async def wait_for_trigger(self) -> TriggerEvent | None:
        """Wait for a message on the channel."""
        if not self._running:
            return None

        channel = self._registry.get_or_create(self.channel_name)

        while self._running:
            try:
                # Use a timeout so we periodically check if still running
                if isinstance(channel, AgentChannel):
                    if self._subscription is None:
                        sub_id = self.subscriber_id or f"trigger_{self.channel_name}"
                        self._subscription = channel.subscribe(sub_id)
                    msg = await self._subscription.receive(timeout=1.0)
                else:
                    msg = await channel.receive(timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if self._should_skip(msg):
                continue
            return self._message_to_event(msg)

        return None

    def drain_ready(self) -> list[TriggerEvent]:
        """Non-blocking drain of every message already queued for this
        trigger's subscription — the events left behind after
        :meth:`wait_for_trigger` returned the first one.

        The trigger manager admits these into the receiver's mid-turn
        buffer so a channel backlog costs ONE turn instead of one round
        per message (UXI-08). Self / filtered senders are dropped the
        same way :meth:`wait_for_trigger` drops them; each surviving
        message keeps its own sender / metadata in the built event.
        """
        if not self._running or self._registry is None:
            return []
        channel = self._registry.get_or_create(self.channel_name)
        if isinstance(channel, AgentChannel):
            source: Any = self._subscription
        else:
            source = channel
        if source is None:
            return []
        events: list[TriggerEvent] = []
        while True:
            msg = source.try_receive()
            if msg is None:
                break
            if self._should_skip(msg):
                continue
            events.append(self._message_to_event(msg))
        return events

    def _should_skip(self, msg: Any) -> bool:
        """Whether a delivered message is filtered out (sender whitelist
        or self-ignore). Prefer the stable ``sender_id`` check —
        ``ignore_sender`` (display name) collides when two creatures
        share a config name."""
        if self.filter_sender and msg.sender != self.filter_sender:
            return True
        if (
            self.ignore_sender_id
            and getattr(msg, "sender_id", None) == self.ignore_sender_id
        ):
            return True
        if self.ignore_sender and msg.sender == self.ignore_sender:
            return True
        return False

    def _message_to_event(self, msg: Any) -> TriggerEvent:
        """Build the CHANNEL_MESSAGE event for a delivered message.

        Terrarium channel injection uses the default template
        ``[Channel '{channel}' from {sender}]: {content}``, so all
        placeholders are rendered before the event reaches the
        controller. ``BaseTrigger._create_event`` defaults
        ``prompt_override`` to ``self.prompt`` (the raw template); the
        controller prefers ``prompt_override`` over ``content`` when
        assembling the LLM input, so the rendered string is pinned onto
        the override too — otherwise the receiver sees unfilled
        ``{placeholders}``.
        """
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        event_prompt = self._render_prompt(msg, content)
        event = self._create_event(
            EventType.CHANNEL_MESSAGE,
            content=event_prompt or content,
            context={
                "sender": msg.sender,
                "channel": self.channel_name,
                "message_id": msg.message_id,
                "raw_content": msg.content,
                **msg.metadata,
            },
        )
        if event_prompt is not None:
            event.prompt_override = event_prompt
        return event

    def _render_prompt(self, msg: Any, content: str) -> str | None:
        """Render the prompt template against channel message metadata."""
        if not self.prompt:
            return None
        values = defaultdict(str, msg.metadata)
        values.update(
            {
                "content": content,
                "sender": msg.sender,
                "channel": self.channel_name,
                "message_id": msg.message_id,
                "reply_to": msg.reply_to or "",
                "sender_id": getattr(msg, "sender_id", None) or "",
            }
        )
        return self.prompt.format_map(values)
