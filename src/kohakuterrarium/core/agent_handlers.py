"""Agent event handling, tool dispatch, and result collection."""

import asyncio
import importlib
from typing import Any

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
from kohakuterrarium.core.events import (
    EventType,
    TriggerEvent,
    create_tool_complete_event,
)
from kohakuterrarium.core.metrics_hook import metrics
from kohakuterrarium.llm.message import content_parts_to_dicts
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.parsing import (
    CommandResultEvent,
    SubAgentCallEvent,
    TextEvent,
    ToolCallEvent,
)
from kohakuterrarium.skills.hints import inject_skill_path_hint
from kohakuterrarium.utils.logging import get_logger

_BG_PLACEHOLDER = (
    "Running in background — task delegated. "
    "Do NOT do this same task yourself — it is already being done. "
    "Do NOT use bash echo/sleep to wait — just end your response. "
    "Work on a DIFFERENT task or STOP your response now. "
    "Result arrives automatically in the next turn."
)

logger = get_logger(__name__)


def _to_serializable_content(content: Any) -> Any:
    """Convert a user_input content payload into a JSON-serializable
    form for the WS sink + SQLite event store.

    ``normalize_content_parts`` (called by ``create_user_input_event``)
    turns list-of-dict WS input into typed ``[TextPart, ImagePart, ...]``
    dataclass instances. Those are fine for in-memory conversation /
    LLM consumption but break ``ws.send_json`` (TypeError) and msgpack
    serialization. Strings pass through; lists of ContentPart get
    routed through ``content_parts_to_dicts``; anything else (already
    a list of dicts, a plain string, etc.) passes through unchanged.
    """
    if content is None or isinstance(content, str):
        return content
    if isinstance(content, list):
        return content_parts_to_dicts(content)
    return content


def _coalesce_user_contents(contents: list[Any]) -> Any:
    """Concatenate N user-input contents into one ``role=user`` message
    body suitable for ``Conversation.append``.

    Plain-text-only lists join with a blank line between entries so
    the LLM sees separate messages without ambiguous run-together.
    Mixed-modal lists (any entry that's a content-parts list) build
    a single content-parts array with text separators between entries.
    A single entry passes through verbatim so the common case stays
    cheap.
    """
    if len(contents) == 1:
        return contents[0]
    if all(isinstance(c, str) for c in contents):
        return "\n\n".join(c for c in contents if c)
    # Mixed-modal — flatten into one content-parts list with text
    # separators between entries so a downstream provider sees them
    # as one logical user turn.
    parts: list[dict] = []
    for idx, c in enumerate(contents):
        if idx > 0:
            parts.append({"type": "text", "text": "\n\n"})
        if isinstance(c, str):
            parts.append({"type": "text", "text": c})
        elif isinstance(c, list):
            parts.extend(p for p in c if isinstance(p, dict))
    return parts


