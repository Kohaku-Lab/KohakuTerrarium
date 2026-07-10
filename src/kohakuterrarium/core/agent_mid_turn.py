"""Mid-turn user-input injection + interrupt-buffer drain.

Extracted from ``agent_handlers.py`` to keep that file under the 1000-
line hard cap (``tests/unit/test_file_sizes.py``). Same surface — the
four methods are mixed back into ``AgentHandlersMixin`` via
:class:`AgentMidTurnMixin`. The two module-level helpers
(``_to_serializable_content``, ``_coalesce_user_contents``) move with
them since they have no other callers.

Why this cohesive cluster: mid-turn injection is the path where any
event (typed input, trigger, background completion, channel traffic)
arrives while the agent is mid-stream. The agent buffers it, drains
the WHOLE buffer AFTER the in-flight tool calls land (so the native
``tool_calls``/``role=tool`` pairing stays valid), records a session
event per user-facing entry, and notifies the FE so the queued banner
clears. The interrupt-buffer drain is the sibling case: when the user
interrupts mid-turn, the buffered events are re-fired as ONE fresh
turn (first event starts it, the rest drain into it).

All four methods share the same state surface on the host ``Agent``:
``_pending_mid_turn_inputs``, ``_turn_index``, ``_branch_id``,
``_parent_branch_path``, ``output_router``, ``session_store``,
``_processing_lock``, ``_interrupt_requested``, ``_running``,
``controller`` (set lazily by the handlers loop), and ``config``.
"""

import asyncio
from typing import Any

from kohakuterrarium.core.controller import Controller
from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.core.agent_runtime_tools import _make_job_label
from kohakuterrarium.llm.message import content_parts_to_dicts
from kohakuterrarium.utils.logging import get_logger

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
    # as one logical user turn. Entries arrive as dicts (web POST) or
    # typed ContentPart instances (normalize_content_parts) — round-
    # trip through ``content_parts_to_dicts`` so neither shape is
    # silently dropped.
    parts: list[dict] = []
    for idx, c in enumerate(contents):
        if idx > 0:
            parts.append({"type": "text", "text": "\n\n"})
        if isinstance(c, str):
            parts.append({"type": "text", "text": c})
        elif isinstance(c, list):
            parts.extend(p for p in content_parts_to_dicts(c) if isinstance(p, dict))
    return parts


