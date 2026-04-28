"""Channel observer attach — engine-backed.

Replaces ``KohakuManager.terrarium_channel_stream`` and
``agent_channel_stream``.  Streams channel messages observed in a
session's environment as ``ChannelEvent`` objects.

Body adapted verbatim from
``serving/manager.py:_stream_from_registry`` (the legacy implementation
that already worked over either a shared or private ``ChannelRegistry``).
The only behaviour change is the resolution path: instead of
``manager._get_runtime(...).environment.shared_channels`` we use
``engine._environments[session_id].shared_channels``.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator

from kohakuterrarium.core.channel import AgentChannel
from kohakuterrarium.core.events import EventContent
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.observer import ChannelObserver


@dataclass
class ChannelEvent:
    """A channel message observed in a session."""

    terrarium_id: str
    channel: str
    sender: str
    content: EventContent
    message_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


async def stream_session_channels(
    engine: Terrarium,
    session_id: str,
    *,
    filter_channels: list[str] | None = None,
) -> AsyncIterator[ChannelEvent]:
    """Stream every shared-channel message from a session as it arrives."""
    env = engine._environments.get(session_id)
    if env is None:
        raise KeyError(f"session {session_id!r} not found")

    async for event in _stream_from_registry(
        env.shared_channels,
        source_id=session_id,
        source_type="session",
        filter_channels=filter_channels,
        running_check=lambda: session_id in engine._environments,
    ):
        yield event


async def stream_creature_channels(
    engine: Terrarium,
    creature_id: str,
    *,
    filter_channels: list[str] | None = None,
) -> AsyncIterator[ChannelEvent]:
    """Stream a creature's private (sub-agent) channel messages."""
    creature = engine.get_creature(creature_id)
    session = creature.agent.session
    async for event in _stream_from_registry(
        session.channels,
        source_id=creature_id,
        source_type="creature",
        filter_channels=filter_channels,
        running_check=lambda: creature.is_running,
    ):
        yield event


async def _stream_from_registry(
    registry: Any,
    *,
    source_id: str,
    source_type: str,
    filter_channels: list[str] | None = None,
    running_check: Any = None,
) -> AsyncIterator[ChannelEvent]:
    """Stream channel events from any ``ChannelRegistry``.

    Adapted from ``serving/manager.py:_stream_from_registry``.
    """
    observer = ChannelObserver(None)
    observer._session = None

    event_queue: asyncio.Queue[ChannelEvent] = asyncio.Queue()

    def on_message(msg: Any) -> None:
        event_queue.put_nowait(
            ChannelEvent(
                terrarium_id=source_id,
                channel=msg.channel,
                sender=msg.sender,
                content=msg.content,
                message_id=msg.message_id,
                timestamp=msg.timestamp,
            )
        )

    observer.on_message(on_message)

    all_channels = registry.list_channels()
    observe_channels = filter_channels or all_channels
    for ch_name in observe_channels:
        ch = registry.get(ch_name)
        if ch is not None and isinstance(ch, AgentChannel):
            sub = ch.subscribe(f"_stream_{source_id}_{ch_name}")
            observer._subscriptions[ch_name] = sub
            task = asyncio.create_task(observer._observe_loop(ch_name, sub))
            observer._observe_tasks.append(task)

    try:
        while running_check is None or running_check():
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                yield event
            except asyncio.TimeoutError:
                continue
    finally:
        await observer.stop()
