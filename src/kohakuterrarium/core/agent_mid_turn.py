"""Mid-turn user-input injection + interrupt-buffer drain.

Extracted from ``agent_handlers.py`` to keep that file under the 1000-
line hard cap (``tests/unit/test_file_sizes.py``). Same surface — the
four methods are mixed back into ``AgentHandlersMixin`` via
:class:`AgentMidTurnMixin`. The two module-level helpers
(``_to_serializable_content``, ``_coalesce_user_contents``) move with
them since they have no other callers.

Why this cohesive cluster: mid-turn injection is the path where the
user types into a chat while the agent is mid-stream. The agent
buffers the input, drains it AFTER the in-flight tool calls land (so
the native ``tool_calls``/``role=tool`` pairing stays valid), records
a session event per buffered entry, and notifies the FE so the queued
banner clears. The interrupt-buffer drain is the sibling case: when
the user interrupts mid-turn, the buffered events are re-fired as
fresh turns.

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
