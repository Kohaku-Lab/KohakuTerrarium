"""Turn-end output-wiring emission for the Agent.

Emit configured agent output wiring after a turn completes.

Emission is deferred while the turn owes a deliverable background result.
Membership in ``_turn_dispatched_bg``, rather than current job status, closes a
race where a completed job's queued follow-up still owns the emission.
"""

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentOutputWiringMixin:
    """Emit output wiring while guarding deferred background results."""

    def _has_unfinished_turn_bg_jobs(self) -> bool:
        """Return whether this turn still owes a deferred output emission.

        Set membership remains authoritative after a job stops running because
        its queued completion may still own the follow-up emission. Mid-turn
        folding removes jobs whose results were consumed by the current turn.
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
