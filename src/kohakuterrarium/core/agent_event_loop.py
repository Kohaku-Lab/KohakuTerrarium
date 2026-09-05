"""Single event consumer + batch-turn driver (queue-first event system).

Single-consumer event batching and serialized turn execution for agents.

Each inbox claim is segmented into maximal stackable runs, with one turn driven
per run. The processing lock allows shutdown to join an in-flight turn.
"""

import asyncio

from kohakuterrarium.core.agent_mid_turn import _to_serializable_content
from kohakuterrarium.core.agent_runtime_tools import _make_job_label
from kohakuterrarium.core.event_inbox import EventEnvelope, TurnOutcome
from kohakuterrarium.core.metrics_hook import metrics
from kohakuterrarium.core.pending_input import pending_id_of
from kohakuterrarium.llm.message import content_parts_to_dicts
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.skills.hints import inject_skill_path_hint
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Settle window (in cooperative yields) applied after a wake before the
# claim, so producers whose event reaches the inbox across several
# create_task / done-callback hops still land in the SAME batch. A
# background TOOL completion reaches the inbox in ~1 hop
# (``_run_tool`` → ``_on_bg_complete`` → ``create_task(_process_event)``);
# a backgroundified SUB-AGENT completion in ~3 (``_on_task_done``
# done-callback → ``create_task(_on_backgroundify_complete)`` →
# ``create_task(_process_event)``). A single yield would coalesce only
# the shallower path, letting a simultaneous sub-agent completion slip to
# a second turn. This budget spans that gap with margin. It is NOT a poll:
# a FIXED, bounded number of immediate reschedules — no timed sleep and no
# drain-until-empty loop — so a steady event stream can never extend it.
_COALESCE_HOPS = 3


def _is_fresh_user_input(event) -> bool:
    """A user_input that opens a NEW user turn (not a regen/edit rerun,
    whose branch was pre-incremented by its caller)."""
    ctx = event.context or {}
    return event.type == "user_input" and not ctx.get("rerun")


def segment_stackable(batch: list[EventEnvelope]) -> list[list[EventEnvelope]]:
    """Split a claimed batch into maximal stackable runs.

    A non-stackable event (startup / shutdown / error / Drive / rerun)
    becomes its own singleton run so it is the sole primary of its turn;
    a maximal span of stackable events becomes one run folded into one
    turn. Mirrors the controller's break-on-non-stackable batching."""
    runs: list[list[EventEnvelope]] = []
    cur: list[EventEnvelope] = []
    for env in batch:
        if env.event.stackable:
            cur.append(env)
        else:
            if cur:
                runs.append(cur)
                cur = []
            runs.append([env])
    if cur:
        runs.append(cur)
    return runs


