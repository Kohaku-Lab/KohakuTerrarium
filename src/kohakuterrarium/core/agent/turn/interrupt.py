"""Agent interrupt, per-job cancel and foreground-to-background promotion."""

import asyncio
from typing import Any

from kohakuterrarium.core.event_inbox import TurnOutcome
from kohakuterrarium.core.job import JobState
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentInterruptMixin:
    """Stop the active turn, cancel single jobs, promote direct jobs."""

    def _init_interrupt_state(self) -> None:
        # Interrupt: flag + task reference for immediate cancellation
        self._interrupt_requested = False
        self._processing_task: asyncio.Task | None = None
        self._turn_completion: asyncio.Future[None] | None = None
        self._branch_request_id: str | None = None
        self._branch_execution_error: tuple[str | None, str] | None = None

        self._active_handles: dict[str, Any] = {}
        self._direct_job_meta: dict[str, dict[str, Any]] = {}
        self._bg_controller_notify: dict[str, bool] = {}
        # Deliverable background jobs (notify=True, non-stateful/-interactive)
        # this turn spawned; the output-wiring guard defers only on these.
        self._turn_dispatched_bg: set[str] = set()

    def interrupt(self) -> None:
        """Cancel the active turn; background jobs untouched.

        The consumer's turn body swallows the cancellation, finalizes, and
        loops — so any events still queued on the inbox process as the next
        fresh turn (no explicit re-fire needed). Queued Drive deliveries
        are dropped WITHOUT running and settle as user-interrupted, so the
        dispatcher pauses their drive instead of redelivering."""
        active = getattr(self, "_active_event_run", None)
        if active:
            active[0].event.context["interrupted_by_user"] = True
        self._interrupt_active_turn()
        if self.plugins:
            asyncio.create_task(self.plugins.notify("on_interrupt"))
        dropped = self._event_inbox.remove_where(
            lambda ev: ev.type.startswith("drive_")
        )
        for env in dropped:
            fut = env.future
            if fut is not None and not fut.done():
                fut.set_result(
                    TurnOutcome(
                        status="interrupted",
                        was_primary=False,
                        interrupted_by_user=True,
                    )
                )
        if dropped:
            logger.info(
                "Dropped queued Drive deliveries after user interrupt",
                count=len(dropped),
            )
        logger.info("Agent interrupted", agent_name=self.config.name)

    def _interrupt_active_turn(self) -> None:
        """Cancel the current controller and its foreground jobs."""
        already_requested = self._interrupt_requested
        self._interrupt_requested = True
        self.controller._interrupted = True
        processing = getattr(self, "_processing_task", None)
        if processing and not processing.done() and not already_requested:
            processing.cancel()
        for job_id in list(self._active_handles.keys()):
            self._interrupt_direct_job(job_id)

    def _cancel_job(self, job_id: str, job_name: str) -> None:
        """Cancel a single running job by ID (tool or sub-agent).

        Called from the TUI running panel click handler.
        """
        cancelled = self._interrupt_direct_job(job_id)

        if not cancelled:
            # Try executor (tools) first
            task = self.executor._tasks.get(job_id)
            if task and not task.done():
                task.cancel()
                self.executor.job_store.update_status(job_id, state=JobState.CANCELLED)
                cancelled = True

            # Try sub-agent manager. Use cooperative cancellation so
            # any tokens accumulated before cancellation are preserved in
            # the final SubAgentResult and session trace.
            if not cancelled:
                sa_task = self.subagent_manager._tasks.get(job_id)
                if sa_task and not sa_task.done():
                    job = self.subagent_manager._jobs.get(job_id)
                    if job and hasattr(job, "subagent"):
                        job.subagent.cancel()
                    sa_task.cancel()
                    cancelled = True

        if cancelled:
            logger.info(
                "Job cancelled via TUI",
                job_id=job_id,
                job_name=job_name,
                agent_name=self.config.name,
            )
            # Notify output so the running panel updates
            self.output_router.notify_activity(
                "job_cancelled",
                f"Cancelled: {job_name}",
                metadata={"job_id": job_id, "job_name": job_name},
            )

    def _promote_handle(self, job_id: str) -> bool:
        """Promote a direct task to background. Thread-safe (TUI + API)."""
        handle = self._active_handles.get(job_id)
        if not handle:
            return False

        # Thread-safe promotion: asyncio.Event.set() must run on the
        # event loop thread. TUI calls this from Textual's thread.
        try:
            asyncio.get_running_loop()
            # Already on the event loop (API handler) — promote directly
            if not handle.promote():
                return False
        except RuntimeError:
            # TUI thread — schedule on the loop captured at ``start()``.
            # ``asyncio.get_event_loop()`` is unsafe on Python 3.14+.
            loop = getattr(self, "_loop", None)
            if loop is None or loop.is_closed():
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    return False
            loop.call_soon_threadsafe(handle.promote)

        self.output_router.notify_activity(
            "task_promoted",
            f"[{job_id}] Moved to background",
            metadata={"job_id": job_id},
        )
        logger.info("Task promoted via UI", job_id=job_id)
        return True
