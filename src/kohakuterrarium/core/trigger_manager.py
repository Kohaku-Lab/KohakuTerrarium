"""
TriggerManager - centralized trigger lifecycle management.

Owns all trigger state (instances, tasks) and provides the event loop
for each trigger. Tools can add/remove triggers at runtime via the
agent's trigger_manager.
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Coroutine
from uuid import uuid4

from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Wave B: drift threshold (seconds). Trigger firings later than this
# get a ``schedule_drift`` event so session readers can see scheduler
# backlog. Kept small so real-time triggers flag promptly.
SCHEDULE_DRIFT_THRESHOLD_S = 1.0


@dataclass
class TriggerInfo:
    """Info about an active trigger."""

    trigger_id: str
    trigger_type: str
    running: bool
    created_at: datetime


class TriggerManager:
    """
    Manages trigger lifecycle for an agent.

    Provides add/remove/list API and runs the event loop for each
    trigger. When a trigger fires, it calls _process_event which
    is bound to the owning agent's _process_event method.
    """

    def __init__(
        self,
        process_event: Callable[..., Coroutine[Any, Any, None]],
    ) -> None:
        self._triggers: dict[str, BaseTrigger] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._created_at: dict[str, datetime] = {}
        self._process_event = process_event
        # Optional callback: (trigger_id, event) -> None
        self.on_trigger_fired: Callable[[str, Any], None] | None = None
        # Session store ref for saving resumable triggers (set by agent)
        self._session_store: Any = None
        self._agent_name: str = ""

    async def add(
        self,
        trigger: BaseTrigger,
        trigger_id: str | None = None,
        autostart: bool = True,
    ) -> str:
        """Add a trigger. Starts it immediately if autostart=True.

        Args:
            trigger: The trigger instance
            trigger_id: Optional ID (auto-generated if not provided)
            autostart: If True, start the trigger and its event loop
                       Set False for triggers added before agent.start()

        Returns:
            The trigger_id
        """
        if trigger_id is None:
            trigger_id = f"trigger_{uuid4().hex[:8]}"

        if trigger_id in self._triggers:
            raise ValueError(f"Trigger already exists: {trigger_id}")

        self._triggers[trigger_id] = trigger
        self._created_at[trigger_id] = datetime.now()

        if autostart:
            await trigger.start()
            task = asyncio.create_task(
                self._run_loop(trigger_id, trigger),
                name=f"trigger_{trigger_id}",
            )
            self._tasks[trigger_id] = task
            logger.info(
                "Trigger added and started",
                trigger_id=trigger_id,
                trigger_type=type(trigger).__name__,
            )

        # Persist resumable triggers to session store
        if getattr(trigger, "resumable", False) and self._session_store:
            try:
                self._session_store.save_state(
                    self._agent_name,
                    triggers=[
                        {
                            "trigger_id": tid,
                            "type": type(t).__name__,
                            "module": type(t).__module__,
                            "data": t.to_resume_dict(),
                        }
                        for tid, t in self._triggers.items()
                        if getattr(t, "resumable", False)
                    ],
                )
            except Exception as e:
                logger.debug("Failed to save trigger state", error=str(e))
        else:
            logger.debug(
                "Trigger registered (not started)",
                trigger_id=trigger_id,
                trigger_type=type(trigger).__name__,
            )

        return trigger_id

    async def remove(self, trigger_id: str) -> bool:
        """Stop and remove a trigger.

        Returns:
            True if removed, False if not found
        """
        trigger = self._triggers.pop(trigger_id, None)
        if trigger is None:
            return False

        task = self._tasks.pop(trigger_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("Trigger task cleanup error", error=str(e), exc_info=True)

        await trigger.stop()
        self._created_at.pop(trigger_id, None)
        logger.info("Trigger removed", trigger_id=trigger_id)
        return True

    def get(self, trigger_id: str) -> TriggerInfo | None:
        """Get info about a trigger."""
        trigger = self._triggers.get(trigger_id)
        if trigger is None:
            return None
        return TriggerInfo(
            trigger_id=trigger_id,
            trigger_type=type(trigger).__name__,
            running=trigger.is_running,
            created_at=self._created_at.get(trigger_id, datetime.now()),
        )

    def list(self) -> list[TriggerInfo]:
        """List all active triggers."""
        return [
            TriggerInfo(
                trigger_id=tid,
                trigger_type=type(trigger).__name__,
                running=trigger.is_running,
                created_at=self._created_at.get(tid, datetime.now()),
            )
            for tid, trigger in self._triggers.items()
        ]

    def get_trigger(self, trigger_id: str) -> BaseTrigger | None:
        """Get the raw trigger instance."""
        return self._triggers.get(trigger_id)

    async def start_all(self) -> None:
        """Start all registered triggers. Called by agent.start()."""
        for trigger_id, trigger in self._triggers.items():
            if trigger_id not in self._tasks:
                await trigger.start()
                task = asyncio.create_task(
                    self._run_loop(trigger_id, trigger),
                    name=f"trigger_{trigger_id}",
                )
                self._tasks[trigger_id] = task

        count = len(self._triggers)
        if count:
            logger.info("Triggers started", count=count)

    async def stop_all(self) -> None:
        """Stop all triggers. Called by agent.stop()."""
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        for trigger in self._triggers.values():
            await trigger.stop()
        self._tasks.clear()
        self._triggers.clear()
        self._created_at.clear()
        logger.debug("All triggers stopped")

    def set_context_all(self, context: dict[str, Any]) -> None:
        """Update context on all triggers."""
        for trigger in self._triggers.values():
            try:
                trigger.set_context(context)
            except Exception as e:
                logger.warning(
                    "Trigger context update failed",
                    trigger=type(trigger).__name__,
                    error=str(e),
                )

    async def _run_loop(self, trigger_id: str, trigger: BaseTrigger) -> None:
        """Run a single trigger's event loop."""
        while trigger.is_running:
            try:
                event = await trigger.wait_for_trigger()
                if event:
                    logger.info(
                        "Trigger fired",
                        trigger_id=trigger_id,
                        event_type=event.type,
                    )
                    # Wave B ``schedule_drift`` — compares the event's
                    # scheduled timestamp (if set) against now. Only
                    # emits when drift crosses the threshold.
                    self._maybe_emit_schedule_drift(trigger_id, trigger, event)
                    if self.on_trigger_fired:
                        try:
                            self.on_trigger_fired(trigger_id, event)
                        except Exception as e:
                            logger.debug(
                                "on_trigger_fired callback error",
                                error=str(e),
                                exc_info=True,
                            )
                    await self._process_event(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "Trigger error",
                    trigger_id=trigger_id,
                    error=str(e),
                )
                await asyncio.sleep(1.0)

    def _maybe_emit_schedule_drift(
        self, trigger_id: str, trigger: BaseTrigger, event: Any
    ) -> None:
        """Record Wave B ``schedule_drift`` when the trigger fired late.

        The event's ``scheduled_at`` timestamp (if set by the trigger)
        is compared to ``time.time()``. If the delta exceeds
        :data:`SCHEDULE_DRIFT_THRESHOLD_S`, a store event is written.
        Pure observability — missing store / missing scheduled_at is
        fine.
        """
        scheduled = getattr(event, "scheduled_at", None)
        if scheduled is None:
            scheduled = getattr(event, "context", None)
            if isinstance(scheduled, dict):
                scheduled = scheduled.get("scheduled_at")
        if not isinstance(scheduled, (int, float)):
            return
        drift_s = time.time() - float(scheduled)
        if drift_s < SCHEDULE_DRIFT_THRESHOLD_S:
            return
        store = self._session_store
        if store is None:
            return
        try:
            store.append_event(
                self._agent_name or "agent",
                "schedule_drift",
                {
                    "trigger_id": trigger_id,
                    "trigger_name": type(trigger).__name__,
                    "drift_ms": drift_s * 1000.0,
                },
            )
        except Exception as e:  # pragma: no cover — observability
            logger.debug("schedule_drift emit failed", error=str(e), exc_info=True)
