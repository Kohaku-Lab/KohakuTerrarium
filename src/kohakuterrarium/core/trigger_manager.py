"""Lifecycle, scheduling, and event delivery for an agent's triggers."""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Coroutine
from uuid import uuid4

from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Firings later than this threshold record scheduler backlog in the session.
SCHEDULE_DRIFT_THRESHOLD_S = 1.0


@dataclass
class TriggerInfo:
    """Describe an active trigger and its lifecycle state."""

    trigger_id: str
    trigger_type: str
    running: bool
    created_at: datetime


class TriggerManager:
    """Own trigger instances, run loops, pause state, and event admission."""

    def __init__(
        self,
        process_event: Callable[..., Coroutine[Any, Any, None]],
    ) -> None:
        self._triggers: dict[str, BaseTrigger] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._created_at: dict[str, datetime] = {}
        self._process_event = process_event
        # Observers receive each firing without participating in delivery.
        self.on_trigger_fired: Callable[[str, Any], None] | None = None
        # Backlog admission folds an already-ready burst into one turn; hosts
        # without mid-turn buffering leave this unset.
        self.admit_ready: Callable[[list[Any]], int] | None = None
        # Pausing blocks loops before they consume another source event.
        self._resume_gate = asyncio.Event()
        self._resume_gate.set()
        # The agent attaches persistence after session initialization.
        self._session_store: Any = None
        self._agent_name: str = ""

    async def add(
        self,
        trigger: BaseTrigger,
        trigger_id: str | None = None,
        autostart: bool = True,
    ) -> str:
        """Register a trigger and optionally start its run loop immediately."""
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
                logger.warning(
                    "Failed to save trigger state", error=str(e), exc_info=True
                )
        else:
            logger.debug(
                "Trigger registered (not started)",
                trigger_id=trigger_id,
                trigger_type=type(trigger).__name__,
            )

        return trigger_id

    async def remove(self, trigger_id: str) -> bool:
        """Stop and remove a trigger, reporting whether it existed."""
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
                logger.warning(
                    "Trigger task cleanup error", error=str(e), exc_info=True
                )

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
        """Start every registered trigger that lacks a run-loop task."""
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
        """Cancel all run loops, stop triggers, and clear registrations."""
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

    def suspend_all(self) -> None:
        """Pause trigger loops before their next source read.

        Source queues continue accumulating until ``resume_all`` releases the loops.
        """
        self._resume_gate.clear()

    def resume_all(self) -> None:
        """Release trigger run-loops suspended by :meth:`suspend_all`."""
        self._resume_gate.set()

    async def _run_loop(self, trigger_id: str, trigger: BaseTrigger) -> None:
        """Run a single trigger's event loop."""
        while trigger.is_running:
            try:
                # Gate before source consumption so pausing cannot lose an event.
                await self._resume_gate.wait()
                if not trigger.is_running:
                    break
                event = await trigger.wait_for_trigger()
                if event:
                    logger.info(
                        "Trigger fired",
                        trigger_id=trigger_id,
                        event_type=event.type,
                    )
                    # Record only material scheduling delays to avoid telemetry noise.
                    self._maybe_emit_schedule_drift(trigger_id, trigger, event)
                    self._fire_notification(trigger_id, event)
                    # Claim ready backlog before the primary turn starts so one burst
                    # is processed as one turn rather than repeated rounds.
                    self._admit_extra_ready(trigger_id, trigger)
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

    def _fire_notification(self, trigger_id: str, event: Any) -> None:
        """Fire the ``on_trigger_fired`` callback for one event."""
        if not self.on_trigger_fired:
            return
        try:
            self.on_trigger_fired(trigger_id, event)
        except Exception as e:
            logger.warning(
                "on_trigger_fired callback error",
                error=str(e),
                exc_info=True,
            )

    def _admit_extra_ready(self, trigger_id: str, trigger: BaseTrigger) -> None:
        """Admit an already-ready trigger backlog into the current turn.

        Draining occurs only when an admission hook exists, preventing destructive
        reads on hosts that cannot accept the events. Each event retains normal
        notification and drift observability.
        """
        admit = self.admit_ready
        drain = getattr(trigger, "drain_ready", None)
        if admit is None or not callable(drain):
            return
        try:
            extra = drain()
        except Exception as e:  # pragma: no cover - backlog failure is isolated
            logger.warning(
                "trigger drain_ready failed",
                trigger_id=trigger_id,
                error=str(e),
                exc_info=True,
            )
            return
        if not extra:
            return
        for ev in extra:
            self._maybe_emit_schedule_drift(trigger_id, trigger, ev)
            self._fire_notification(trigger_id, ev)
        admitted = admit(extra)
        logger.info(
            "Admitted channel backlog into one turn",
            trigger_id=trigger_id,
            count=admitted,
        )

    def _maybe_emit_schedule_drift(
        self, trigger_id: str, trigger: BaseTrigger, event: Any
    ) -> None:
        """Record material delay between a scheduled time and actual firing."""
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
        except Exception as e:  # pragma: no cover - telemetry must not stop triggers
            logger.warning("schedule_drift emit failed", error=str(e), exc_info=True)
