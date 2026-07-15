"""Provide mention, inactivity, and activity-coordination Discord triggers."""

import asyncio
from typing import Any

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.modules.trigger import BaseTrigger
from kohakuterrarium.utils.logging import get_logger

from discord_client import get_client

logger = get_logger("kohakuterrarium.custom.discord_trigger")


class DiscordPingTrigger(BaseTrigger):
    """Turn direct Discord mentions into non-stackable reply events."""

    def __init__(
        self,
        client: Any = None,
        client_name: str = "default",
        prompt: str | None = None,
        **options: Any,
    ):
        """Initialize mention detection and its pending-event queue.

        Args:
            client: Discord client to monitor (optional, will look up from registry)
            client_name: Name to look up in shared client registry
            prompt: Prompt to use when ping is detected
            **options: Additional options
        """
        super().__init__(prompt=prompt, **options)
        self.client = client
        self.client_name = client_name
        self._pending_pings: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def set_client(self, client: Any) -> None:
        """Attach a Discord client after trigger construction."""
        self.client = client

    def _ensure_client(self) -> Any:
        """Return the attached client or resolve it from the registry."""
        if self.client is None:
            self.client = get_client(self.client_name)
        return self.client

    def _on_context_update(self, context: dict[str, Any]) -> None:
        """Queue Discord contexts that directly mention the bot."""
        if context.get("source") != "discord":
            return

        # force_reply marks the synthetic event and prevents a feedback loop.
        if context.get("force_reply"):
            return

        is_mention = context.get("is_mention", False)
        if is_mention:
            try:
                self._pending_pings.put_nowait(context)
            except asyncio.QueueFull:
                pass  # Preserve non-blocking delivery if the queue is later bounded.

    async def wait_for_trigger(self) -> TriggerEvent | None:
        """Return the next queued mention as an immediate reply event."""
        if not self._running:
            return None

        try:
            ping_context = await asyncio.wait_for(
                self._pending_pings.get(),
                timeout=1.0,
            )

            return TriggerEvent(
                type="ping",
                content=self.prompt or "You were mentioned. Reply to this message.",
                context={
                    **ping_context,
                    "force_reply": True,
                },
                prompt_override=self.prompt,
                stackable=False,  # Direct mentions require prompt attention.
            )
        except asyncio.TimeoutError:
            return None


