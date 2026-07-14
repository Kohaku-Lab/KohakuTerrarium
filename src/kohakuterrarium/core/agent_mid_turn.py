"""Mid-turn fold-in of events re-claimed from the event inbox.

Extracted from ``agent_handlers.py`` to keep that file under the 1000-
line hard cap (``tests/unit/test_file_sizes.py``). Mixed into ``Agent``
via :class:`AgentMidTurnMixin`. The two module-level helpers
(``_to_serializable_content``, ``_coalesce_user_contents``) move with
them since they have no other callers.

Why this cohesive cluster: mid-turn fold-in is the path where any
fire-and-forget event (typed input, trigger, background completion,
channel traffic) that arrived WHILE the agent is mid-stream gets
re-claimed from the inbox (``drain_foldable``) and folded into the
running turn AFTER the in-flight tool calls land — so the native
``tool_calls`` / ``role=tool`` pairing stays valid. Each user-facing
entry gets a session record + queued-banner-clear frame; every
background completion shares ONE combined delivery banner.

Shared state surface on the host ``Agent``: ``_event_inbox``,
``_trigger_backlog_stash``, ``_turn_index``, ``_branch_id``,
``_parent_branch_path``, ``output_router``, ``session_store``,
``_running``, ``controller`` (set lazily by the handlers loop), and
``config``. The combined delivery banner
(``_emit_batch_background_banner``) lives in
:mod:`core.agent_event_loop`.
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
    """Mid-turn input drain + interrupt-buffer drain handlers.

    Stateless — every method reads instance attributes from the host
    Agent. See module docstring for the full state surface.
    """

    @property
    def has_pending_mid_turn_inputs(self) -> bool:
        """Whether any event is queued on the inbox awaiting a turn.

        Public read-only probe the Terrarium Drive fairness check reads
        rather than the private inbox, so a rename breaks loudly here
        instead of silently degrading its probe."""
        return bool(self._event_inbox)

    def admit_ready_events(self, events: list[TriggerEvent]) -> int:
        """Stash a trigger's drained backlog so the immediately-following
        primary ``_process_event`` enqueues it right after the primary, in
        order, as fire-and-forget fold-ins (UXI-08b). Wired onto
        ``trigger_manager.admit_ready``; flushed synchronously by
        ``_flush_trigger_backlog_stash`` before the primary awaits. Returns
        the count stashed."""
        stash = getattr(self, "_trigger_backlog_stash", None)
        if stash is None or not events:
            return 0
        stash.extend(events)
        return len(events)

    def edit_pending(self, pending_id: str, content: Any) -> bool:
        """Rewrite a still-queued message's content by id (UXI-08a).

        Wins iff it commits before the consumer claims the envelope; a
        plain ``False`` "already sent" no-op otherwise."""
        return self._event_inbox.edit(pending_id, content)

    def cancel_pending(self, pending_id: str) -> bool:
        """Drop a still-queued message before it is sent (UXI-08a).
        ``False`` when it was already claimed by the consumer."""
        return self._event_inbox.cancel(pending_id)

    async def _drain_mid_turn_pending_inputs(self, controller: Controller) -> int:
        """Re-claim fire-and-forget stackable events that arrived DURING
        this turn and fold them in. Called from
        ``_collect_and_push_feedback`` AFTER tool results land so the
        native ``tool_calls`` → ``role=tool`` pairing stays valid before a
        fresh ``role=user`` slot. Drained events concatenate into ONE
        combined ``role=user`` message; each user-facing entry still
        produces its own session record + ``user_input_injected`` frame,
        and every background completion shares ONE combined delivery
        banner. Returns count drained.

        ``drain_foldable`` leaves any non-stackable or awaiting
        (future-bearing) envelope in the inbox so it keeps its own turn."""
        claimed = self._event_inbox.drain_foldable()
        if not claimed:
            return 0
        drained: list[TriggerEvent] = [env.event for env in claimed]

        pairs = [(evt, self._resolve_injected_content(evt)) for evt in drained]
        # Filter out anything that resolved to empty (unlikely but
        # defensive — a trigger with no prompt and no fallback would
        # produce ``None``).
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
