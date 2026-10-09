"""Per-store summary refresher driven by the store's recorded events.

A ``processing_end`` or ``compact_complete`` event schedules one refresh on
the event loop; refreshes for a store run one at a time and coalesce, so a
slow model call never queues a backlog and never blocks a turn.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from kohakuterrarium.studio.identity.session_summary import (
    SummarySettings,
    load_settings,
)
from kohakuterrarium.studio.sessions.summary.writer import (
    COMPACTED,
    TURN,
    summarize,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_TRIGGERS = {"processing_end": TURN, "compact_complete": COMPACTED}


def event_agent(key: str) -> str:
    """The agent namespace of an event key ``<agent>:e<seq>``."""
    return key.rsplit(":e", 1)[0] if ":e" in key else key


class SummaryHook:
    """Keeps one store's ``meta["summary"]`` fresh while the session runs.

    ``llm_for(agent, settings)`` returns the provider for the ``llm`` source,
    or None. ``idle()`` waits for the current refresh chain to finish.
    """

    def __init__(
        self,
        store: Any,
        *,
        loop: asyncio.AbstractEventLoop,
        llm_for: Callable[[str, SummarySettings], Any],
        settings: Callable[[], SummarySettings] = load_settings,
    ) -> None:
        self._store = store
        self._loop = loop
        self._llm_for = llm_for
        self._settings = settings
        self._wanted: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task | None = None
        self._attached = True
        store.subscribe(self._on_event)
        store._companion_closers.append(self.detach)

    @property
    def llm_for(self) -> Callable[[str, SummarySettings], Any]:
        return self._llm_for

    def _on_event(self, key: str, data: dict) -> None:
        reason = _TRIGGERS.get((data or {}).get("type"))
        if reason is None or not self._attached:
            return
        request = {
            "reason": reason,
            "agent": event_agent(key),
            "compaction": str(data.get("summary") or ""),
            "turns": 1 if reason == TURN else 0,
        }
        try:
            self._loop.call_soon_threadsafe(self.request, request)
        except RuntimeError:
            self.detach()

    def request(self, request: dict[str, Any]) -> None:
        """Queue a refresh for one agent, merging into its pending request.

        Merging sums the turns, keeps the latest compaction text, and keeps a
        compaction reason over a turn reason.
        """
        if not self._attached:
            return
        pending = self._wanted.get(request["agent"])
        if pending is not None:
            request = {
                "agent": request["agent"],
                "reason": (
                    request["reason"]
                    if pending["reason"] == TURN
                    else pending["reason"]
                ),
                "compaction": request["compaction"] or pending["compaction"],
                "turns": request["turns"] + pending["turns"],
            }
        self._wanted[request["agent"]] = request
        if self._task is None or self._task.done():
            self._task = self._loop.create_task(self._drain())

    async def _drain(self) -> None:
        while self._wanted and self._attached:
            agent = next(iter(self._wanted))
            request = self._wanted.pop(agent)
            settings = self._settings()
            try:
                await summarize(
                    self._store,
                    settings,
                    llm_factory=lambda: self._llm_for(agent, settings),
                    reason=request["reason"],
                    agent=agent,
                    compaction=request["compaction"],
                    new_turns=request["turns"],
                )
            except Exception as exc:  # noqa: BLE001 - summaries are best-effort
                logger.warning("session summary refresh failed", error=str(exc))

    async def idle(self) -> None:
        while self._task is not None and not self._task.done():
            await asyncio.shield(self._task)

    def detach(self) -> None:
        if not self._attached:
            return
        self._attached = False
        self._wanted.clear()
        try:
            self._store.unsubscribe(self._on_event)
        except Exception:  # noqa: BLE001
            pass
