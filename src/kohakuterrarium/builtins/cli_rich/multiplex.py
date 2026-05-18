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


# Handler signature: ``async def handler(creature_id, event_kind, payload) -> None``.
# ``event_kind`` is one of ``"emit"`` (full OutputEvent), ``"text"`` (raw
# streamed chunk), ``"processing_start"`` / ``"processing_end"`` (turn
# lifecycle), or ``"activity"`` (legacy callback shape).
EventHandler = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class MultiplexedRichOutput(BaseOutputModule):
    """``OutputModule`` that stamps every event with ``creature_id``.

    Mounted once per creature by ``run_engine_with_rich_cli`` to
    replace each creature's ``output_router.default_output``. Forwards
    to a single ``handler`` (typically ``RichCLIApp._handle_creature_event``)
    that owns all the per-creature state.
    """

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

    async def _dispatch(self, kind: str, payload: dict[str, Any]) -> None:
        try:
            await self.handler(self.creature_id, kind, payload)
        except Exception as e:  # pragma: no cover - defensive
            logger.exception(
                "multiplexed sink handler raised",
                creature_id=self.creature_id,
                kind=kind,
                error=str(e),
            )

    # ── Stream + lifecycle ─────────────────────────────────────────

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
        # CLI composer prints user input itself; preserve the
        # single-creature no-op behavior from RichCLIOutput.
        return

    # ── Legacy activity callbacks (sync) ───────────────────────────

    def on_activity(self, activity_type: str, detail: str) -> None:
        self.on_activity_with_metadata(activity_type, detail, {})

    def on_activity_with_metadata(
        self, activity_type: str, detail: str, metadata: dict[str, Any]
    ) -> None:
        try:
            self.handler  # for type-check + early failure
        except AttributeError:
            return
        # Fire-and-forget: the async handler runs on the loop the
        # router started us with. Use run_coroutine_threadsafe so a
        # sync callback from a worker thread doesn't block.
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        coro = self._dispatch(
            "activity",
            {
                "activity_type": activity_type,
                "detail": detail,
                "metadata": dict(metadata) if metadata else {},
            },
        )
        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            # Loop not running — drop silently; the router teardown
            # path covers final flushes.
            return

    # ── Typed event consumer (preferred path) ──────────────────────

    async def emit(self, event: OutputEvent) -> None:
        # Forward the full event so the app can inspect type / payload.
        await self._dispatch(
            "emit",
            {"event": event},
        )


__all__ = ["MultiplexedRichOutput", "EventHandler"]
