"""Drive-overlay live-refresh wiring for :class:`RichCLIApp`.

Extracted from ``cli_rich/app.py`` to keep that file under the hard 1000-line
cap. Mixed back into ``RichCLIApp`` via :class:`AppDriveMixin`; it assumes the
host provides ``self.engine`` / ``self.drive_overlay`` / ``self.focus_controller``
and the module-level ``spawn`` scheduler.
"""

import asyncio

from kohakuterrarium.builtins.cli_rich.runtime import spawn
from kohakuterrarium.terrarium.events import EventFilter, EventKind
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_DRIVE_EVENT_KINDS = frozenset(
    {
        EventKind.DRIVE_CREATED,
        EventKind.DRIVE_UPDATED,
        EventKind.DRIVE_ASSIGNED,
        EventKind.DRIVE_UNASSIGNED,
        EventKind.DRIVE_STATUS_CHANGED,
        EventKind.DRIVE_DELIVERY_ADMITTED,
        EventKind.DRIVE_DELIVERY_ACKNOWLEDGED,
        EventKind.DRIVE_DELIVERY_RETRYING,
        EventKind.DRIVE_DELIVERY_DEAD_LETTERED,
        EventKind.DRIVE_ORPHANED,
        EventKind.DRIVE_RETIRED,
    }
)


class AppDriveMixin:
    """Manage the Drive-event subscription for live overlay refreshes."""

    def _drive_focus_id(self) -> str:
        """Return the creature used for the overlay's assigned-to-me scope."""
        return getattr(self.focus_controller, "focus_id", "") or ""

    def _start_drive_watch(self) -> None:
        """Subscribe to Drive engine events so the open overlay live-refreshes."""
        if self.engine is None:
            return
        if self._drive_watch_task is not None and not self._drive_watch_task.done():
            return
        self._drive_watch_task = spawn(self._watch_drive_events())

    def _stop_drive_watch(self) -> None:
        task = self._drive_watch_task
        self._drive_watch_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _watch_drive_events(self) -> None:
        if self.engine is None:
            return
        filt = EventFilter(kinds=set(_DRIVE_EVENT_KINDS))
        try:
            async for _ev in self.engine.subscribe(filt):
                if not self.drive_overlay.visible:
                    break
                self.drive_overlay.note_event()
        except asyncio.CancelledError:
            return
        except Exception as e:  # pragma: no cover - defensive event watcher
            logger.warning("drive event watch crashed", error=str(e), exc_info=True)


__all__ = ["AppDriveMixin"]
