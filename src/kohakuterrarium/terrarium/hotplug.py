"""
Hot-plug mixin for TerrariumRuntime.

Provides methods to add/remove creatures and channels at runtime
without restarting the terrarium.
"""

import asyncio
import time

from kohakuterrarium.core.channel import BaseChannel
from kohakuterrarium.modules.trigger.channel import ChannelTrigger
from kohakuterrarium.terrarium.config import (
    ChannelConfig,
    CreatureConfig,
    build_channel_topology_prompt,
)
import kohakuterrarium.terrarium.wiring as _wiring
from kohakuterrarium.terrarium.creature import CreatureHandle
from kohakuterrarium.terrarium.factory import build_creature
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _emit_hotplug_transition(
    runtime: "HotPlugMixin",
    action: str,
    creature: str | None = None,
    channel: str | None = None,
) -> None:
    """Append a Wave B ``hotplug_transition`` event to the shared session store.

    Best-effort observability; swallows any store errors. Also nudges
    ``meta["agents"]`` so resume can pick up creatures added at runtime
    (test_session_resume_hotplugged_agent flip).
    """
    store = getattr(runtime, "_session_store", None) or getattr(
        runtime, "_pending_session_store", None
    )
    if store is None:
        return
    try:
        payload: dict = {"action": action, "ts": time.time()}
        if creature is not None:
            payload["creature"] = creature
        if channel is not None:
            payload["channel"] = channel
        store.append_event("terrarium", "hotplug_transition", payload)
    except Exception as e:  # pragma: no cover — observability
        logger.debug("hotplug_transition emit failed", error=str(e), exc_info=True)


