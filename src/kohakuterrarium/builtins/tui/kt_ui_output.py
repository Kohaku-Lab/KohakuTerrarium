"""``kt.ui`` text profile for :class:`TUIOutput`: cards, selection and text modals."""

import asyncio
from typing import Any

from kohakuterrarium.builtins.kt_ui.terminal import TEXT_PROFILE, TerminalUiRenderer
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class TUIKtUiMixin:
    """Draw ``kt.ui`` widgets with the TUI's card blocks and modals."""

    def _init_kt_ui(self) -> None:
        self._kt_ui = TerminalUiRenderer(
            get_service=self._widget_service,
            present=self._present_widget_event,
            retract=self.on_supersede,
            source=self,
        )
        self._kt_ui_tasks: set[asyncio.Task] = set()

    def ui_renderers(self) -> dict[str, int]:
        """The ``kt.ui`` text profile."""
        return dict(TEXT_PROFILE)

    def _widget_service(self) -> Any:
        return getattr(getattr(self, "_router", None), "widgets", None)

    def _present_widget_event(self, event: OutputEvent, reply_router: Any) -> None:
        """Show a ``kt.ui`` card or prompt; its reply goes to ``reply_router``."""
        match event.type:
            case "card":
                self._handle_card_event(event, reply_router)
            case "selection":
                self._schedule(self._handle_selection_event(event, reply_router))
            case "ask_text":
                self._schedule(self._handle_ask_text_event(event, reply_router))

    def _schedule(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._kt_ui_tasks.add(task)
        task.add_done_callback(self._kt_ui_tasks.discard)
