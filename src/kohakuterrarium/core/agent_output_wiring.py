"""Turn-end output-wiring emission for the Agent.

Extracted from ``agent_handlers.py`` to keep it under the 1000-line
hard cap. Mixed back into :class:`AgentHandlersMixin` via
:class:`AgentOutputWiringMixin`; ``_finalize_processing`` calls
:meth:`_emit_output_wiring`.

The busy-guard here is the load-bearing bit (UXI-10): a creature that
promoted or backgrounded THIS turn's deliverable work is NOT finished,
so firing its output wire now would read to the creator as the child
"randomly turning off." The guard defers on MEMBERSHIP of
``_turn_dispatched_bg`` (jobs promoted/backgrounded this turn that
report back), not on whether they are still running — a job that
completes in the window between the final handle-wait and finalize is
no longer running yet its queued completion still owns the emit, so a
running-only check would fire a duplicate. An unrelated persistent /
interactive / fire-and-forget job is never added to the set, so it
never strands the wire; a completion folded back into this turn is
discarded from the set by ``_drain_mid_turn_pending_inputs``.
"""

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentOutputWiringMixin:
    """Output-wiring emission + the this-turn background-job guard."""

    def _has_unfinished_turn_bg_jobs(self) -> bool:
        """Whether this turn still OWES a deferred output-wire emit.

        True while ``_turn_dispatched_bg`` holds any deliverable
        background job this turn dispatched. Every such job is notify=True
        and non-stateful/non-interactive, so its completion is guaranteed
        to drive a follow-up turn that re-emits the real result — unless
        the completion is folded back into THIS turn's own feedback round,
        in which case ``_drain_mid_turn_pending_inputs`` discards it from
        the set. Deferring on set MEMBERSHIP (not on "still running") is
        what closes the double-emit race: a job that completes between the
        turn's final handle-wait and finalize is no longer running, yet
        its queued completion still owns the emit — so this turn stays
        silent rather than fire a second, duplicate delivery.
        """
        return bool(getattr(self, "_turn_dispatched_bg", None))

    async def _emit_output_wiring(self, trigger_event: TriggerEvent) -> None:
        """Emit a ``creature_output`` event for each configured wiring entry.

        Called at the end of ``_finalize_processing``. No-op when the
        creature has no wiring configured or no resolver is attached
        (standalone mode). Skips while this turn still owes a deliverable
        background completion; the completion of that work drives a
        follow-up turn whose finalization re-emits with the real result.
        """
        entries = getattr(self.config, "output_wiring", None) or []
        resolver = getattr(self, "_wiring_resolver", None)
        if not entries or resolver is None:
            return

        if self._has_unfinished_turn_bg_jobs():
            logger.debug(
                "Output wiring deferred — this turn still owes a "
                "deliverable background completion",
                source=self.config.name,
            )
            return

        content = "".join(self._last_turn_text).strip()
        # ``_turn_index`` is bumped at user-input arrival inside
        # ``_process_event``; output wiring just reads the current value.
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
