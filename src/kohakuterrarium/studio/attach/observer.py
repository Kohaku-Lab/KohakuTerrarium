"""Expose host-local shared and private channel messages as async events."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator

from kohakuterrarium.core.channel import AgentChannel
from kohakuterrarium.core.events import EventContent
from kohakuterrarium.terrarium.observer import ChannelObserver
from kohakuterrarium.terrarium import TerrariumService
from kohakuterrarium.studio._runtime import host_engine_or_none


@dataclass
class ChannelEvent:
    """A channel message annotated with its observed source."""

    terrarium_id: str
    channel: str
    sender: str
    content: EventContent
    message_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


async def stream_session_channels(
    service: "TerrariumService",
    session_id: str,
    *,
    filter_channels: list[str] | None = None,
) -> AsyncIterator[ChannelEvent]:
    """Stream shared-channel messages from a host-local graph session.

    Observation reads the engine's channel registry directly. Lab hosts therefore
    raise ``KeyError`` for worker sessions until a cross-node observer transport is
    available, allowing websocket callers to use the standard not-local close path.
    """
    engine = host_engine_or_none(service)
    if engine is None:
        raise KeyError(
            f"session {session_id!r} is not host-local — channel observer "
            "streaming of worker sessions is not yet wired"
        )
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
    service: "TerrariumService",
    creature_id: str,
    *,
    filter_channels: list[str] | None = None,
) -> AsyncIterator[ChannelEvent]:
    """Stream private sub-agent channel messages for a host-local creature.

    Worker creatures raise ``KeyError`` because private registries are not available
    on a lab host.
    """
    engine = host_engine_or_none(service)
    if engine is None:
        raise KeyError(
            f"creature {creature_id!r} is not host-local — channel observer "
            "streaming of worker creatures is not yet wired"
        )
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
    """Observe an arbitrary channel registry until its source stops running."""
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

    def _subscribe_new_channels() -> None:
        """Subscribe to eligible channels created since the previous poll.

        Registries are dynamic, so an attachment-time snapshot would miss later
        channel creation and graph merges. Periodic polling keeps subscriptions
        current without requiring registry mutation hooks.
        """
        for ch_name in filter_channels or registry.list_channels():
            if ch_name in observer._subscriptions:
                continue
            ch = registry.get(ch_name)
            if ch is None or not isinstance(ch, AgentChannel):
                continue
            sub = ch.subscribe(f"_stream_{source_id}_{ch_name}")
            observer._subscriptions[ch_name] = sub
            task = asyncio.create_task(observer._observe_loop(ch_name, sub))
            observer._observe_tasks.append(task)

    _subscribe_new_channels()

    try:
        while running_check is None or running_check():
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                yield event
            except asyncio.TimeoutError:
                _subscribe_new_channels()
                continue
            else:
                _subscribe_new_channels()
    finally:
        await observer.stop()