class HotPlugMixin:
    """
    Mixin providing hot-plug methods for TerrariumRuntime.

    Allows adding/removing creatures and channels while the
    terrarium is running.  Mixed into TerrariumRuntime.
    """

    async def add_creature(self, creature_cfg: CreatureConfig) -> CreatureHandle:
        """Add and start a new creature to a running terrarium.

        Creates the agent, wires channels, injects topology, starts it,
        and launches its event loop task.

        Args:
            creature_cfg: Creature configuration

        Returns:
            The new CreatureHandle

        Raises:
            RuntimeError: If terrarium not running or creature name exists
        """
        if not self._running:
            raise RuntimeError("Terrarium not running")
        if creature_cfg.name in self._creatures:
            raise RuntimeError(f"Creature already exists: {creature_cfg.name}")

        # Build creature via factory function
        handle = build_creature(creature_cfg, self.environment, self.config)
        self._creatures[creature_cfg.name] = handle
        _wiring.install_runtime_output_wiring_resolver(
            self._creatures,
            root_agent=getattr(self, "_root_agent", None),
        )

        # Start the agent
        await handle.agent.start()

        # Launch event loop task
        task = asyncio.create_task(
            self._run_creature(handle),
            name=f"creature_{handle.name}",
        )
        self._creature_tasks.append(task)

        _emit_hotplug_transition(self, "add", creature=creature_cfg.name)
        # Update meta["agents"] so resume_terrarium covers hot-plugged
        # creatures (test_session_resume_hotplugged_agent).
        store = getattr(self, "_session_store", None) or getattr(
            self, "_pending_session_store", None
        )
        if store is not None:
            try:
                meta = store.load_meta()
                agents = list(meta.get("agents", []) or [])
                if creature_cfg.name not in agents:
                    agents.append(creature_cfg.name)
                    store.meta["agents"] = agents
            except Exception as e:  # pragma: no cover — observability
                logger.debug(
                    "Failed to update meta['agents'] on hot-plug",
                    error=str(e),
                    exc_info=True,
                )
        logger.info("Creature hot-added", creature=creature_cfg.name)
        return handle

    async def remove_creature(self, name: str) -> bool:
        """Stop and remove a creature from a running terrarium.

        Args:
            name: Creature name to remove

        Returns:
            True if removed, False if not found
        """
        handle = self._creatures.get(name)
        if handle is None:
            return False

        # Stop the agent (sets _running=False, stops triggers)
        handle.agent._running = False

        # Find and cancel the creature's task
        task_name = f"creature_{name}"
        for i, task in enumerate(self._creature_tasks):
            if task.get_name() == task_name:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                self._creature_tasks.pop(i)
                break

        # Stop the agent properly
        try:
            await handle.agent.stop()
        except Exception as e:
            logger.warning("Error stopping creature", creature=name, error=str(e))

        # Remove from registry
        del self._creatures[name]
        _wiring.install_runtime_output_wiring_resolver(
            self._creatures,
            root_agent=getattr(self, "_root_agent", None),
        )
        _emit_hotplug_transition(self, "remove", creature=name)
        logger.info("Creature removed", creature=name)
        return True

    async def add_channel(
        self, name: str, channel_type: str = "queue", description: str = ""
    ) -> BaseChannel:
        """Add a new channel to a running terrarium.

        Args:
            name: Channel name
            channel_type: "queue" or "broadcast"
            description: Human-readable description

        Returns:
            The created channel

        Raises:
            RuntimeError: If terrarium not started
        """
        if not self._running:
            raise RuntimeError("Terrarium not started")

        channel = self.environment.shared_channels.get_or_create(
            name, channel_type=channel_type, description=description
        )

        # Also add to config for topology prompt updates
        self.config.channels.append(
            ChannelConfig(name=name, channel_type=channel_type, description=description)
        )

        _emit_hotplug_transition(self, "add", channel=name)
        logger.info("Channel hot-added", channel=name, channel_type=channel_type)
        return channel

    async def wire_channel(
        self, creature_name: str, channel_name: str, direction: str
    ) -> None:
        """Connect a creature to a channel at runtime.

        For "listen": creates and starts a ChannelTrigger on the creature.
        For "send": updates the creature's config and topology prompt.

        Args:
            creature_name: Name of the creature
            channel_name: Name of the channel
            direction: "listen" or "send"

        Raises:
            ValueError: If creature not found or invalid direction
        """
        handle = self._creatures.get(creature_name)
        if handle is None:
            raise ValueError(f"Creature not found: {creature_name}")

        if direction == "listen":
            # Determine if broadcast for prompt framing
            broadcast_names = {
                ch.name for ch in self.config.channels if ch.channel_type == "broadcast"
            }
            prompt = None
            if channel_name in broadcast_names:
                prompt = (
                    "[Broadcast on '{channel}' from '{sender}']: {content}\n\n"
                    "This message was broadcast to all team members "
                    "on '{channel}'. "
                    "Only act on it if it is relevant to your current task."
                )

            # Use the agent's own ``config.name`` so the self-filter
            # byte-matches what ``send_message`` stamps via
            # ``ToolContext.agent_name``.  See ``terrarium/factory.py``
            # ``build_root_agent`` for context.
            identity = handle.agent.config.name
            trigger = ChannelTrigger(
                channel_name=channel_name,
                subscriber_id=identity,
                prompt=prompt,
                ignore_sender=identity,
                registry=self.environment.shared_channels,
            )

            # Use the Agent.add_trigger() method
            await handle.agent.add_trigger(trigger)

            # Update handle
            handle.listen_channels.append(channel_name)

            logger.info(
                "Channel wired",
                creature=creature_name,
                channel=channel_name,
                direction="listen",
            )

        elif direction == "send":
            # Just update metadata - sending doesn't need triggers
            handle.send_channels.append(channel_name)

            # Update topology in system prompt
            topology = build_channel_topology_prompt(self.config, handle.config)
            handle.agent.update_system_prompt(topology)

            logger.info(
                "Channel wired",
                creature=creature_name,
                channel=channel_name,
                direction="send",
            )
        else:
            raise ValueError(f"Invalid direction: {direction}. Use 'listen' or 'send'")
