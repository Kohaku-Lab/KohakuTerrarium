"""
Programmatic API for terrarium management.

Wraps TerrariumRuntime with convenient methods for channel
operations, creature lifecycle, and terrarium status.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from kohakuterrarium.core.channel import AgentChannel, ChannelMessage
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.observer import ChannelObserver
    from kohakuterrarium.terrarium.runtime import TerrariumRuntime

logger = get_logger(__name__)


class TerrariumAPI:
    """
    Programmatic interface for terrarium management.

    Wraps TerrariumRuntime with convenient methods for:
    - Channel operations (list, read, send, observe)
    - Creature operations (list, start, stop, status)
    - Terrarium lifecycle (start, stop, status)
    """

    def __init__(self, runtime: "TerrariumRuntime") -> None:
        self._runtime = runtime

    # ------------------------------------------------------------------
    # Channel operations
    # ------------------------------------------------------------------

    async def list_channels(self) -> list[dict[str, str]]:
        """List all channels with name, type, description."""
        session = self._runtime._session
        if session is None:
            return []
        return session.channels.get_channel_info()

    async def channel_info(self, name: str) -> dict[str, Any] | None:
        """Get info for a single channel (type, qsize, subscribers)."""
        session = self._runtime._session
        if session is None:
            return None

        channel = session.channels.get(name)
        if channel is None:
            return None

        info: dict[str, Any] = {
            "name": channel.name,
            "type": channel.channel_type,
            "description": channel.description,
            "qsize": channel.qsize,
        }
        if isinstance(channel, AgentChannel):
            info["subscriber_count"] = channel.subscriber_count
        return info

    async def send_to_channel(
        self,
        name: str,
        content: str,
        sender: str = "human",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Inject a message into a channel.

        Creates a ChannelMessage and sends it. Works for both queue
        and broadcast channels.

        Returns:
            The generated message_id.

        Raises:
            ValueError: If the terrarium is not running or channel not found.
        """
        session = self._runtime._session
        if session is None:
            raise ValueError("Terrarium is not running")

        channel = session.channels.get(name)
        if channel is None:
            available = session.channels.list_channels()
            raise ValueError(f"Channel '{name}' not found. Available: {available}")

        msg = ChannelMessage(
            sender=sender,
            content=content,
            metadata=metadata or {},
        )
        await channel.send(msg)

        # Record in observer if one exists
        if hasattr(self._runtime, "_observer"):
            observer: "ChannelObserver" = self._runtime._observer
            observer.record(name, msg)

        logger.debug(
            "API: message sent",
            channel=name,
            sender=sender,
            message_id=msg.message_id,
        )
        return msg.message_id

    # ------------------------------------------------------------------
    # Creature operations
    # ------------------------------------------------------------------

    async def list_creatures(self) -> list[dict[str, Any]]:
        """List all creatures with name, running status, and channels."""
        result: list[dict[str, Any]] = []
        for name, handle in self._runtime._creatures.items():
            result.append(
                {
                    "name": name,
                    "running": handle.is_running,
                    "listen_channels": handle.listen_channels,
                    "send_channels": handle.send_channels,
                }
            )
        return result

    async def get_creature_status(self, name: str) -> dict[str, Any] | None:
        """Get detailed status for one creature.

        Returns None if the creature does not exist.
        """
        handle = self._runtime._creatures.get(name)
        if handle is None:
            return None
        return {
            "name": handle.name,
            "running": handle.is_running,
            "listen_channels": handle.listen_channels,
            "send_channels": handle.send_channels,
        }

    async def stop_creature(self, name: str) -> bool:
        """Stop a specific creature.

        Cancels the creature's task and stops its agent.

        Returns:
            True if the creature was stopped, False if not found.
        """
        handle = self._runtime._creatures.get(name)
        if handle is None:
            return False

        # Cancel the matching creature task
        for task in self._runtime._creature_tasks:
            if task.get_name() == f"creature_{name}" and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # Stop the agent
        try:
            await handle.agent.stop()
        except Exception as exc:
            logger.error("Error stopping creature", creature=name, error=str(exc))

        logger.info("API: creature stopped", creature=name)
        return True

    async def start_creature(self, name: str) -> bool:
        """Restart a stopped creature.

        Re-starts the agent and creates a new run task.

        Returns:
            True if the creature was started, False if not found.
        """
        handle = self._runtime._creatures.get(name)
        if handle is None:
            return False

        if handle.is_running:
            logger.warning("Creature already running", creature=name)
            return True

        await handle.agent.start()
        task = asyncio.create_task(
            self._runtime._run_creature(handle),
            name=f"creature_{name}",
        )
        self._runtime._creature_tasks.append(task)

        logger.info("API: creature started", creature=name)
        return True

    # ------------------------------------------------------------------
    # Terrarium operations
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Get full terrarium status."""
        return self._runtime.get_status()

    @property
    def is_running(self) -> bool:
        """Whether the terrarium runtime is currently running."""
        return self._runtime._running
