"""Agent lifecycle helpers.

Split out of :mod:`agent` to keep the main orchestrator file below the
repository file-size guard while keeping shutdown behavior centralized.
"""

import asyncio
from typing import Any

from kohakuterrarium.core.job import JobState, JobType
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentLifecycleMixin:
    """Mixin providing agent shutdown behavior."""

    plugins: Any
    compact_manager: Any
    llm: Any
    output_router: Any
    subagent_manager: Any
    trigger_manager: Any
    input: Any
    config: Any
    _running: bool
    _shutdown_event: Any

    async def stop(self) -> None:
        """Stop all agent modules. Safe to call concurrently / twice —
        the second caller waits for the first teardown and returns."""
        stop_lock = getattr(self, "_stop_lock", None)
        if stop_lock is None:
            stop_lock = self._stop_lock = asyncio.Lock()
        async with stop_lock:
            if getattr(self, "_stopped", False):
                return
            await self._stop_inner()
            self._stopped = True

    async def _stop_inner(self) -> None:
        logger.info("Stopping agent", agent_name=self.config.name)

        if self.plugins:
            await self.plugins.notify("on_agent_stop")
            await self.plugins.unload_all()

        # Must run before ``_running`` flips and the router stops —
        # the cancel paths cannot persist job terminals after that.
        self._finalize_inflight_jobs_for_stop()

        self._running = False
        self._shutdown_event.set()

        # A live turn must be fully unwound BEFORE the router stops and
        # the providers close — otherwise processing continues against
        # closed sinks after stop() returns. Skip when stop() is called
        # from inside the turn itself (self-cancel would kill stop()).
        processing = getattr(self, "_processing_task", None)
        if (
            processing is not None
            and not processing.done()
            and processing is not asyncio.current_task()
        ):
            processing.cancel()
            await asyncio.gather(processing, return_exceptions=True)
        # The inner loop is down; the OUTER turn (finalization: output
        # flush, wiring, session bookkeeping) still runs under the
        # processing lock — join it too, unless stop() was called from
        # inside the turn itself.
        turn_lock = getattr(self, "_processing_lock", None)
        if (
            turn_lock is not None
            and getattr(self, "_turn_lock_holder", None) is not asyncio.current_task()
        ):
            try:
                await asyncio.wait_for(turn_lock.acquire(), timeout=10)
                turn_lock.release()
            except asyncio.TimeoutError:  # pragma: no cover - defensive
                logger.warning(
                    "Timed out waiting for turn finalization during stop",
                    agent_name=self.config.name,
                )

        if hasattr(self, "_mcp_manager") and self._mcp_manager:
            await self._mcp_manager.shutdown()

        await self._cancel_executor_tasks()
        await self.subagent_manager.cancel_all()
        await self.trigger_manager.stop_all()
        await self.input.stop()
        if self.compact_manager:
            await self.compact_manager.cancel()
        await self.output_router.stop()
        compact_llm = (
            getattr(self.compact_manager, "_llm", None)
            if self.compact_manager
            else None
        )
        if (
            compact_llm is not None
            and compact_llm is not self.llm
            and hasattr(compact_llm, "close")
        ):
            await compact_llm.close()
        await self.llm.close()

    def _finalize_inflight_jobs_for_stop(self) -> None:
        """Emit a genuine interrupted terminal for every still-running
        job so the session log terminates cleanly. Each swept job's
        status transitions to CANCELLED, so a repeat sweep finds
        nothing — one terminal per job, no matter how often stop()
        runs."""
        router = getattr(self, "output_router", None)
        if router is None or not hasattr(router, "notify_activity"):
            return
        jobs: list[tuple[Any, Any]] = []
        executor = getattr(self, "executor", None)
        if executor is not None and hasattr(executor, "get_running_jobs"):
            store = getattr(executor, "job_store", None)
            jobs.extend((status, store) for status in executor.get_running_jobs())
        manager = getattr(self, "subagent_manager", None)
        if manager is not None and hasattr(manager, "get_running_jobs"):
            store = getattr(manager, "job_store", None)
            jobs.extend((status, store) for status in manager.get_running_jobs())
        seen: set[str] = set()
        for status, store in jobs:
            job_id = getattr(status, "job_id", None)
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)
            is_subagent = getattr(status, "job_type", None) == JobType.SUBAGENT
            name = getattr(status, "type_name", "") or job_id
            error = "Agent stopped while this job was still running."
            if store is not None and hasattr(store, "update_status"):
                store.update_status(job_id, state=JobState.CANCELLED, error=error)
            metadata: dict[str, Any] = {
                "job_id": job_id,
                "interrupted": True,
                "cancelled": False,
                "final_state": "interrupted",
                "error": error,
            }
            if is_subagent:
                metadata["result"] = error
            # A native-mode job has an unanswered assistant.tool_calls
            # announcement in the conversation — pair it with an
            # interrupted result NOW, or the processing-end snapshot's
            # provider-safe serialization drops the whole round and the
            # resumed model never learns the call happened. Text-mode
            # jobs have no announcement (meta defaults tool_call_id to
            # job_id) — appending would create an orphan, so only pair
            # ids the conversation actually announces.
            meta = getattr(self, "_direct_job_meta", {}).get(job_id) or {}
            tool_call_id = meta.get("tool_call_id")
            controller = getattr(self, "controller", None)
            if tool_call_id and controller is not None:
                announced = any(
                    any(
                        tc.get("id") == tool_call_id
                        for tc in (getattr(m, "tool_calls", None) or [])
                    )
                    for m in controller.conversation.get_messages()
                )
                if announced:
                    metadata["tool_call_id"] = tool_call_id
                    try:
                        controller.conversation.append(
                            "tool",
                            error,
                            tool_call_id=tool_call_id,
                            name=meta.get("name") or name,
                        )
                    except Exception:  # pragma: no cover - defensive
                        logger.warning(
                            "Failed to pair interrupted tool result",
                            job_id=job_id,
                            exc_info=True,
                        )
            router.notify_activity(
                "subagent_error" if is_subagent else "tool_error",
                f"[{name}] INTERRUPTED: {error}",
                metadata=metadata,
            )
            logger.info(
                "Finalized in-flight job on stop",
                job_id=job_id,
                kind="subagent" if is_subagent else "tool",
            )

    async def _cancel_executor_tasks(self) -> None:
        executor = getattr(self, "executor", None)
        if executor is None:
            return
        tasks = [task for task in executor._tasks.values() if not task.done()]
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
