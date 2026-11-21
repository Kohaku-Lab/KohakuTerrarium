"""Agent lifecycle helpers.

Centralizes warm-pause and orderly shutdown behavior for agents.
"""

import asyncio
from typing import Any, Awaitable

from kohakuterrarium.errors import (
    ConflictError,
    InvalidRequestError,
    ShutdownIncompleteError,
)
from kohakuterrarium.core.asyncio_compat import request_cancellation
from kohakuterrarium.core.agent.shutdown import (
    FORCE_SETTLE_SECONDS,
    JOIN_SLACK_SECONDS,
    AbandonedTask,
    ShutdownDeadline,
    ShutdownReport,
    ShutdownState,
    log_late_failure,
    spawn_stage,
)
from kohakuterrarium.core.jobs.job import JobState, JobType
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

TURN_LOCK_WAIT_SECONDS = 10.0


async def wait_for_turn_completion(
    agent: Any, completion: asyncio.Future | None, *, timeout: float = 10.0
) -> None:
    """Join one captured turn without cancelling it when the waiter times out."""
    if completion is None or completion.done():
        return
    if getattr(agent, "_turn_lock_holder", None) is asyncio.current_task():
        raise ConflictError("Cannot wait for the current turn from its own callback")
    try:
        await asyncio.wait_for(asyncio.shield(completion), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise ConflictError(
            "The previous turn is still finishing; retry after it stops"
        ) from exc


class AgentLifecycleMixin:
    """Provide warm-pause and orderly shutdown behavior for an agent."""

    plugins: Any
    compact_manager: Any
    llm: Any
    output_router: Any
    subagent_manager: Any
    trigger_manager: Any
    input: Any
    config: Any
    _running: bool
    _paused: bool
    _shutdown_event: Any

    @property
    def is_processing(self) -> bool:
        """Whether a turn is active, finalizing, or waiting in the inbox."""
        lock = getattr(self, "_processing_lock", None)
        inbox = getattr(self, "_event_inbox", None)
        return bool(
            getattr(self, "_active_event_run", None)
            or (lock is not None and lock.locked())
            or (inbox is not None and len(inbox))
        )

    async def interrupt_and_wait(self, *, timeout: float = 10.0) -> None:
        """Interrupt the captured turn and wait until its finalization releases it."""
        completion = getattr(self, "_turn_completion", None)
        self.interrupt()
        await wait_for_turn_completion(self, completion, timeout=timeout)

    @property
    def paused(self) -> bool:
        """Return whether the agent is warm-paused and rejecting new turns."""
        return getattr(self, "_paused", False)

    def pause(self) -> None:
        """Idempotently pause admission and triggers while queued events remain pending."""
        if getattr(self, "_paused", False):
            return
        self._paused = True
        self._consumer_resume.clear()
        self.trigger_manager.suspend_all()
        logger.info("Agent paused", agent_name=self.config.name)

    def resume(self) -> None:
        """Idempotently resume triggers and drain events queued during the pause."""
        if not getattr(self, "_paused", False):
            return
        self._paused = False
        self.trigger_manager.resume_all()
        # One wake releases the gate and lets the consumer claim the full queued batch.
        self._consumer_resume.set()
        self._event_inbox.wake()
        logger.info("Agent resumed", agent_name=self.config.name)

    async def stop(
        self, *, grace: float | None = None, force: bool = False
    ) -> ShutdownReport:
        """Stop all agent modules once; concurrent and repeated callers join it.

        ``grace`` bounds caller waiting with one deadline shared by every stage;
        later requests may only tighten it. ``force`` authorises one escalated
        cancellation of work still running at the deadline and needs a bounded
        deadline. Returns a settled ``ShutdownReport``; raises
        ``ShutdownIncompleteError`` when work was forced or abandoned. Without
        ``grace`` the shutdown waits for cooperative settlement indefinitely.
        """
        if grace is not None and grace < 0:
            raise InvalidRequestError("grace must be a non-negative number of seconds")
        state: ShutdownState | None = getattr(self, "_shutdown_state", None)
        if force and grace is None and (state is None or not state.deadline.bounded):
            raise InvalidRequestError("force=True requires a grace deadline")
        if state is None:
            state = ShutdownState(ShutdownDeadline(grace), force_requested=force)
            state.done = asyncio.get_running_loop().create_future()
            self._shutdown_state = state
            return await self._own_shutdown(state)
        if grace is not None:
            state.deadline.tighten(grace)
        if force:
            state.force_requested = True
            state.deadline.wake()
        if not state.done.done():
            await self._join_shutdown(state)
        elif force and not state.forced and state.live_abandoned():
            await self._escalate_abandoned(state)
        if not state.done.cancelled() and state.done.exception() is not None:
            raise state.done.exception()
        return self._shutdown_outcome(state)

    async def _own_shutdown(self, state: ShutdownState) -> ShutdownReport:
        """Run the shutdown this caller started and publish its outcome."""
        try:
            await self._stop_inner(state)
        except BaseException as exc:
            self._shutdown_state = None
            if not state.done.done():
                state.done.set_exception(exc)
                state.done.exception()
            raise
        state.done.set_result(None)
        self._stopped = True
        if state.errors and not (state.abandoned or state.forced):
            # A failed but settled stop may be retried, e.g. to close providers.
            self._shutdown_state = None
            self._stopped = False
        return self._shutdown_outcome(state)

    async def _join_shutdown(self, state: ShutdownState) -> None:
        """Wait for an in-flight shutdown, bounded by its shared deadline."""
        while not state.done.done():
            remaining = state.deadline.remaining()
            budget = (
                None
                if remaining is None
                else remaining + FORCE_SETTLE_SECONDS + JOIN_SLACK_SECONDS
            )
            woke = await state.deadline.wait_change(state.done, budget)
            if not woke and not state.done.done():
                self._mark_expired(state, "shutdown")
                raise ShutdownIncompleteError(state.build_report())

    def _shutdown_outcome(self, state: ShutdownState) -> ShutdownReport:
        """Return the settled report or raise the highest-precedence failure."""
        report = state.report = state.build_report()
        if not report.settled:
            cause = state.errors[0] if state.errors else None
            raise ShutdownIncompleteError(report) from cause
        if state.errors:
            raise state.errors[0]
        return report

    def _prepare_restart(self) -> None:
        """Clear a finished shutdown so ``start`` can run the agent again."""
        state: ShutdownState | None = getattr(self, "_shutdown_state", None)
        if state is None:
            return
        if state.done is not None and not state.done.done():
            raise ConflictError("The agent is still stopping")
        if live := state.live_abandoned():
            raise ConflictError(
                "A previous shutdown abandoned work that is still running: "
                + ", ".join(item.label for item in live)
            )
        self._shutdown_state = None
        self._restore_sinks()

    def _revoke_sinks(self) -> None:
        """Close the conversation and outputs to writes from abandoned work."""
        router = getattr(self, "output_router", None)
        if callable(getattr(router, "revoke", None)):
            router.revoke()
        conversation = getattr(getattr(self, "controller", None), "conversation", None)
        if callable(getattr(conversation, "seal", None)):
            conversation.seal()

    def _restore_sinks(self) -> None:
        """Reopen the sinks a previous incomplete shutdown revoked."""
        router = getattr(self, "output_router", None)
        if callable(getattr(router, "restore", None)):
            router.restore()
        conversation = getattr(getattr(self, "controller", None), "conversation", None)
        if callable(getattr(conversation, "unseal", None)):
            conversation.unseal()

    def _mark_expired(self, state: ShutdownState, stage: str) -> None:
        """Record the first stage that hit the deadline and revoke the sinks."""
        if state.expired_stage is not None:
            return
        state.expired_stage = stage
        self._revoke_sinks()
        logger.warning(
            "Shutdown deadline expired; abandoning unsettled work",
            agent_name=self.config.name,
            stage=stage,
        )

    def _abandon(
        self, state: ShutdownState, labelled: dict[asyncio.Task, str], stage: str
    ) -> None:
        """Stop waiting for ``labelled`` tasks and report them as abandoned."""
        if not labelled:
            return
        self._mark_expired(state, stage)
        known = {id(item.task) for item in state.abandoned}
        for task, label in labelled.items():
            if id(task) in known:
                continue
            state.abandoned.append(AbandonedTask(label, task))
            task.add_done_callback(log_late_failure)

    async def _join_work(
        self, state: ShutdownState, labelled: dict[asyncio.Task, str], stage: str
    ) -> None:
        """Join cancelled work until the deadline; escalate or abandon the rest."""
        pending = await state.deadline.join(set(labelled), force=state.forced)
        if pending and state.force_requested and state.deadline.bounded:
            state.forced = True
            state.deadline.open_force_window()
            for task in pending:
                # Explicit escalation deliberately repeats cancellation.
                task.cancel()
            pending = await state.deadline.join(pending, force=True)
        self._abandon(state, {task: labelled[task] for task in pending}, stage)

    async def _run_stage(
        self, state: ShutdownState, name: str, awaitable: Awaitable[Any]
    ) -> Any:
        """Run one cleanup stage, abandoning it uncancelled at the deadline.

        An unbounded shutdown awaits the stage inline, so it never yields to
        the loop before cancelling the live turn.
        """
        if not state.deadline.bounded:
            return await awaitable
        task = spawn_stage(f"{self.config.name}:{name}", awaitable)
        pending = await state.deadline.join({task}, force=state.forced)
        if pending:
            self._abandon(state, {task: f"stage:{name}"}, name)
            return None
        return task.result()

    async def _escalate_abandoned(self, state: ShutdownState) -> None:
        """Force-cancel work that a finished, incomplete shutdown left running."""
        live = {
            item.task
            for item in state.live_abandoned()
            if not item.label.startswith("stage:")
        }
        state.forced = True
        if not live:
            return
        state.deadline.open_force_window()
        for task in live:
            task.cancel()
        await state.deadline.join(live, force=True)

    async def _stop_inner(self, state: ShutdownState) -> None:
        logger.info("Stopping agent", agent_name=self.config.name)

        if self.plugins:
            await self._run_stage(
                state, "plugin_stop_notify", self.plugins.notify("on_agent_stop")
            )
            await self._run_stage(state, "plugin_unload", self.plugins.unload_all())

        # Job terminals require both the running state and output router to remain live.
        had_direct_jobs = self._finalize_inflight_jobs_for_stop()

        self._running = False
        self._shutdown_event.set()

        # Wake the single event consumer so it observes ``_running`` False
        # and exits instead of parking forever on the inbox / resume gate.
        consumer_resume = getattr(self, "_consumer_resume", None)
        if consumer_resume is not None:
            consumer_resume.set()
        inbox = getattr(self, "_event_inbox", None)
        if inbox is not None:
            inbox.wake()

        # Unwind a live turn before closing its sinks, but never self-cancel stop().
        current = asyncio.current_task()
        processing = getattr(self, "_processing_task", None)
        if (
            processing is not None
            and not processing.done()
            and processing is not current
        ):
            request_cancellation(processing)
            await self._join_work(state, {processing: "processing"}, "processing")
        # The processing lock also covers output, wiring, and session finalization.
        turn_lock = getattr(self, "_processing_lock", None)
        if (
            turn_lock is not None
            and getattr(self, "_turn_lock_holder", None) is not current
        ):
            await self._await_turn_lock(state, turn_lock)

        # Consumer cancellation rejects pending futures so callers cannot hang on shutdown.
        consumer = getattr(self, "_consumer_task", None)
        if consumer is not None and not consumer.done() and consumer is not current:
            consumer.cancel()
            await self._join_work(state, {consumer: "consumer"}, "consumer")

        if hasattr(self, "_mcp_manager") and self._mcp_manager:
            await self._run_stage(state, "mcp", self._mcp_manager.shutdown())

        await self._cancel_executor_tasks(state)
        await self._cancel_subagents(state)
        settlement_error = None
        try:
            await self._await_interrupt_finalizers(state)
        except RuntimeError as exc:
            settlement_error = exc
            state.errors.append(exc)
        if had_direct_jobs and settlement_error is None and not state.abandoned:
            session_output = getattr(self, "_session_output", None)
            persist_snapshot = getattr(session_output, "on_processing_end", None)
            if callable(persist_snapshot):
                await self._run_stage(state, "persist_snapshot", persist_snapshot())
        controller = getattr(self, "controller", None)
        cleanup_inline_files = getattr(controller, "cleanup_inline_files", None)
        if callable(cleanup_inline_files):
            cleanup_inline_files()
        await self._run_stage(state, "triggers", self.trigger_manager.stop_all())
        await self._run_stage(state, "input", self.input.stop())
        if self.compact_manager:
            await self._run_stage(state, "compact", self.compact_manager.cancel())
        await self._run_stage(state, "output", self.output_router.stop())
        await self._close_providers(state)

    async def _await_turn_lock(self, state: ShutdownState, turn_lock: Any) -> None:
        """Wait for turn finalization to release the processing lock."""
        acquire = asyncio.ensure_future(turn_lock.acquire())
        if state.deadline.bounded:
            pending = await state.deadline.join({acquire}, force=state.forced)
        else:
            _, pending = await asyncio.wait({acquire}, timeout=TURN_LOCK_WAIT_SECONDS)
        if pending:
            acquire.cancel()
            await asyncio.gather(acquire, return_exceptions=True)
        if acquire.done() and not acquire.cancelled() and acquire.result():
            turn_lock.release()
        if not pending:
            return
        if not state.deadline.bounded:
            logger.warning(
                "Timed out waiting for turn finalization during stop",
                agent_name=self.config.name,
            )
            return
        self._mark_expired(state, "turn_finalization")
        holder = getattr(self, "_turn_lock_holder", None)
        if isinstance(holder, asyncio.Task) and not holder.done():
            self._abandon(state, {holder: "turn_finalization"}, "turn_finalization")

    async def _close_providers(self, state: ShutdownState) -> None:
        """Close every distinct provider, keeping failed ones for a retry."""
        compact_llm = (
            getattr(self.compact_manager, "_llm", None)
            if self.compact_manager
            else None
        )
        providers = [*getattr(self, "_retired_llms", []), compact_llm, self.llm]
        seen: set[int] = set()
        failed: list[Any] = []
        for provider in providers:
            if provider is None or id(provider) in seen:
                continue
            seen.add(id(provider))
            if not hasattr(provider, "close"):
                continue
            try:
                await self._run_stage(state, "provider_close", provider.close())
            except Exception as exc:
                failed.append(provider)
                state.errors.append(exc)
        self._retired_llms = failed

    def _finalize_inflight_jobs_for_stop(self) -> bool:
        """Finalize each running job as interrupted exactly once during shutdown."""
        direct_jobs = getattr(self, "_direct_job_meta", {})
        active_direct_jobs = [
            job_id
            for job_id, meta in direct_jobs.items()
            if not meta.get("background")
            and meta.get("interruptible", True)
            and not getattr(
                getattr(self, "_active_handles", {}).get(job_id), "promoted", False
            )
        ]
        for job_id in active_direct_jobs:
            meta = direct_jobs[job_id]
            meta["stop_requested"] = True
            self._start_interrupt_finalizer(job_id)
        router = getattr(self, "output_router", None)
        if router is None or not hasattr(router, "notify_activity"):
            return bool(active_direct_jobs)
        jobs: list[tuple[Any, Any]] = []
        executor = getattr(self, "executor", None)
        if executor is not None and hasattr(executor, "get_running_jobs"):
            store = getattr(executor, "job_store", None)
            jobs.extend((status, store) for status in executor.get_running_jobs())
        manager = getattr(self, "subagent_manager", None)
        if manager is not None and hasattr(manager, "get_running_jobs"):
            store = getattr(manager, "job_store", None)
            jobs.extend((status, store) for status in manager.get_running_jobs())
        had_jobs = bool(active_direct_jobs or jobs)
        seen: set[str] = set()
        for status, store in jobs:
            job_id = getattr(status, "job_id", None)
            if not job_id or job_id in seen:
                continue
            if job_id in active_direct_jobs:
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
            # Pair only announced native calls; an orphan result would invalidate text-mode history.
            meta = getattr(self, "_direct_job_meta", {}).get(job_id) or {}
            tool_call_id = meta.get("tool_call_id")
            controller = getattr(self, "controller", None)
            if tool_call_id and controller is not None:
                if controller.conversation.owes_tool_response(
                    tool_call_id, meta.get("announcement")
                ):
                    metadata["tool_call_id"] = tool_call_id
                    try:
                        controller.conversation.append(
                            "tool",
                            error,
                            tool_call_id=tool_call_id,
                            name=meta.get("name") or name,
                            metadata={"job_id": job_id, "tool_result_phase": "final"},
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
        return had_jobs

    async def _await_interrupt_finalizers(
        self, state: ShutdownState | None = None
    ) -> None:
        """Wait for interrupted direct jobs before closing result sinks."""
        state = state or ShutdownState(ShutdownDeadline(None))
        finalizers = tuple(getattr(self, "_interrupt_finalizers", ()))
        if finalizers:
            labelled = {task: f"finalizer:{task.get_name()}" for task in finalizers}
            await self._join_work(state, labelled, "finalizers")
        if getattr(self, "_interrupt_finalizer_error", None):
            raise RuntimeError(
                f"Interrupted job settlement failed: {self._interrupt_finalizer_error}"
            )

    async def _cancel_executor_tasks(self, state: ShutdownState | None = None) -> None:
        """Request cancellation of running tool jobs once and join them."""
        state = state or ShutdownState(ShutdownDeadline(None))
        executor = getattr(self, "executor", None)
        if executor is None:
            return
        finalizers = getattr(self, "_interrupt_finalizers", ())
        labelled = {
            task: f"job:{job_id}"
            for job_id, task in executor._tasks.items()
            if not task.done() and task not in finalizers
        }
        if not labelled:
            return
        for task in labelled:
            request_cancellation(task)
        await self._join_work(state, labelled, "jobs")

    async def _cancel_subagents(self, state: ShutdownState) -> None:
        """Request cancellation of sub-agents once, join them, stop interactives."""
        manager = self.subagent_manager
        request_all = getattr(manager, "request_cancel_all", None)
        if not callable(request_all):
            await self._run_stage(state, "subagents", manager.cancel_all())
            return
        labelled = {
            task: f"subagent:{job_id}" for job_id, task in request_all().items()
        }
        if labelled:
            await self._join_work(state, labelled, "subagents")
        await self._run_stage(
            state, "interactive_subagents", manager.stop_all_interactive()
        )