class AgentEventLoopMixin:
    """The single consumer + batch-turn driver mixed into ``Agent``."""

    def _flush_trigger_backlog_stash(self) -> None:
        """Place a trigger's stashed backlog immediately after its primary.

        The synchronous flush admits no scheduling point, preserving adjacency
        and order in the inbox.
        """
        stash = getattr(self, "_trigger_backlog_stash", None)
        if not stash:
            return
        for event in stash:
            self._event_inbox.put(EventEnvelope(event))
        stash.clear()

    async def _run_event_consumer(self) -> None:
        """Drain the inbox forever: claim ALL → one turn per stackable run.

        The only park is ``inbox.wait_nonempty()`` (woken by ``put``) plus
        the warm-pause gate; both re-checked each loop so an event landing
        at any boundary is never lost and a paused agent processes nothing
        until resume."""
        try:
            while self._running:
                await self._consumer_resume.wait()
                if not self._running:
                    break
                await self._event_inbox.wait_nonempty()
                if not self._running:
                    break
                # Settle window: yield a small FIXED number of times so
                # simultaneous producers whose event reaches the inbox across
                # several create_task / done-callback hops (bg tool vs
                # sub-agent completion) accumulate, THEN claim them all in one
                # drain → ONE turn. Yielding BEFORE the drain keeps the inbox
                # non-empty throughout (no spurious-empty window). Bounded —
                # never a poll (see ``_COALESCE_HOPS``).
                for _ in range(_COALESCE_HOPS):
                    await asyncio.sleep(0)
                if not self._running:
                    break
                # Paused between the wake and here — re-park (invariant: the
                # queue may hold events while paused; they drain on resume).
                if not self._consumer_resume.is_set():
                    continue
                batch = self._event_inbox.drain_all()
                for run in segment_stackable(batch):
                    if not self._running:
                        self._reject_run(run, status="rejected")
                        continue
                    try:
                        await self._run_turn_for_batch(run)
                    except asyncio.CancelledError:
                        self._reject_run(run, status="interrupted")
                        raise
                    except Exception:
                        logger.error("Turn crashed in event consumer", exc_info=True)
                        self._reject_run(run, status="error")
        except asyncio.CancelledError:
            pass
        finally:
            self._drain_and_reject_inbox()

    async def _run_turn_for_batch(self, run: list[EventEnvelope]) -> None:
        """Run ONE turn for a stackable run and resolve its futures.

        Awaiting captures (``run`` / ``run_stream`` / ``run_event``) attach
        BEFORE the turn so they record it from the start and detach after.
        The turn mutex is held for the whole turn so ``stop()`` can join."""
        events = [env.event for env in run]
        primary = events[0]
        captures = [env.capture for env in run if env.capture is not None]
        completion = asyncio.get_running_loop().create_future()
        self._turn_completion = completion
        self._active_event_run = run
        self._active_event_captures = captures
        for cap in captures:
            self.output_router.add_secondary(cap)
        interrupted = False
        handoff: list[EventEnvelope] = []
        try:
            async with self._processing_lock:
                self._turn_lock_holder = asyncio.current_task()
                self._active_turn_stackable = primary.stackable
                try:
                    if not self._running:
                        self._reject_run(run, status="rejected")
                        return
                    await self._begin_batch(events)
                    await self._process_batch_with_controller(events, self.controller)
                    interrupted = bool(primary.context.get("interrupted_by_user"))
                finally:
                    self._turn_lock_holder = None
                    self._active_turn_stackable = True
                if interrupted:
                    self._flush_trigger_backlog_stash()
                    handoff = self._event_inbox.drain_all()
        finally:
            for cap in captures:
                self.output_router.remove_secondary(cap)
            self._active_event_run = None
            self._active_event_captures = None
            if self._turn_completion is completion:
                self._turn_completion = None
            if not completion.done():
                completion.set_result(None)
        self._resolve_run(
            run,
            status="interrupted" if interrupted else "ok",
            interrupted=interrupted,
        )
        if handoff and self._running:
            await self._run_turn_for_batch(handoff)

    def _resolve_run(
        self, run: list[EventEnvelope], *, status: str, interrupted: bool = False
    ) -> None:
        """Resolve every awaiting envelope's future at turn end.

        The first envelope of a fresh run is the primary (``was_primary``);
        the rest folded. Capture-based callers build their ``TurnResult``
        from their own capture and read only ``status`` + interrupt here."""
        for idx, env in enumerate(run):
            fut = env.future
            if fut is None or fut.done():
                continue
            fut.set_result(
                TurnOutcome(
                    status=status,
                    was_primary=(idx == 0),
                    interrupted_by_user=interrupted,
                )
            )

    def _reject_run(
        self, run: list[EventEnvelope], *, status: str = "rejected"
    ) -> None:
        """Resolve a run's futures as non-primary rejection (stop / crash)."""
        for env in run:
            fut = env.future
            if fut is not None and not fut.done():
                fut.set_result(TurnOutcome(status=status, was_primary=False))

    def _drain_and_reject_inbox(self) -> None:
        """On consumer exit, reject every leftover awaiting future so no
        ``run`` / ``run_event`` caller hangs after the agent stopped."""
        for env in self._event_inbox.drain_all():
            fut = env.future
            if fut is not None and not fut.done():
                fut.set_result(TurnOutcome(status="rejected", was_primary=False))

    async def _begin_batch(self, events: list) -> None:
        """Pre-turn side effects for the whole batch: primary branch /
        session / output / plugin bookkeeping, folded-event injected
        records, and ONE combined background-delivery banner."""
        primary = events[0]
        is_rerun = bool(primary.context.get("rerun")) if primary.context else False
        is_edited = bool(primary.context.get("edited")) if primary.context else False
        is_pure_rerun = is_rerun and not is_edited

        # A fresh (non-rerun) user_input introduces a NEW user turn — advance
        # even when it is FOLDED behind a non-user primary (e.g. a background
        # completion claimed in the same batch), so the folded message is
        # stamped under the new (turn_index, branch_id), not the previous
        # turn's stale ids. A rerun primary keeps its pre-incremented branch.
        if not is_rerun and any(_is_fresh_user_input(e) for e in events):
            self._advance_turn_for_user_input()
        if primary.type == "user_input" and not is_rerun:
            if self.session_store is not None:
                self._record_primary_user_input(primary)

        if (
            primary.type == "user_input"
            and self.output_router is not None
            and not is_pure_rerun
        ):
            content = (
                primary.get_text_content()
                if hasattr(primary, "is_multimodal") and primary.is_multimodal()
                else (primary.content or "")
            )
            await self.output_router.emit(
                OutputEvent(type="user_input", content=content)
            )

        if self.plugins is not None:
            await self.plugins.notify("on_event", event=primary)
        if primary.type == "user_input":
            inject_skill_path_hint(self)

        # Folded events get their own injected record + queued-banner-clear
        # frame (only user-facing kinds — background completions get the
        # combined delivery banner below instead).
        for evt in events[1:]:
            if evt.type in ("user_input", "trigger"):
                await self._record_folded_user_event(evt)

        self._emit_batch_background_banner(events)

    def _advance_turn_for_user_input(self) -> None:
        """Bump turn_index + allocate a collision-safe branch_id for a fresh
        user turn (was inline in ``_process_event``)."""
        if self._turn_index > 0 and self._branch_id > 0:
            self._parent_branch_path = list(self._parent_branch_path)
            self._parent_branch_path.append((self._turn_index, self._branch_id))
        self._turn_index += 1
        helper = getattr(self, "_max_branch_id_for_turn", None)
        existing_max = helper(self._turn_index) if helper else 0
        self._branch_id = existing_max + 1 if existing_max > 0 else 1

    def _record_primary_user_input(self, event) -> None:
        """Append the ``user_input`` + ``user_message`` session events for a
        fresh (non-rerun) user turn."""
        content = (
            content_parts_to_dicts(event.content)
            if hasattr(event, "is_multimodal") and event.is_multimodal()
            else (event.content or "")
        )
        ppath = [tuple(p) for p in self._parent_branch_path]
        payload = {"content": content}
        pending_id = pending_id_of(event)
        if pending_id:
            payload["pending_id"] = pending_id
        self.session_store.append_event(
            self.config.name,
            "user_input",
            dict(payload),
            turn_index=self._turn_index,
            branch_id=self._branch_id,
            parent_branch_path=ppath,
        )
        self.session_store.append_event(
            self.config.name,
            "user_message",
            dict(payload),
            turn_index=self._turn_index,
            branch_id=self._branch_id,
            parent_branch_path=ppath,
        )

    async def _record_folded_user_event(self, evt) -> None:
        """Record + notify one folded user_input/trigger event so the FE
        pops its queued banner and replay shows it as its own bubble."""
        content = _to_serializable_content(self._resolve_injected_content(evt))
        pending_id = pending_id_of(evt)
        self._record_injected_input_event(content, pending_id=pending_id)
        metadata = {
            "content": content,
            "turn_index": self._turn_index,
            "branch_id": self._branch_id,
        }
        if pending_id:
            metadata["pending_id"] = pending_id
        self.output_router.notify_activity(
            "user_input_injected",
            "",
            metadata=metadata,
        )
        await asyncio.sleep(0)

    def _emit_batch_background_banner(self, events: list) -> None:
        """Emit ONE ``background_result`` activity for every background
        completion in the batch — ``delivered: a, b, c`` instead of one
        banner per completion. Carries per-event metadata in ``jobs`` and
        keeps singular ``label`` / ``job_id`` for un-updated renderers."""
        jobs: list[dict] = []
        labels: list[str] = []
        kinds: set[str] = set()
        for evt in events:
            if evt.type not in ("tool_complete", "subagent_output"):
                continue
            job_id = getattr(evt, "job_id", "") or ""
            if not job_id:
                continue
            kind = (
                "subagent"
                if evt.type == "subagent_output" or job_id.startswith("agent_")
                else "tool"
            )
            _, label = _make_job_label(job_id)
            jobs.append({"job_id": job_id, "kind": kind, "label": label})
            labels.append(label)
            kinds.add(kind)
        if not jobs:
            return
        kind = next(iter(kinds)) if len(kinds) == 1 else "mixed"
        noun = "result" if len(labels) == 1 else "results"
        self.output_router.notify_activity(
            "background_result",
            f"background {noun} delivered: " + ", ".join(labels),
            metadata={
                "labels": labels,
                "jobs": jobs,
                "kind": kind,
                "count": len(labels),
                "label": labels[0] if len(labels) == 1 else ", ".join(labels),
                "job_id": jobs[0]["job_id"],
                "turn_index": self._turn_index,
                "branch_id": self._branch_id,
            },
        )

    def _notify_drive_turn(self, event) -> None:
        """Mark a turn that a Drive delivery started, for every output surface."""
        ctx = event.context or {}
        projected = ctx.get("drive") if isinstance(ctx.get("drive"), dict) else {}
        drive_id = str(ctx.get("drive_id") or "")
        kind = str(ctx.get("drive_kind") or projected.get("kind") or "drive")
        reason = str(ctx.get("delivery_reason") or event.type.removeprefix("drive_"))
        objective = str(projected.get("objective") or "")[:240]
        detail = f"[drive] {kind} {drive_id} ({reason})"
        if objective:
            detail += f": {objective}"
        self.output_router.notify_activity(
            "drive_turn",
            detail,
            metadata={
                "drive_id": drive_id,
                "drive_kind": kind,
                "delivery_reason": reason,
                "delivery_id": str(ctx.get("delivery_id") or ""),
                "objective": objective,
                "event_type": event.type,
                "turn_index": self._turn_index,
                "branch_id": self._branch_id,
            },
        )

    async def _process_batch_with_controller(self, events: list, controller) -> None:
        """Push the whole stackable run to the controller (one combined
        ``role=user`` message) and drive the controller loop. Cancellable
        via ``interrupt()``."""
        primary = events[0]
        self._prepare_processing_cycle(primary, controller)
        for event in events:
            await controller.push_event(event)
        await self.output_router.emit(
            OutputEvent(
                type="processing_start",
                payload={"request_id": self._branch_request_id},
            )
        )
        if primary.type.startswith("drive_"):
            self._notify_drive_turn(primary)

        all_round_text: list[str] = []
        loop_task = asyncio.create_task(
            self._run_controller_loop(controller, all_round_text)
        )
        self._processing_task = loop_task
        try:
            await loop_task
        except asyncio.CancelledError:
            logger.info("Processing cancelled by interrupt")
            self.output_router.notify_activity(
                "interrupt", "[system] Processing interrupted"
            )
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            logger.error("Processing error", error_type=error_type, error=error_msg)
            self.output_router.notify_activity(
                "processing_error",
                f"[{error_type}] {error_msg}",
                metadata={"error_type": error_type, "error": error_msg},
            )
            metrics.observe_error("controller")
        finally:
            self._processing_task = None
        if self._interrupt_requested:
            primary.context["interrupted_by_user"] = True
        await self._finalize_processing(primary, controller, all_round_text)