class DiscordIdleTrigger(BaseTrigger):
    """Probabilistically suggest exploration after a randomized idle period."""

    def __init__(
        self,
        min_idle_seconds: float = 1800.0,  # 30 minutes.
        max_idle_seconds: float = 7200.0,  # 2 hours.
        exploration_chance: float = 0.3,  # Avoid forcing a topic at every timeout.
        prompt: str | None = None,
        **options: Any,
    ):
        """Initialize randomized idle thresholds and exploration probability.

        Args:
            min_idle_seconds: Minimum idle time before trigger can fire
            max_idle_seconds: Maximum idle time (random between min and max)
            exploration_chance: Probability of actually exploring (0.0 - 1.0)
            prompt: Prompt for exploration behavior
            **options: Additional options
        """
        super().__init__(prompt=prompt, **options)
        self.min_idle_seconds = min_idle_seconds
        self.max_idle_seconds = max_idle_seconds
        self.exploration_chance = exploration_chance
        self._last_activity = asyncio.get_event_loop().time()
        self._current_threshold: float | None = None
        self._check_count = 0  # Throttle status logs independently of polling.

    def _on_context_update(self, context: dict[str, Any]) -> None:
        """Reset activity time and choose a fresh idle threshold."""
        import random

        self._last_activity = asyncio.get_event_loop().time()
        self._current_threshold = random.uniform(
            self.min_idle_seconds,
            self.max_idle_seconds,
        )
        logger.debug(
            "Idle timer reset (activity detected)",
            extra={"new_threshold": int(self._current_threshold)},
        )

    async def _on_start(self) -> None:
        """Initialize the first randomized idle threshold."""
        import random

        self._last_activity = asyncio.get_event_loop().time()
        self._current_threshold = random.uniform(
            self.min_idle_seconds,
            self.max_idle_seconds,
        )
        logger.info(
            "Idle trigger started",
            extra={
                "min_idle": int(self.min_idle_seconds),
                "max_idle": int(self.max_idle_seconds),
                "exploration_chance": f"{self.exploration_chance:.0%}",
                "initial_threshold": int(self._current_threshold),
            },
        )

    async def wait_for_trigger(self) -> TriggerEvent | None:
        """Poll idle duration and occasionally emit an exploration event."""
        import random

        if not self._running:
            return None

        # Coarse polling avoids a continuously active trigger task.
        await asyncio.sleep(30.0)

        if not self._running:
            return None

        current_time = asyncio.get_event_loop().time()
        idle_duration = current_time - self._last_activity

        # Report status about every five minutes without logging every poll.
        self._check_count += 1
        if self._check_count >= 10:
            self._check_count = 0
            logger.debug(
                "Idle status",
                extra={
                    "idle_minutes": int(idle_duration / 60),
                    "threshold_minutes": (
                        int(self._current_threshold / 60)
                        if self._current_threshold
                        else None
                    ),
                },
            )

        if self._current_threshold and idle_duration >= self._current_threshold:
            roll = random.random()
            logger.info(
                "Idle threshold reached, rolling for exploration",
                extra={
                    "idle_seconds": int(idle_duration),
                    "threshold": int(self._current_threshold),
                    "roll": f"{roll:.2f}",
                    "chance": f"{self.exploration_chance:.2f}",
                    "will_trigger": roll < self.exploration_chance,
                },
            )

            if roll < self.exploration_chance:
                self._last_activity = current_time
                self._current_threshold = random.uniform(
                    self.min_idle_seconds,
                    self.max_idle_seconds,
                )

                logger.info("Idle trigger fired - starting exploration")

                return TriggerEvent(
                    type="idle",
                    content=self.prompt
                    or "Chat has been quiet. Consider starting a new topic.",
                    context={
                        "idle_duration": idle_duration,
                        "exploration": True,
                        "force_reply": False,  # Exploration remains advisory.
                    },
                    prompt_override=self.prompt,
                    stackable=True,
                )
            else:
                # A skipped roll starts a new idle window instead of retrying immediately.
                new_threshold = random.uniform(
                    self.min_idle_seconds,
                    self.max_idle_seconds,
                )
                logger.debug(
                    "Exploration skipped, new threshold set",
                    extra={"new_threshold": int(new_threshold)},
                )
                self._current_threshold = new_threshold

        return None


class DiscordActivityMonitor(BaseTrigger):
    """Fan out Discord activity context to coordinating triggers."""

    def __init__(
        self,
        client: Any = None,
        client_name: str = "default",
        prompt: str | None = None,
        **options: Any,
    ):
        """Initialize client lookup and activity callback storage.

        Args:
            client: Discord client to monitor (optional, will look up from registry)
            client_name: Name to look up in shared client registry
            prompt: Default prompt (unused)
            **options: Additional options
        """
        super().__init__(prompt=prompt, **options)
        self.client = client
        self.client_name = client_name
        self._activity_callbacks: list[callable] = []

    def set_client(self, client: Any) -> None:
        """Attach a Discord client after monitor construction."""
        self.client = client

    def _ensure_client(self) -> Any:
        """Return the attached client or resolve it from the registry."""
        if self.client is None:
            self.client = get_client(self.client_name)
        return self.client

    def add_activity_callback(self, callback: callable) -> None:
        """Register a callback to receive each activity context."""
        self._activity_callbacks.append(callback)

    def _on_context_update(self, context: dict[str, Any]) -> None:
        """Propagate activity without allowing one callback to break others."""
        for callback in self._activity_callbacks:
            try:
                callback(context)
            except Exception:
                pass  # Callback isolation preserves delivery to remaining listeners.

    async def wait_for_trigger(self) -> TriggerEvent | None:
        """Remain alive while producing no trigger events directly."""
        await asyncio.sleep(1.0)
        return None
