"""Per-creature output sink that demultiplexes into ``RichCLIApp``.

In a multi-creature terrarium, every ``Creature.agent.output_router``
needs a sink so that creature's output reaches the rich CLI. We mount
one :class:`MultiplexedRichOutput` per creature; each instance is
bound to its ``creature_id`` at construction.

The sink doesn't do its own rendering — it stamps the event with the
``creature_id`` and hands it to a single ``handler`` callable owned by
:class:`RichCLIApp`. The app then routes the event to the matching
:class:`~kohakuterrarium.builtins.cli_rich.live_state.LiveRegionState`
(text → ``append_text``, everything → ``record_event``) and triggers a
repaint if the targeted creature is currently focused.

This decoupling — sink does demultiplex, app does state mutation and
render — keeps both pieces independently testable. Phase A tests
exercise this module without booting a prompt_toolkit Application.
"""

import asyncio
from typing import Any, Awaitable, Callable

from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


EventHandler = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class MultiplexedRichOutput(BaseOutputModule):
    """Forward output events with their originating creature identifier."""

    def __init__(
        self,
        handler: EventHandler,
        creature_id: str,
        *,
        creature_name: str = "",
    ) -> None:
        super().__init__()
        self.handler = handler
        self.creature_id = creature_id
        self.creature_name = creature_name or creature_id
        try:
            self._owner_loop: asyncio.AbstractEventLoop | None = (
                asyncio.get_running_loop()
            )
        except RuntimeError:
            self._owner_loop = None

    async def _on_start(self) -> None:
        self._owner_loop = asyncio.get_running_loop()

    async def _dispatch(self, kind: str, payload: dict[str, Any]) -> None:
        self._owner_loop = asyncio.get_running_loop()
        try:
            await self.handler(self.creature_id, kind, payload)
        except Exception as e:  # pragma: no cover - defensive
            logger.exception(
                "multiplexed sink handler raised",
                creature_id=self.creature_id,
                kind=kind,
                error=str(e),
            )

    async def write(self, content: str) -> None:
        if content:
            await self._dispatch("text", {"text": content})

    async def write_stream(self, chunk: str) -> None:
        if chunk:
            await self._dispatch("text", {"text": chunk})

    async def flush(self) -> None:
        await self._dispatch("flush", {})

    async def on_processing_start(self) -> None:
        await self._dispatch("processing_start", {})

    async def on_processing_end(self) -> None:
        await self._dispatch("processing_end", {})

    async def on_user_input(self, text: str) -> None:
        # Composer submissions are already rendered by the app.
        return

    def on_activity(self, activity_type: str, detail: str) -> None:
        self.on_activity_with_metadata(activity_type, detail, {})

    def on_activity_with_metadata(
        self, activity_type: str, detail: str, metadata: dict[str, Any]
    ) -> None:
        # Synchronous callbacks may arrive from worker threads, where Python 3.12
        # deliberately provides no implicit event loop. Schedule construction of
        # the coroutine back on the loop which owns this sink.
        loop = self._owner_loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
                self._owner_loop = loop
            except RuntimeError:
                return
        if loop.is_closed():
            return
        payload = {
            "activity_type": activity_type,
            "detail": detail,
            "metadata": dict(metadata) if metadata else {},
        }

        def _schedule_dispatch() -> None:
            asyncio.create_task(self._dispatch("activity", payload))

        try:
            loop.call_soon_threadsafe(_schedule_dispatch)
        except RuntimeError:
            return

    async def emit(self, event: OutputEvent) -> None:
        await self._dispatch(
            "emit",
            {"event": event},
        )


__all__ = ["MultiplexedRichOutput", "EventHandler"]
