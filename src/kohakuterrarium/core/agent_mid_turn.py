"""Mid-turn fold-in of events re-claimed from the event inbox.

Fold queued input and background events into an agent's active turn.

Events are folded only after in-flight tool results arrive, preserving native
``tool_calls`` and ``role=tool`` pairing. User-facing entries retain distinct
session records, while background completions share a delivery banner.
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
    """Drain and fold queued events into an active agent turn."""

    @property
    def has_pending_mid_turn_inputs(self) -> bool:
        """Whether any event is queued on the inbox awaiting a turn.

        This public probe keeps fairness checks independent of the private
        inbox representation."""
        return bool(self._event_inbox)

    def admit_ready_events(self, events: list[TriggerEvent]) -> int:
        """Stash drained trigger events for ordered admission after the primary.

        The stash is flushed synchronously before the primary event yields,
        preserving backlog order for fire-and-forget fold-ins. Returns the
        number of events stashed.
        """
        stash = getattr(self, "_trigger_backlog_stash", None)
        if stash is None or not events:
            return 0
        stash.extend(events)
        return len(events)

    def edit_pending(self, pending_id: str, content: Any) -> bool:
        """Rewrite a queued message before its envelope is claimed.

        Returns ``False`` if the consumer has already claimed it.
        """
        return self._event_inbox.edit(pending_id, content)

    def cancel_pending(self, pending_id: str) -> bool:
        """Drop a queued message, returning ``False`` if already claimed."""
        return self._event_inbox.cancel(pending_id)

    async def _drain_mid_turn_pending_inputs(self, controller: Controller) -> int:
        """Claim every queued event into the active controller turn."""
        claimed = self._event_inbox.drain_all()
        if not claimed:
            return 0

        active_run = getattr(self, "_active_event_run", None)
        if active_run is not None:
            active_run.extend(claimed)
        active_captures = getattr(self, "_active_event_captures", None)
        if active_captures is not None:
            for envelope in claimed:
                capture = envelope.capture
                if capture is None:
                    continue
                active_captures.append(capture)
                self.output_router.add_secondary(capture)

        drained: list[TriggerEvent] = [env.event for env in claimed]

        pairs = [(evt, self._resolve_injected_content(evt)) for evt in drained]
        # Empty events cannot form a meaningful user message.
        pairs = [(evt, c) for evt, c in pairs if c is not None and c != ""]
        # ONE combined delivery banner for every background completion in
        # this re-claim, plus release each one's output-wire defer: it
        # folded into THIS turn, so no follow-up turn re-emits for it and
        # the membership guard must not strand the wire.
        self._emit_batch_background_banner(drained)
        owed = getattr(self, "_turn_dispatched_bg", None)
        if owed is not None:
            for evt in drained:
                if evt.type in ("tool_complete", "subagent_output"):
                    owed.discard(getattr(evt, "job_id", "") or "")
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

        # One session record + one WS frame PER user-facing drained event
        # so the FE can pop the corresponding queued banner and history
        # replay shows each typed message as its own user bubble.
        #
        # Yield after each notify_activity so Textual / other renderers
        # whose output handlers schedule widget mutations via
        # ``call_later`` actually get a render slot between iterations.
        for evt, content in pairs:
            # Only user-facing entries (typed input / fired triggers) get a
            # session record + queued-banner frame; background completions
            # already got the combined delivery banner above.
            if evt.type not in ("user_input", "trigger"):
                continue
            # ``create_user_input_event`` runs ``normalize_content_parts``
            # which converts WS dict lists into typed ``[TextPart, ...]``
            # dataclass instances. Conversation/LLM consumers handle those
            # fine, but the WS sink (``ws.send_json``) and the SQLite event
            # store (msgpack) both need plain JSON-safe dicts — a TextPart
            # raises ``TypeError: not JSON serializable``. Round-trip through
            # ``content_parts_to_dicts`` so both sinks get a safe payload.
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
            "Drained %d mid-turn folded event(s)",
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
