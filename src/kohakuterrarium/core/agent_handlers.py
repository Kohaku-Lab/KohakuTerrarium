"""Agent event handling, tool dispatch, and result collection."""

import asyncio
import importlib

from kohakuterrarium.core.agent_tools import (
    AgentToolsMixin,
    _TurnResult,
    _make_job_label,
)
from kohakuterrarium.core.backgroundify import BackgroundifyHandle, backgroundify

_BG_PLACEHOLDER = (
    "Running in background — task delegated. "
    "Do NOT do this same task yourself — it is already being done. "
    "Do NOT use bash echo/sleep to wait — just end your response. "
    "Work on a DIFFERENT task or STOP your response now. "
    "Result arrives automatically in the next turn."
)
from kohakuterrarium.core.controller import Controller
from kohakuterrarium.core.events import (
    EventType,
    TriggerEvent,
    create_tool_complete_event,
)
from kohakuterrarium.parsing import (
    CommandResultEvent,
    SubAgentCallEvent,
    TextEvent,
    ToolCallEvent,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


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

    async def _process_event(self, event: TriggerEvent) -> None:
        """Process event using the primary controller.

        Uses a lock to prevent concurrent processing. When multiple
        triggers fire simultaneously, events are serialized so only
        one LLM call runs at a time.
        """
        # Record user input to session store
        if self.session_store is not None and event.type == "user_input":
            content = (
                event.get_text_content()
                if hasattr(event, "is_multimodal") and event.is_multimodal()
                else (event.content or "")
            )
            self.session_store.append_event(
                self.config.name, "user_input", {"content": content}
            )

        # Notify output of user input (for inline panel rendering)
        if event.type == "user_input" and self.output_router is not None:
            content = (
                event.get_text_content()
                if hasattr(event, "is_multimodal") and event.is_multimodal()
                else (event.content or "")
            )
            await self.output_router.on_user_input(content)

        if self.plugins is not None:
            await self.plugins.notify("on_event", event=event)
        async with self._processing_lock:
            if not self._running:
                logger.debug("Dropping event, agent stopped", event_type=event.type)
                return
            await self._process_event_with_controller(event, self.controller)

    # ------------------------------------------------------------------
    # Main processing loop (split into phases)
    # ------------------------------------------------------------------

    async def _process_event_with_controller(
        self, event: TriggerEvent, controller: Controller
    ) -> None:
        """Process event through controller. Cancellable via interrupt()."""
        self._prepare_processing_cycle(event, controller)
        await controller.push_event(event)
        await self.output_router.on_processing_start()

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
        tool_call_id = parse_event.args.pop("_tool_call_id", None)
        run_bg = parse_event.args.pop("run_in_background", False)

        job_id, task, is_direct = await self._start_tool_async(parse_event)

        # Three-level decision for execution mode
        if not is_direct:
            pass  # Tool declared BACKGROUND, respect it
        elif run_bg:
            is_direct = False

        # Wrap in backgroundify handle
        handle = backgroundify(
            task,
            job_id,
            on_bg_complete=self._on_backgroundify_complete,
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
        sa_tool_call_id = parse_event.args.pop("_tool_call_id", None)
        full_task = parse_event.args.get("task", "")
        job_id, is_bg = await self._start_subagent_async(parse_event)

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
        """Check if termination conditions are met. Returns True to stop."""
        if not self._termination_checker:
            return False
        self._termination_checker.record_turn()
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

        await self.output_router.on_processing_end()
        self.output_router.clear_all()

        if controller.is_ephemeral:
            controller.flush()

        # Check if auto-compact should trigger
        if self.compact_manager is not None:
            last_usage = getattr(controller, "_last_usage", {})
            prompt_tokens = last_usage.get("prompt_tokens", 0)
            if self.compact_manager.should_compact(prompt_tokens):
                self.compact_manager.trigger_compact()

