"""Browser sessions the ``browser`` plugin let go of, kept until they have closed."""

import asyncio

from kohakuterrarium.browser.session import BrowserSession
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

SETTLE_MARGIN_S = 1.0


class RetiredSessions:
    """Sessions being closed in the background, and those whose close is still to start.

    ``retire`` starts a session's close on the running loop; without a loop
    the session is kept, and the next ``settle`` starts its close.
    ``settle`` waits for every close in progress, bounded by the longest
    ``close_timeout`` among them plus ``SETTLE_MARGIN_S``.
    """

    def __init__(self) -> None:
        self._closing: dict[asyncio.Task, BrowserSession] = {}
        self._deferred: list[BrowserSession] = []

    def retire(self, session: BrowserSession) -> None:
        """Start closing ``session`` in the background."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "No running event loop: the close of the old browser session was "
                "deferred to the next browser call or unload"
            )
            self._deferred.append(session)
            return
        task = loop.create_task(session.close())
        self._closing[task] = session
        task.add_done_callback(self._closed)

    def _closed(self, task: asyncio.Task) -> None:
        self._closing.pop(task, None)
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "Closing a browser session failed",
                error=type(task.exception()).__name__,
            )

    async def settle(self) -> None:
        """Close every retired session; returns once all have closed or the bound passed."""
        deferred, self._deferred = self._deferred, []
        for session in deferred:
            self.retire(session)
        closing = dict(self._closing)
        if not closing:
            return
        bound = max(s.close_timeout for s in closing.values()) + SETTLE_MARGIN_S
        _, pending = await asyncio.wait(list(closing), timeout=bound)
        if pending:
            logger.warning(
                "A browser session did not finish closing in time", count=len(pending)
            )