class AgentHandlersMixin(AgentToolsMixin):
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
        """Process event using the primary controller.

        Uses a lock to prevent concurrent processing. When multiple
        triggers fire simultaneously, events are serialized so only
        one LLM call runs at a time.

        The lock covers the ENTIRE event-handling pipeline — including
        pre-controller side effects like ``output_router.on_user_input``,
        plugin ``on_event`` notifications, and ``session_store`` event
        appends. If those ran concurrently (two trigger tasks calling
        ``_process_event`` at once) the TUI would see overlapping
        renders and the session log would interleave events from two
        turns. Holding the lock around them serializes everything.

        **Opportunistic mid-turn buffering (Feat 3)**: ``user_input``
        and ``trigger`` events that arrive while the lock is held by
        another turn DON'T block on the lock. They're appended to
        ``_pending_mid_turn_inputs`` and drained from inside the
        current turn's ``_collect_and_push_feedback`` after tool
        results land. This keeps user follow-ups (typed while the
        agent is busy) and live triggers (timer fired mid-turn)
        visible to the LLM on the next round inside the same turn,
        instead of waiting for the current turn to fully end.

        Rerun events (regenerate / edit-and-rerun) BYPASS the buffer
        — they must run against the original lock-held turn because
        the agent's branch-id was pre-incremented for them.

        Returns ``True`` when the call actually ran the event (lock
        acquired, turn processed) and ``False`` when it was buffered.
        The WS attach path (``studio/attach/io.py:_process_input``)
        uses this to suppress the ``idle`` frame on buffered events —
        otherwise ``idle`` fires immediately, the FE clears the
        ``processingByTab`` flag, and KohakUwUing blinks off until
        the next chunk arrives (Bug 1).
        """
        is_rerun = bool(event.context.get("rerun")) if event.context else False
        if (
            self._running
            and not is_rerun
            and event.type in ("user_input", "trigger")
            and self._processing_lock.locked()
        ):
            buffer = getattr(self, "_pending_mid_turn_inputs", None)
            if buffer is not None:
                buffer.append(event)
                logger.info(
                    "Event buffered for mid-turn injection",
                    event_type=event.type,
                    pending=len(buffer),
                )
                return False
        async with self._processing_lock:
            if not self._running:
                logger.debug("Dropping event, agent stopped", event_type=event.type)
                return True

            is_rerun = bool(event.context.get("rerun"))
            is_edited = bool(event.context.get("edited"))
            is_pure_rerun = is_rerun and not is_edited
            # Turn / branch bookkeeping for v2 session events.
            # - New user input → bump turn_index, reset branch_id to 1.
            # - Pure regen / edit+rerun keep turn_index, ``_branch_id`` was
            #   pre-incremented by ``regenerate_last_response`` /
            #   ``edit_and_rerun`` before this trigger fired.
            if event.type == "user_input" and not is_rerun:
                # The previous turn's (turn_index, branch_id) becomes part
                # of the new turn's parent_branch_path so a future branch
                # switch on that earlier turn can hide this turn's events
                # if they don't belong to the chosen subtree.
                if self._turn_index > 0 and self._branch_id > 0:
                    self._parent_branch_path = list(self._parent_branch_path)
                    self._parent_branch_path.append((self._turn_index, self._branch_id))
                self._turn_index += 1
                # Collision-safe branch allocation. After the user has
                # switched onto a non-latest branch and submits a fresh
                # input, the event log may already carry events at this
                # turn_index (the prior subtree's children). Resetting
                # branch_id to 1 would collide on (turn, branch); the
                # replay resolver in ``session/history.py`` dedupes
                # turns by (turn, branch) and keeps the first occurrence
                # — the orphaned events — which it then filters out as
                # path-incompatible, dropping the whole new turn. Bump
                # past anything existing to keep (turn, branch) globally
                # unique. ``_max_branch_id_for_turn`` comes from the
                # ``AgentMessagesMixin``; in narrow unit tests that mock
                # ``Agent`` without the mixin we fall back to 1.
                helper = getattr(self, "_max_branch_id_for_turn", None)
                existing_max = helper(self._turn_index) if helper else 0
                self._branch_id = existing_max + 1 if existing_max > 0 else 1
            # Record user input to session store — fresh inputs only.
            # Both pure regen and edit+rerun ride a TriggerEvent with
            # ``rerun=True``; their event-log writes are owned by the
            # ``AgentMessagesMixin`` callers
            # (``regenerate_last_response``/``edit_and_rerun``) which
            # have the correct branch_id + parent_branch_path already
            # computed. Letting this block also run would DOUBLE-append
            # the user_input/user_message events at the new branch — a
            # subtle bug that bloated ``_live_user_turns`` with
            # duplicates and shifted every subsequent edit's
            # ``_resolve_edit_message_index`` to the wrong turn (matched
            # the "edit drops huge swathes of context" symptom).
            if (
                self.session_store is not None
                and event.type == "user_input"
                and not is_rerun
            ):
                content = (
                    content_parts_to_dicts(event.content)
                    if hasattr(event, "is_multimodal") and event.is_multimodal()
                    else (event.content or "")
                )
                ppath = [tuple(p) for p in self._parent_branch_path]
                self.session_store.append_event(
                    self.config.name,
                    "user_input",
                    {"content": content},
                    turn_index=self._turn_index,
                    branch_id=self._branch_id,
                    parent_branch_path=ppath,
                )
                self.session_store.append_event(
                    self.config.name,
                    "user_message",
                    {"content": content},
                    turn_index=self._turn_index,
                    branch_id=self._branch_id,
                    parent_branch_path=ppath,
                )

            # Notify output of user input (for inline panel rendering).
            # Pure regen has no new user input — skip the notification so
            # output modules don't render an empty user bubble.
            if (
                event.type == "user_input"
                and self.output_router is not None
                and not is_pure_rerun
            ):
                content = (
                    event.get_text_content()
                    if hasattr(event, "is_multimodal") and event.is_multimodal()
                    else (event.content or "")
                )
                await self.output_router.emit(
                    OutputEvent(type="user_input", content=content)
                )

            if self.plugins is not None:
                await self.plugins.notify("on_event", event=event)
            # Procedural-skill ``paths:`` auto-activate (D.6 + Qd).
            if event.type == "user_input":
                inject_skill_path_hint(self)

            await self._process_event_with_controller(event, self.controller)
            return True

    # ------------------------------------------------------------------
    # Main processing loop (split into phases)
    # ------------------------------------------------------------------

    async def _process_event_with_controller(
        self, event: TriggerEvent, controller: Controller
    ) -> None:
        """Process event through controller. Cancellable via interrupt()."""
        self._prepare_processing_cycle(event, controller)
        await controller.push_event(event)
        await self.output_router.emit(OutputEvent(type="processing_start"))

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
            logger.error(
                "Processing error",
                error_type=error_type,
                error=error_msg,
            )
            # Emit as structured error activity (TUI/frontend render distinctively)
            self.output_router.notify_activity(
                "processing_error",
                f"[{error_type}] {error_msg}",
                metadata={
                    "error_type": error_type,
                    "error": error_msg,
                },
            )
            metrics.observe_error("controller")
        finally:
            self._processing_task = None
        await self._finalize_processing(event, controller, all_round_text)

    def _prepare_processing_cycle(
        self, event: TriggerEvent, controller: Controller
    ) -> None:
        """Reset state at the start of a new processing cycle."""
        self._interrupt_requested = False
        controller._interrupted = False
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
        # pre_tool_dispatch plugin chain (cluster B.2) — may rewrite or veto.
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
            # Already background — add placeholder
            if tool_call_id:
                controller.conversation.append(
                    "tool",
                    f"[{parse_event.name}] {_BG_PLACEHOLDER}",
                    tool_call_id=tool_call_id,
                    name=parse_event.name,
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
            if sa_tool_call_id:
                controller.conversation.append(
                    "tool",
                    f"[{parse_event.name}] {_BG_PLACEHOLDER}",
                    tool_call_id=sa_tool_call_id,
                    name=parse_event.name,
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
        parent turn (cluster 6.1). When the counter hits zero the
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

        # Feat 3 — drain mid-turn buffered user_input/trigger events
        # AFTER tool results are appended (so the native
        # assistant.tool_calls → role=tool pairing stays intact) and
        # BEFORE the next LLM round so the model sees the new input.
        injected_count = await self._drain_mid_turn_pending_inputs(controller)
        if injected_count:
            native_results_added = True

        # No feedback means we're done
        if not feedback_parts and not native_results_added:
            logger.debug("No feedback, exiting process loop")
            return False

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

    async def _drain_mid_turn_pending_inputs(self, controller: Controller) -> int:
        """Drain ``Agent._pending_mid_turn_inputs`` into the CURRENT
        turn. Called from ``_collect_and_push_feedback`` AFTER tool
        results land so the native ``tool_calls`` → ``role=tool``
        pairing stays valid before a fresh ``role=user`` slot. Buffered
        events get concatenated into ONE combined ``role=user`` message
        for provider alternation; each still produces its own session
        record + ``user_input_injected`` frame so the FE clears
        entry-by-entry. Returns count drained."""
        buffer = getattr(self, "_pending_mid_turn_inputs", None)
        if not buffer:
            return 0
        drained: list[TriggerEvent] = list(buffer)
        buffer.clear()

        contents = [self._resolve_injected_content(evt) for evt in drained]
        # Filter out anything that resolved to empty (unlikely but
        # defensive — a trigger with no prompt and no fallback would
        # produce ``None``).
        contents = [c for c in contents if c is not None and c != ""]
        if not contents:
            return 0

        combined = _coalesce_user_contents(contents)
        try:
            controller.conversation.append("user", combined)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Mid-turn input injection failed", error=str(exc), exc_info=True
            )
            return 0

        # One session record + one WS frame PER drained event so the
        # FE can pop the corresponding queued banner and history
        # replay shows each typed message as its own user bubble.
        #
        # Yield after each notify_activity so Textual / other renderers
        # whose output handlers schedule widget mutations via
        # ``call_later`` actually get a render slot between iterations.
        # Without this, a multi-entry drain fires N synchronous
        # dispatch calls in a tight loop without yielding to the event
        # loop — Textual's call_later queue backs up and the TUI render
        # loop starves, freezing input. (TUI freeze investigation, 3
        # parallel agent reports, ./temp/tui-freeze-investigation.md)
        for content in contents:
            # ``create_user_input_event`` runs ``normalize_content_parts``
            # which converts WS dict lists into typed ``[TextPart, ...]``
            # dataclass instances. Conversation/LLM consumers handle
            # those fine, but the WS sink (``ws.send_json``) and the
            # SQLite event store (msgpack) both need plain JSON-safe
            # dicts — passing TextPart raises ``TypeError: Object of
            # type TextPart is not JSON serializable``, which used to
            # kill ``_forward_queue`` silently at DEBUG-level (8-hour
            # debugging session, 2026-05-28). Round-trip through
            # ``content_parts_to_dicts`` so both sinks get safe payload.
            serializable_content = _to_serializable_content(content)
            self._record_injected_input_event(serializable_content)
            self.output_router.notify_activity(
                "user_input_injected",
                "",
                metadata={
                    "content": serializable_content,
                    "turn_index": self._turn_index,
                    "branch_id": self._branch_id,
                },
            )
            await asyncio.sleep(0)
        logger.info(
            "Drained %d mid-turn buffered event(s)",
            len(drained),
            turn_index=self._turn_index,
        )
        return len(drained)

    def _resolve_injected_content(self, evt: TriggerEvent) -> Any:
        """Extract the user-facing content string / parts list from a
        buffered TriggerEvent. Triggers carry their prompt in
        ``prompt_override`` or fall back to a synthesised label so
        the LLM understands what fired."""
        if evt.type == "user_input":
            return evt.content
        # Trigger fall-back chain — prompt_override → content → a
        # bracketed label keyed on whatever id the trigger carried.
        if evt.prompt_override:
            return evt.prompt_override
        if evt.content:
            return evt.content
        trigger_id = evt.context.get("trigger_id", "?") if evt.context else "?"
        return f"[trigger fired: {trigger_id}]"

    def _record_injected_input_event(self, content: Any) -> None:
        """Append a ``user_input_injected`` event at the current
        ``(turn_index, branch_id)``. Distinct from ``user_input`` so
        the FE replay's ``(turn, branch)`` dedupe doesn't drop it —
        mid-turn injections share ids with the turn-starter and would
        otherwise collide."""
        store = getattr(self, "session_store", None)
        if store is None:
            return
        try:
            store.append_event(
                self.config.name,
                "user_input_injected",
                {"content": content},
                turn_index=self._turn_index,
                branch_id=self._branch_id,
                parent_branch_path=[
                    tuple(p) for p in getattr(self, "_parent_branch_path", [])
                ],
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Mid-turn input session record failed",
                error=str(exc),
                exc_info=True,
            )

    async def _flush_buffer_after_interrupt(self) -> None:
        """Re-fire buffered mid-turn events as fresh turns after an
        interrupt cancels the original turn (Bug 3). Each goes through
        the normal ``_process_event`` path so the FE sees a real new
        turn rather than a phantom injection.

        Yields briefly first so the cancellation + lock release
        propagates; then resets the interrupt flag so the new turns
        can actually run instead of being short-circuited by the
        leftover ``_interrupt_requested`` state.
        """
        for _ in range(5):
            if not self._processing_lock.locked():
                break
            await asyncio.sleep(0.005)
        self._interrupt_requested = False
        if hasattr(self, "controller"):
            self.controller._interrupted = False
        while self._pending_mid_turn_inputs and self._running:
            event = self._pending_mid_turn_inputs.pop(0)
            try:
                await self._process_event(event)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Buffered event re-process failed after interrupt",
                    event_type=event.type,
                    error=str(exc),
                )
                break

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

        # Flush per-turn token aggregate as a Wave B event before the
        # ``processing_end`` marker so session readers can pin the turn
        # rollup to this turn_index.
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

    async def _emit_output_wiring(self, trigger_event: TriggerEvent) -> None:
        """Emit a ``creature_output`` event for each configured wiring entry.

        Called at the end of ``_finalize_processing``. No-op when the
        creature has no wiring configured or no resolver is attached
        (standalone mode).
        """
        entries = getattr(self.config, "output_wiring", None) or []
        resolver = getattr(self, "_wiring_resolver", None)
        if not entries or resolver is None:
            return

        content = "".join(self._last_turn_text).strip()
        # ``_turn_index`` is now bumped at user-input arrival inside
        # ``_process_event``; output wiring just reads the current
        # value rather than bumping again here.
        try:
            await resolver.emit(
                source=getattr(self, "_creature_id", self.config.name),
                content=content,
                source_event_type=trigger_event.type,
                turn_index=self._turn_index,
                entries=entries,
            )
        except Exception as exc:
            logger.warning(
                "Output wiring resolver raised - dropping emission",
                source=self.config.name,
                error=str(exc),
                exc_info=True,
            )
