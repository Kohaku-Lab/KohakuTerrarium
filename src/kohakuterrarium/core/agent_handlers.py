"""Agent event handling, tool dispatch, and result collection."""

import asyncio
import importlib

from kohakuterrarium.errors import AgentNotRunningError
from kohakuterrarium.core.agent_mid_turn import AgentMidTurnMixin
from kohakuterrarium.core.agent_output_wiring import AgentOutputWiringMixin
from kohakuterrarium.core.agent_pre_dispatch import (
    run_pre_subagent_dispatch,
    run_pre_tool_dispatch,
)
from kohakuterrarium.core.agent_tools import (
    AgentToolsMixin,
    _make_job_label,
    _TurnResult,
)
from kohakuterrarium.core.backgroundify import BackgroundifyHandle, backgroundify
from kohakuterrarium.core.budget import BudgetExhausted
from kohakuterrarium.core.controller import Controller
from kohakuterrarium.core.event_inbox import EventEnvelope
from kohakuterrarium.core.events import (
    EventType,
    TriggerEvent,
    create_tool_complete_event,
)
from kohakuterrarium.core.pending_input import stamp_pending_id
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.tool.base import ExecutionMode
from kohakuterrarium.parsing import (
    CommandResultEvent,
    SubAgentCallEvent,
    TextEvent,
    ToolCallEvent,
)
from kohakuterrarium.utils.logging import get_logger

_BG_PLACEHOLDER = (
    "Running in background — task delegated. "
    "Do NOT do this same task yourself — it is already being done. "
    "Do NOT use bash echo/sleep to wait — just end your response. "
    "Work on a DIFFERENT task or STOP your response now. "
    "Result arrives automatically in the next turn."
)

logger = get_logger(__name__)