class AgentMidTurnMixin:
    """Mid-turn input drain + interrupt-buffer drain handlers.

    Stateless — every method reads instance attributes from the host
    Agent. See module docstring for the full state surface.
    """

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

        pairs = [(evt, self._resolve_injected_content(evt)) for evt in drained]
        # Filter out anything that resolved to empty (unlikely but
        # defensive — a trigger with no prompt and no fallback would
        # produce ``None``).
        pairs = [(evt, c) for evt, c in pairs if c is not None and c != ""]
        if not pairs:
            return 0

        combined = _coalesce_user_contents([c for _, c in pairs])
        # A single completion arriving while siblings still run reads
        # as "the others failed" without explicit status — attach the
        # live-jobs line so the model neither re-dispatches nor mourns.
        if any(evt.type in ("tool_complete", "subagent_output") for evt, _ in pairs):
            hint = self._background_status_hint()
            if hint:
                if isinstance(combined, str):
                    combined = f"{combined}\n\n{hint}"
                elif isinstance(combined, list):
                    combined.append({"type": "text", "text": f"\n\n{hint}"})
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
        for evt, content in pairs:
            # Only user-facing entries (typed input / fired triggers)
            # get a session record + queued-banner frame. Background
            # completions get a delivery banner instead — recording
            # them as injected input would double-render them as user
            # bubbles on replay.
            if evt.type not in ("user_input", "trigger"):
                if evt.type in ("tool_complete", "subagent_output"):
                    self._notify_background_result(evt)
                continue
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
        """Extract the injectable content string / parts list from a
        buffered TriggerEvent. Non-user types get the same bracketed
        prefixes ``Controller._format_events_for_context`` would give
        them, so a drained event reads identically to one that started
        its own turn."""
        if evt.type == "user_input":
            return evt.content
        if evt.type == "tool_complete":
            prefix = f"[Tool {evt.job_id} completed]"
            if isinstance(evt.content, list):
                # Multimodal result — keep image/file parts instead of
                # flattening to text.
                return [{"type": "text", "text": prefix}] + [
                    p
                    for p in content_parts_to_dicts(evt.content)
                    if isinstance(p, dict)
                ]
            text = evt.get_text_content()
            return f"{prefix}\n{text}" if text else prefix
        if evt.type == "subagent_output":
            prefix = f"[Sub-agent {evt.job_id} output]"
            if isinstance(evt.content, list):
                return [{"type": "text", "text": prefix}] + [
                    p
                    for p in content_parts_to_dicts(evt.content)
                    if isinstance(p, dict)
                ]
            return f"{prefix}\n{evt.get_text_content()}"
        # Fall-back chain — prompt_override → content → a bracketed
        # label keyed on whatever id the event carried.
        if evt.prompt_override:
            return evt.prompt_override
        if evt.content:
            return evt.content
        trigger_id = evt.context.get("trigger_id", "?") if evt.context else "?"
        if evt.type == "trigger":
            return f"[trigger fired: {trigger_id}]"
        return f"[{evt.type} event: {trigger_id}]"

    def _notify_background_result(self, evt: TriggerEvent) -> None:
        """Banner marking the moment a background result reaches the
        controller — the visible cause for the agent speaking again."""
        job_id = getattr(evt, "job_id", "") or ""
        if not job_id:
            return
        kind = (
            "subagent"
            if evt.type == "subagent_output" or job_id.startswith("agent_")
            else "tool"
        )
        _, label = _make_job_label(job_id)
        self.output_router.notify_activity(
            "background_result",
            f"[{label}] background result delivered",
            metadata={
                "job_id": job_id,
                "kind": kind,
                "label": label,
                "turn_index": self._turn_index,
                "branch_id": self._branch_id,
            },
        )

    def _background_status_hint(self) -> str:
        """Status line for still-running background jobs plus a
        don't-duplicate / don't-assume-failed hint; "" when idle."""
        jobs: list[Any] = []
        executor = getattr(self, "executor", None)
        if executor is not None and hasattr(executor, "get_running_jobs"):
            jobs.extend(executor.get_running_jobs())
        manager = getattr(self, "subagent_manager", None)
        if manager is not None and hasattr(manager, "get_running_jobs"):
            jobs.extend(manager.get_running_jobs())
        seen: set[str] = set()
        names: list[str] = []
        for status in jobs:
            job_id = getattr(status, "job_id", None)
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)
            label = getattr(status, "type_name", "") or job_id
            names.append(f"{label} ({job_id})")
        if not names:
            return ""
        return (
            "[background status] Still running: "
            + ", ".join(names)
            + ". Their results arrive automatically in later turns — do "
            "NOT restart or duplicate them, and do NOT treat them as "
            "failed."
        )

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

    def _schedule_leftover_buffer_flush(self) -> None:
        """Kick events left in the buffer by a turn that just ended
        (they arrived after its last mid-turn drain) into a fresh
        batched turn. No-op while another turn holds the lock — that
        turn's own drain owns the buffer."""
        if not (self._pending_mid_turn_inputs and self._running):
            return
        if self._processing_lock.locked():
            return
        asyncio.create_task(self._flush_leftover_buffer())

    async def _flush_leftover_buffer(self) -> None:
        if self._processing_lock.locked() or not (
            self._pending_mid_turn_inputs and self._running
        ):
            return
        event = self._pending_mid_turn_inputs.pop(0)
        try:
            # If a racing turn grabbed the lock first this re-buffers
            # (returns False) and that turn's drain takes over.
            await self._process_event(event)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Leftover buffered event flush failed",
                event_type=event.type,
                error=str(exc),
            )

    async def _flush_buffer_after_interrupt(self) -> None:
        """Re-fire buffered mid-turn events as ONE fresh turn after an
        interrupt cancels the original turn. The first event starts the
        turn through the normal ``_process_event`` path; the rest stay
        in the buffer so that turn's first feedback round drains them
        all as a batch — a single follow-up interrupt clears the whole
        queue, never one interrupt per event.

        Must acquire (and release) the processing lock before draining:
        draining while the cancelled turn still holds it re-buffers each
        event without yielding, starving the lock holder.
        """
        async with self._processing_lock:
            pass
        self._interrupt_requested = False
        if hasattr(self, "controller"):
            self.controller._interrupted = False
        if not (self._pending_mid_turn_inputs and self._running):
            return
        event = self._pending_mid_turn_inputs.pop(0)
        try:
            # A False return means a racing turn grabbed the lock first
            # and the event went back into the buffer — that turn's own
            # drain now owns the whole batch.
            await self._process_event(event)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Buffered event re-process failed after interrupt",
                event_type=event.type,
                error=str(exc),
            )