class AgentHandlersMixin(AgentMidTurnMixin, AgentToolsMixin, AgentOutputWiringMixin):
    """Mixin providing event handling and tool execution for the Agent class.

    Contains the core event processing loop, tool startup, result collection,
    and background job status management.
    """

    async def _restore_triggers(self, saved_triggers: list[dict]) -> None:
        """Re-create resumable triggers from saved state."""
        for saved in saved_triggers:
            trigger_id = saved.get("trigger_id", "")
            type_name = saved.get("type", "")
            module_path = saved.get("module", "")
            data = saved.get("data", {})

            if not type_name or not module_path:
                continue

            # Skip triggers that already exist (e.g. config-defined ones)
            if trigger_id and trigger_id in self.trigger_manager._triggers:
                continue

            try:
                mod = importlib.import_module(module_path)
                cls = getattr(mod, type_name)
                trigger = cls.from_resume_dict(data)

                # Wire registry/session for ChannelTrigger
                if hasattr(trigger, "_registry") and trigger._registry is None:
                    if self.environment is not None:
                        trigger._registry = self.environment.shared_channels
                    elif self.session is not None:
                        trigger._registry = self.session.channels

                await self.trigger_manager.add(trigger, trigger_id=trigger_id)
                logger.info(
                    "Trigger restored",
                    trigger_id=trigger_id,
                    trigger_type=type_name,
                )
            except Exception as e:
                logger.warning(
                    "Failed to restore trigger",
                    trigger_id=trigger_id,
                    trigger_type=type_name,
                    error=str(e),
                )

    async def _fire_startup_trigger(self) -> None:
        """Fire startup trigger if configured."""
        startup_trigger = self.config.startup_trigger
        if not startup_trigger:
            return

        logger.info("Firing startup trigger")
        event = TriggerEvent(
            type=EventType.STARTUP,
            content=startup_trigger.get("prompt", "Agent starting up."),
            context={"trigger": "startup"},
            prompt_override=startup_trigger.get("prompt"),
            stackable=False,
        )
        await self._process_event(event)

    async def _process_event(self, event: TriggerEvent) -> bool:
        """Ingress: enqueue ``event`` on the single infinite inbox and,
        for a turn PRIMARY, await the turn that consumes it.

        The queue-first model (see :mod:`core.event_inbox` +
        :mod:`core.agent_event_loop`): one consumer drains the whole inbox
        each wake, so simultaneous events batch into ONE turn instead of
        each racing the processing lock for its own turn. This method only
        stages the event and reports whether it ran as a primary.

        Returns ``True`` when the event started (or was the primary of) a
        turn, ``False`` when it folded into a running turn or was queued
        while warm-paused. The WS attach path
        (``studio/attach/io.py:_process_input``) uses ``False`` to suppress
        the ``idle`` frame — the running turn owns the terminal frames.
        """
        is_rerun = bool(event.context.get("rerun")) if event.context else False
        awaits_turn = bool(event.context.get("await_turn")) if event.context else False
        paused = getattr(self, "_paused", False)

        if not self._running:
            # A stopped agent must not report a dropped event as accepted.
            # Strict (programmatic default) raises for user input; internal
            # kinds and lenient agents get an honest False.
            if event.type == "user_input" and getattr(self, "_strict", True):
                raise AgentNotRunningError(
                    f"Agent {self.config.name!r} is not running — "
                    "start() it before injecting input"
                )
            logger.debug("Dropping event, agent stopped", event_type=event.type)
            return False

        # Fold: a stackable event arriving while a STACKABLE turn holds the
        # mutex folds into that turn (fire-and-forget, returns False fast) —
        # the consumer's mid-turn re-claim drains it. Rerun / await_turn /
        # non-stackable events never fold, nor into a non-stackable turn.
        if (
            not is_rerun
            and not awaits_turn
            and event.stackable
            and event.type not in ("startup", "shutdown")
            and not paused
            and self._processing_lock.locked()
            and getattr(self, "_active_turn_stackable", True)
        ):
            stamp_pending_id(event)
            self._event_inbox.put(EventEnvelope(event))
            self._flush_trigger_backlog_stash()
            logger.info(
                "Event folded into running turn",
                event_type=event.type,
                queued=len(self._event_inbox),
            )
            return False

        # Warm pause: an awaiting caller (run / run_event) or a rerun
        # rejects WITHOUT enqueue — their contract is retry-later, and a
        # future held across the pause would stall the Drive dispatcher.
        # Fire-and-forget events enqueue and drain on resume (the consumer
        # parks on the resume gate).
        if paused:
            if awaits_turn or is_rerun:
                logger.info("Rejecting ingress while paused", event_type=event.type)
                return False
            stamp_pending_id(event)
            self._event_inbox.put(EventEnvelope(event))
            self._flush_trigger_backlog_stash()
            return False

        # Primary: enqueue with a future and await the turn that consumes it.
        fut = asyncio.get_running_loop().create_future()
        self._event_inbox.put(EventEnvelope(event, future=fut))
        self._flush_trigger_backlog_stash()
        outcome = await fut
        return bool(getattr(outcome, "was_primary", True))

    def _prepare_processing_cycle(
        self, event: TriggerEvent, controller: Controller
    ) -> None:
        """Reset state at the start of a new processing cycle."""
        self._interrupt_requested = False
        controller._interrupted = False
        # Fresh turn — forget the previous turn's backgrounded jobs so the
        # output-wiring guard only defers on work THIS turn spawns.
        self._turn_dispatched_bg = set()
        # Text-mode background-dispatch acknowledgments, delivered via
        # the next feedback round (native mode uses role=tool instead).
        self._bg_dispatch_acks: list[str] = []
        self.trigger_manager.set_context_all(event.context)
        if self._termination_checker:
            self._termination_checker.record_activity()
        # Reset per-turn token aggregator. ``_emit_token_usage`` adds
        # each LLM round's usage; ``_finalize_processing`` flushes a
        # single ``turn_token_usage`` event when the turn ends.
        if isinstance(getattr(self, "_turn_usage_accum", None), dict):
            for k in self._turn_usage_accum:
                self._turn_usage_accum[k] = 0

    async def _run_controller_loop(
        self, controller: Controller, all_round_text: list[str]
    ) -> None:
        """Inner loop: run LLM → dispatch tools → collect feedback → repeat."""
        while True:
            if self._interrupt_requested:
                self._interrupt_requested = False
                controller._interrupted = False
                self.output_router.notify_activity(
                    "interrupt", "[system] Processing interrupted"
                )
                break

            self._reset_output_state()

            round_result = await self._run_single_turn(controller)
            all_round_text.extend(round_result.text_output)
            # Track the final round's text separately for output-wiring
            # emission (REPLACE each iteration — we want only the last round).
            self._last_turn_text = list(round_result.text_output)

            # Emit token usage after each LLM turn (real-time update)
            self._emit_token_usage(controller)

            # Check interrupt after LLM turn (before waiting for tools)
            if self._interrupt_requested:
                self._cancel_handles(round_result.handles)
                self._interrupt_requested = False
                controller._interrupted = False
                self.output_router.notify_activity(
                    "interrupt", "[system] Processing interrupted"
                )
                break

            # Termination check
            if self._check_termination(round_result.text_output):
                break

            # Flush before collecting results (TUI renders text first)
            await self._flush_output()

            # Collect feedback and decide whether to continue
            should_continue = await self._collect_and_push_feedback(
                controller,
                round_result.handles,
                round_result.handle_order,
                round_result.native_tool_call_ids,
                round_result.native_mode,
            )
            if not should_continue:
                break

            # Mid-turn auto-compact: fire between loop iterations so
            # summarization can run while the agent continues. The
            # compact manager's single-flight gate suppresses duplicate
            # compact jobs while one is already in progress.
            self._maybe_trigger_compact(controller)

    async def _run_single_turn(self, controller: Controller) -> "_TurnResult":
        """Run one LLM turn, dispatching tools and sub-agents as they appear.

        Returns a ``_TurnResult`` with collected job info and text output.
        """
        handles: dict[str, BackgroundifyHandle] = {}
        handle_order: list[str] = []
        round_text: list[str] = []
        native_mode = getattr(controller.config, "tool_format", None) == "native"
        native_tool_call_ids: dict[str, str] = {}

        async for parse_event in controller.run_once():
            if self._interrupt_requested:
                break

            if isinstance(parse_event, ToolCallEvent):
                await self._dispatch_tool_event(
                    parse_event,
                    controller,
                    handles,
                    handle_order,
                    native_tool_call_ids,
                    native_mode,
                )
            elif isinstance(parse_event, SubAgentCallEvent):
                await self._dispatch_subagent_event(
                    parse_event,
                    controller,
                    handles,
                    handle_order,
                    native_tool_call_ids,
                    native_mode,
                )
            elif isinstance(parse_event, CommandResultEvent):
                self._notify_command_result(parse_event)
            else:
                if isinstance(parse_event, TextEvent):
                    round_text.append(parse_event.text)
                await self.output_router.route(parse_event)

        return _TurnResult(
            handles=handles,
            handle_order=handle_order,
            text_output=round_text,
            native_mode=native_mode,
            native_tool_call_ids=native_tool_call_ids,
        )

    async def _dispatch_tool_event(
        self,
        parse_event: ToolCallEvent,
        controller: Controller,
        handles: dict[str, BackgroundifyHandle],
        handle_order: list[str],
        native_tool_call_ids: dict[str, str],
        native_mode: bool,
    ) -> None:
        """Handle a ToolCallEvent: wrap in backgroundify and track."""
        # Plugins may rewrite or veto the call before any execution is scheduled.
        parse_event = await run_pre_tool_dispatch(self, parse_event, controller)
        if parse_event is None:
            return

        tool_call_id = parse_event.args.pop("_tool_call_id", None)
        run_bg = parse_event.args.pop("run_in_background", False)

        job_id, task, is_direct = await self._start_tool_async(parse_event)
        # A tool the executor submitted as background (is_direct=False at
        # submit time) gets its completion delivered by the executor's own
        # ``_on_complete`` callback. The backgroundify handle below must
        # NOT also fire ``_on_backgroundify_complete`` for it — that double
        # completion runs the controller an extra turn (B-fat2-core-1).
        # A *direct* tool promoted mid-flight (``run_bg`` or ``promote()``
        # during the wait) was submitted is_direct=True, so the executor
        # stays silent and the handle is the only completion path.
        executor_delivers_completion = not is_direct
        tool = self.executor.get_tool(parse_event.name)
        notify_controller_on_background_complete = True
        if tool is not None and hasattr(tool, "config"):
            notify_controller_on_background_complete = bool(
                getattr(
                    tool.config,
                    "notify_controller_on_background_complete",
                    True,
                )
            )
        self._bg_controller_notify[job_id] = notify_controller_on_background_complete

        # Three-level decision for execution mode
        if not is_direct:
            pass  # Tool declared BACKGROUND, respect it
        elif run_bg:
            is_direct = False

        # Wrap in backgroundify handle
        handle = backgroundify(
            task,
            job_id,
            on_bg_complete=(
                None
                if executor_delivers_completion
                else self._on_backgroundify_complete
            ),
            background_init=not is_direct,
        )

        if tool_call_id:
            native_tool_call_ids[job_id] = tool_call_id

        if handle.promoted:
            # A deliverable background tool defers this turn's output wire
            # until it reports back. STATEFUL (persistent, multi-turn) tools
            # and notify=False (fire-and-forget) tools do NOT — they would
            # strand the wire (they never drive a completion turn).
            if notify_controller_on_background_complete and (
                tool is None or tool.execution_mode != ExecutionMode.STATEFUL
            ):
                self._turn_dispatched_bg.add(job_id)
            # Already background — add placeholder
            if tool_call_id:
                controller.conversation.append(
                    "tool",
                    f"[{parse_event.name}] {_BG_PLACEHOLDER}",
                    tool_call_id=tool_call_id,
                    name=parse_event.name,
                )
            else:
                # Text mode has no role=tool slot — deliver the same
                # acknowledgment through the feedback round instead of
                # leaving the model with no dispatch confirmation.
                self._bg_dispatch_acks.append(
                    f"[{parse_event.name} → {job_id}] {_BG_PLACEHOLDER}"
                )
        else:
            # Direct — track for gathering (promotable mid-wait)
            handles[job_id] = handle
            handle_order.append(job_id)
            self._active_handles[job_id] = handle
            self._register_direct_job(
                job_id,
                kind="tool",
                name=parse_event.name,
                tool_call_id=tool_call_id,
                notify_controller_on_background_complete=notify_controller_on_background_complete,
            )

        logger.debug(
            "Tool started",
            tool_name=parse_event.name,
            job_id=job_id,
            direct=is_direct,
        )

        await self._flush_output()
        self._notify_tool_start(parse_event, job_id, is_direct)

    async def _dispatch_subagent_event(
        self,
        parse_event: SubAgentCallEvent,
        controller: Controller,
        handles: dict[str, BackgroundifyHandle] | None = None,
        handle_order: list[str] | None = None,
        native_tool_call_ids: dict[str, str] | None = None,
        native_mode: bool = False,
    ) -> None:
        """Handle a SubAgentCallEvent: wrap in backgroundify and track."""
        parse_event = await run_pre_subagent_dispatch(self, parse_event, controller)
        if parse_event is None:
            return
        sa_tool_call_id = parse_event.args.pop("_tool_call_id", None)
        full_task = parse_event.args.get("task", "")
        job_id, is_bg = await self._start_subagent_async(parse_event)
        cfg = self.subagent_manager._configs.get(parse_event.name)
        notify_controller_on_background_complete = True
        if cfg is not None:
            notify_controller_on_background_complete = bool(
                getattr(cfg, "notify_controller_on_background_complete", True)
            )
        self._bg_controller_notify[job_id] = notify_controller_on_background_complete

        sa_task = self.subagent_manager._tasks.get(job_id)
        handle = (
            backgroundify(
                sa_task,
                job_id,
                on_bg_complete=self._on_backgroundify_complete,
                background_init=is_bg,
            )
            if sa_task
            else None
        )

        if handle and handle.promoted:
            # A deliverable background sub-agent defers this turn's output
            # wire until it reports. INTERACTIVE sub-agents (persistent,
            # receive context updates, may never "complete") and notify=False
            # ones do NOT — deferring on them would strand the wire.
            if notify_controller_on_background_complete and not (
                cfg is not None and getattr(cfg, "interactive", False)
            ):
                self._turn_dispatched_bg.add(job_id)
            if sa_tool_call_id:
                controller.conversation.append(
                    "tool",
                    f"[{parse_event.name}] {_BG_PLACEHOLDER}",
                    tool_call_id=sa_tool_call_id,
                    name=parse_event.name,
                )
            else:
                self._bg_dispatch_acks.append(
                    f"[{parse_event.name} → {job_id}] {_BG_PLACEHOLDER}"
                )
        elif handle and handles is not None and handle_order is not None:
            handles[job_id] = handle
            handle_order.append(job_id)
            self._active_handles[job_id] = handle
            self._register_direct_job(
                job_id,
                kind="subagent",
                name=parse_event.name,
                tool_call_id=sa_tool_call_id,
                notify_controller_on_background_complete=notify_controller_on_background_complete,
            )
            if sa_tool_call_id and native_tool_call_ids is not None:
                native_tool_call_ids[job_id] = sa_tool_call_id

        await self._flush_output()
        _, label = _make_job_label(job_id)
        self.output_router.notify_activity(
            "subagent_start",
            f"[{label}] {full_task[:60]}",
            metadata={"job_id": job_id, "task": full_task, "background": is_bg},
        )

    def _check_termination(self, round_text: list[str]) -> bool:
        """Check if termination conditions are met. Returns True to stop.

        Consumes one slot from the shared :class:`IterationBudget` per
        parent turn. When the counter hits zero the
        ``BudgetExhausted`` raised by ``budget.consume`` is translated
        into a termination with reason ``"Iteration budget exhausted"``
        so the outer run-loop exits cleanly.
        """
        if not self._termination_checker:
            budget = getattr(self, "iteration_budget", None)
            if budget is None:
                return False
        else:
            self._termination_checker.record_turn()
            budget = getattr(self, "iteration_budget", None)

        budget_exhausted = False
        if budget is not None:
            try:
                budget.consume(1)
            except BudgetExhausted as exc:
                logger.info(
                    "Agent terminated: iteration budget exhausted",
                    budget_total=budget.total,
                    agent_name=self.config.name,
                )
                if self._termination_checker is not None:
                    self._termination_checker.force_terminate(
                        f"Iteration budget exhausted ({exc})"
                    )
                budget_exhausted = True
                self._running = False
                return True

        if self._termination_checker is None:
            return budget_exhausted

        last_output = "".join(round_text)
        if self._termination_checker.should_terminate(last_output=last_output):
            logger.info(
                "Agent terminated",
                reason=self._termination_checker.reason,
                turns=self._termination_checker.turn_count,
            )
            self._running = False
            return True
        return False

    async def _collect_and_push_feedback(
        self,
        controller: Controller,
        handles: dict[str, BackgroundifyHandle],
        handle_order: list[str],
        native_tool_call_ids: dict[str, str],
        native_mode: bool,
    ) -> bool:
        """Collect tool results via backgroundify handles, push to controller."""
        feedback_parts: list[str] = []

        # Output feedback (tells model what was sent to named outputs)
        output_feedback = self.output_router.get_output_feedback()
        if output_feedback:
            feedback_parts.append(output_feedback)

        # Text-mode background-dispatch acks (native mode already put
        # the placeholder in the conversation as the tool result).
        # Consumed further down ONLY if a feedback round happens anyway
        # — an ack must never force an extra LLM round: if the turn is
        # ending, the model already stopped, which is the ack's goal.
        acks = list(getattr(self, "_bg_dispatch_acks", None) or [])
        self._bg_dispatch_acks = []

        # Wait for handles (direct tools + sub-agents)
        native_results_added = False
        had_promotions = False
        if handles and self._interrupt_requested:
            self._cancel_handles(handles)
            return False
        if handles:
            logger.info("Waiting for %d direct task(s)", len(handles))
            results, had_promotions = await self._wait_handles(
                handles, handle_order, controller, native_tool_call_ids, native_mode
            )
            if results:
                if native_mode and native_tool_call_ids:
                    self._add_native_results_to_conversation(
                        controller, handle_order, results, native_tool_call_ids
                    )
                    native_results_added = True
                else:
                    text = self._format_text_results(handle_order, results)
                    if text:
                        feedback_parts.append(text)

        # A direct job promoted to background mid-wait also defers this
        # turn's output wire (deliverable — promoted directs are ordinary
        # tools/sub-agents that report back). Record it so the guard sees it.
        if had_promotions:
            for jid, promoted_handle in handles.items():
                if getattr(
                    promoted_handle, "promoted", False
                ) and self._bg_controller_notify.get(jid, True):
                    self._turn_dispatched_bg.add(jid)

        # If promotions happened, the controller must continue so the model
        # sees the placeholder and can proceed working on other tasks.
        if had_promotions:
            if native_mode:
                # Placeholder already added to conversation as role="tool"
                native_results_added = True
            else:
                # Text mode: add feedback text about promoted tasks
                feedback_parts.append(
                    "[Tasks promoted to background — results arrive later. "
                    "Continue with other work.]"
                )

        # Drain mid-turn buffered user_input/trigger events AFTER tool
        # results are appended (so the native assistant.tool_calls →
        # role=tool pairing stays intact) and BEFORE the next LLM round
        # so the model sees the new input.
        injected_count = await self._drain_mid_turn_pending_inputs(controller)
        if injected_count:
            native_results_added = True

        # No feedback means we're done. Pending acks alone do NOT
        # continue the loop — the turn ending is exactly the "stop
        # outputting" the ack asks for; the running-jobs context covers
        # the next turn.
        if not feedback_parts and not native_results_added:
            logger.debug("No feedback, exiting process loop")
            return False

        # A feedback round is happening anyway — let the acks ride
        # along so the model knows the dispatch succeeded.
        if acks:
            feedback_parts.extend(acks)

        # Push feedback to controller for next turn
        if native_results_added and not feedback_parts:
            logger.debug("Results/promotions in conversation, continuing")
            await controller.push_event(TriggerEvent(type="tool_complete", content=""))
        elif feedback_parts:
            combined = "\n\n".join(feedback_parts)
            feedback_event = create_tool_complete_event(
                job_id="batch",
                content=combined,
                exit_code=0,
                error=None,
            )
            logger.debug("Pushing feedback to controller, continuing")
            await controller.push_event(feedback_event)

        return True

    async def _finalize_processing(
        self,
        event: TriggerEvent,
        controller: Controller,
        all_round_text: list[str],
    ) -> None:
        """Finalize: flush output, notify processing end."""
        await self._flush_output()

        # Channel-triggered event notification
        trigger_channel = event.context.get("channel") if event.context else None
        trigger_sender = event.context.get("sender") if event.context else None
        if trigger_channel and trigger_sender:
            round_output = "".join(all_round_text).strip()
            if round_output:
                self.output_router.notify_activity(
                    "processing_complete",
                    f"Processed message from {trigger_channel}",
                    metadata={
                        "trigger_channel": trigger_channel,
                        "trigger_sender": trigger_sender,
                        "output_preview": round_output[:500],
                    },
                )

        # Emit usage before processing_end so readers can bind it to this turn.
        accum = getattr(self, "_turn_usage_accum", None)
        if isinstance(accum, dict) and any(accum.values()):
            self.output_router.notify_activity(
                "turn_token_usage",
                (
                    f"turn {self._turn_index}: "
                    f"{accum.get('prompt_tokens', 0)} in, "
                    f"{accum.get('completion_tokens', 0)} out"
                ),
                metadata={
                    "turn_index": self._turn_index,
                    "prompt_tokens": accum.get("prompt_tokens", 0),
                    "completion_tokens": accum.get("completion_tokens", 0),
                    "cached_tokens": accum.get("cached_tokens", 0),
                    "total_tokens": accum.get("total_tokens", 0),
                },
            )

        # Publish ordered reasoning before processing_end so live chat and the
        # trace log can attach it to this turn's assistant message.
        fields = (
            getattr(getattr(controller, "llm", None), "last_assistant_extra_fields", {})
            or {}
        )
        reasoning_metadata = {}
        for key in (
            "reasoning_content",
            "reasoning_summary",
            "reasoning_details",
            "reasoning",
            "_kt_assistant_segments",
        ):
            value = fields.get(key)
            if value not in (None, "", [], {}):
                reasoning_metadata[key] = value
        if reasoning_metadata:
            reasoning_metadata["turn_index"] = self._turn_index
            reasoning_metadata["branch_id"] = self._branch_id
            self.output_router.notify_activity(
                "assistant_reasoning",
                f"turn {self._turn_index} assistant reasoning",
                metadata=reasoning_metadata,
            )

        await self.output_router.emit(OutputEvent(type="processing_end"))
        self.output_router.clear_all()

        if controller.is_ephemeral:
            controller.flush()

        # Check if auto-compact should trigger at turn end
        self._maybe_trigger_compact(controller)

        # Output wiring emission.
        #
        # Runs after the normal turn-end bookkeeping so the resolver (and
        # any receiver plugins) see a consistent post-turn state. The
        # resolver is responsible for never raising back into this path;
        # we still wrap defensively so a buggy resolver can't break the
        # creature's main loop.
        await self._emit_output_wiring(event)

    def _maybe_trigger_compact(self, controller: Controller) -> None:
        """Fire auto-compact if the last LLM call hit the threshold.

        Called both mid-loop (between turns within a single user
        request) and at turn end. The compact manager's single-flight
        dispatch gate ensures later attempts are ignored immediately
        while one compact job is already running.
        """
        if self.compact_manager is None:
            return
        last_usage = getattr(controller, "_last_usage", {}) or {}
        prompt_tokens = last_usage.get("prompt_tokens", 0)
        if self.compact_manager.should_compact(prompt_tokens):
            self.compact_manager.trigger_compact()
