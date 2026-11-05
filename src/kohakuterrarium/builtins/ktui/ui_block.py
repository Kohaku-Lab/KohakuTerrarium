"""One inline ``<ui>`` block: validated, drawn, interactive while live, then frozen (D26).

A block is drawn through a ``ui_slot.Slot``, so every redraw updates its nodes in place. It
takes input once it is closed and until it freezes: controls write its state (re-evaluating
``let`` values and redrawing), buttons send a message or set state. State is restored from and
saved to the agent's ``InlineUiStates`` (``inline_ui_state`` events), keyed by the reply's
turn and branch and the block id (source hash + occurrence), as the web client does.

Inline, the newest interactive block stays live at the bottom with a hint; it freezes (drawn
with its final values, static) when the next transcript entry comes, and every block freezes
when the view switches to inline. In full screen nothing freezes on a new turn.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kohakutui import Event
from kohakutui.ui import Node, Stack, Text

from kohakuterrarium.builtins.kt_ui.inline_state import block_hash
from kohakuterrarium.builtins.kt_ui.markup import MarkupError, convert_block
from kohakuterrarium.builtins.kt_ui.spec import (
    SpecError,
    ValidatedSpec,
    build_scope,
    validate_spec,
)
from kohakuterrarium.builtins.ktui.ui_map import mount
from kohakuterrarium.builtins.ktui.ui_slot import Slot
from kohakuterrarium.modules.output.widgets.service import WidgetMessage
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

SAVE_DELAY = 0.4
HINT_INLINE = "tab to use · ctrl+t full screen"
HINT_FULL = "tab to use"


class UiHost:
    """Routes the events of block controls (one per shell) to the block that drew them."""

    def __init__(self) -> None:
        self._handlers: dict[int, Callable[[Event], None]] = {}

    def register(self, node: Node, handler: Callable[[Event], None]) -> None:
        self._handlers[node.id] = handler

    def drop(self, ids: list[int]) -> None:
        for node_id in ids:
            self._handlers.pop(node_id, None)

    def handle(self, ev: Event) -> bool:
        handler = self._handlers.get(ev.id) if ev.id is not None else None
        if handler is None or ev.kind not in ("change", "select", "submit"):
            return False
        handler(ev)
        return True


@dataclass
class UiContext:
    """What a creature's blocks need from the shell."""

    host: UiHost
    send: Callable[[str], None]
    inline: Callable[[], bool]
    turn: Callable[[], tuple[int, int] | None]
    states: Any = None  # the agent's InlineUiStates, when it has one
    copy: Callable[[str], None] | None = None


class BlockUi:
    """What ``ui_controls`` handlers use to read and write one block."""

    def __init__(self, block: "BlockView"):
        self.block = block
        self.live = block.live
        # a tool's widget: buttons send named widget messages, not user messages
        self.widget = block.link is not None

    def value(self, name: Any) -> Any:
        return self.block.state.get(name) if isinstance(name, str) else None

    def bind(self, node: Node, name: str) -> None:
        self.block.listen(node, lambda ev: self.block.change(name, ev))

    def on_select(self, node: Node, press: Callable[[], None]) -> None:
        def handler(ev: Event) -> None:
            if ev.kind == "select":
                press()

        self.block.listen(node, handler)

    def set_state(self, changes: dict[str, Any]) -> None:
        self.block.set_state(changes)

    def send(self, text: str) -> None:
        self.block.send(text)

    def message(self, name: str, data: dict[str, Any]) -> None:
        self.block.message(name, data)

    def copy(self, text: str) -> None:
        if self.block.ctx is not None and self.block.ctx.copy is not None:
            self.block.ctx.copy(text)

    def remembered(self, key: str, default: Any) -> Any:
        """View state that is not the block's own (the open tab), kept across redraws."""
        return self.block.view_state.get(key, default)

    def remember_on_change(self, node: Node, key: str, redraw: bool = False) -> None:
        def handler(ev: Event) -> None:
            if ev.kind == "change":
                self.block.view_state[key] = ev.value
                if redraw:
                    self.block.draw()

        self.block.listen(node, handler)

    def mount(self, slot: Slot, node: Any, scope: dict[str, Any]) -> None:
        mount(slot, node, scope)


@dataclass
class WidgetLink:
    """A tool's kt.ui widget: where its messages and state go."""

    widget_id: str
    service: Any
    interactive: bool = True
    source: Any = None  # our output: the service does not echo our own state back to us


class BlockView:
    """One ``<ui>`` segment of a reply, or (with ``link``) a widget a tool opened."""

    def __init__(
        self,
        holder: Node,
        ctx: UiContext | None,
        turn: tuple[int, int] | None,
        link: WidgetLink | None = None,
    ):
        self.holder = holder
        self.ctx = ctx
        self.turn = turn
        self.link = link
        self.content = holder.add(Stack())
        self.hint: Node | None = None
        self.slot: Slot | None = None
        self.source = ""
        self.closed = False
        self.lines = -1
        self.validated: ValidatedSpec | None = None
        self.state: dict[str, Any] = {}
        self.view_state: dict[str, Any] = {}
        self.block_id: str | None = None
        self.frozen = False
        self.live = False
        self._listening: list[int] = []
        self._save: asyncio.TimerHandle | None = None

    # -- streaming --------------------------------------------------------------------------

    def update(self, source: str, closed: bool, occurrence: int) -> None:
        if source == self.source and closed == self.closed:
            return
        self.source, self.closed = source, closed
        lines = source.count("\n")
        if not closed and lines == self.lines:
            return  # redraw once per completed line while streaming
        self.lines = lines
        try:
            self.validated = validate_spec(convert_block(source))
        except (MarkupError, SpecError) as error:
            self.validated = None
            self._draw_text(
                (
                    f"UI block could not be shown: {error}"
                    if closed
                    else "… building UI …"
                ),
                "danger" if closed else "muted",
            )
            return
        self.state = dict(self.validated.spec.get("state") or {})
        if closed:
            self.block_id = f"{block_hash(source)}.{occurrence}"
            self.state.update(self._restored())
        self.draw()

    def show(self, validated: ValidatedSpec, state: dict[str, Any]) -> None:
        """A widget's spec (already validated) with its state."""
        self.validated, self.closed = validated, True
        self.state = {**(validated.spec.get("state") or {}), **state}
        self.draw()

    def merge_state(self, state: dict[str, Any]) -> None:
        """State set elsewhere (the tool, another client)."""
        self.state.update(state)
        self.draw()

    # -- drawing ----------------------------------------------------------------------------

    def draw(self) -> None:
        if self.validated is None:
            return
        self._drop_listeners()
        self.live = self.closed and not self.frozen and self.ctx is not None
        if self.link is not None and not self.link.interactive:
            self.live = False
        ui = BlockUi(self)
        previous = self.slot.drawn if self.slot is not None else None
        self.slot = Slot(self.content, ui, previous)
        title = self.validated.spec.get("title")
        if self.link is not None:  # a tool's widget: its holder carries the one title
            title = None
        inner = self.slot.add(Stack(title=str(title)) if title else Stack())
        scope = build_scope(self.validated, self.state)
        mount(inner, self.validated.spec.get("root"), scope)
        if not self.closed:
            self.slot.add(Text("… building UI …", tone="muted", italic=True))
        self.slot.finish()
        self._hint()

    def _draw_text(self, text: str, tone: str) -> None:
        self._drop_listeners()
        previous = self.slot.drawn if self.slot is not None else None
        self.slot = Slot(self.content, BlockUi(self), previous)
        self.slot.add(Text(text, tone=tone, italic=tone == "muted"))
        self.slot.finish()

    def _hint(self) -> None:
        wanted = self.live and bool(self._listening)
        if not wanted:
            if self.hint is not None:
                self.hint.remove()
                self.hint = None
            return
        text = HINT_INLINE if self.ctx.inline() else HINT_FULL
        if self.hint is None:
            self.hint = self.holder.add(Text(text, tone="faint"))
        else:
            self.hint.update(value=text)

    def refresh_hint(self) -> None:
        self._hint()

    @property
    def interactive(self) -> bool:
        return self.live and bool(self._listening)

    def freeze(self) -> None:
        """Static from now on, drawn with the final values."""
        if self.frozen:
            return
        self.frozen = True
        self.draw()

    # -- input ------------------------------------------------------------------------------

    def listen(self, node: Node, handler: Callable[[Event], None]) -> None:
        if self.ctx is None:
            return
        self.ctx.host.register(node, handler)
        self._listening.append(node.id)

    def _drop_listeners(self) -> None:
        if self.ctx is not None:
            self.ctx.host.drop(self._listening)
        self._listening = []

    def change(self, name: str, ev: Event) -> None:
        if ev.kind == "change":
            self.set_state({name: ev.value})

    def set_state(self, changes: dict[str, Any]) -> None:
        if not self.live:
            return
        self.state.update(changes)
        self.draw()
        self._schedule_save()

    def send(self, text: str) -> None:
        if self.live and text.strip() and self.ctx is not None:
            self.ctx.send(text)

    def message(self, name: str, data: dict[str, Any]) -> None:
        if not (self.live and self.link is not None):
            return
        msg = WidgetMessage(self.link.widget_id, name, data, origin="renderer")
        try:
            status = self.link.service.submit_message(msg)
        except RuntimeError as exc:
            logger.warning("kt.ui widget message not sent", error=str(exc))
            return
        logger.debug("kt.ui widget message", widget=self.link.widget_id, status=status)

    # -- persistence ------------------------------------------------------------------------

    def _restored(self) -> dict[str, Any]:
        states = self.ctx.states if self.ctx is not None else None
        if states is None or self.turn is None or self.block_id is None:
            return {}
        try:
            stored = states.get(self.turn[0], self.turn[1], self.block_id) or {}
        except Exception as exc:  # restoring is a nicety, never a failure
            logger.debug("inline ui state restore failed", error=str(exc))
            return {}
        declared = self.validated.spec.get("state") or {}
        return {k: v for k, v in stored.items() if k in declared}

    def _schedule_save(self) -> None:
        if self.link is not None:  # a widget's state lives in the widget service
            try:
                self.link.service.submit_state(
                    self.link.widget_id, dict(self.state), source=self.link.source
                )
            except RuntimeError as exc:
                logger.warning("kt.ui widget state not saved", error=str(exc))
            return
        states = self.ctx.states if self.ctx is not None else None
        if states is None or self.turn is None or self.block_id is None:
            return
        if self._save is not None:
            self._save.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._save_now()
            return
        self._save = loop.call_later(SAVE_DELAY, self._save_now)

    def _save_now(self) -> None:
        self._save = None
        declared = self.validated.spec.get("state") or {} if self.validated else {}
        state = {k: v for k, v in self.state.items() if k in declared}
        try:
            result = self.ctx.states.submit(
                self.turn[0], self.turn[1], self.block_id, state
            )
        except Exception as exc:
            logger.debug("inline ui state save failed", error=str(exc))
            return
        if result != "accepted":
            logger.debug(
                "inline ui state not saved", result=result, block=self.block_id
            )


__all__ = ["BlockUi", "BlockView", "UiContext", "UiHost", "WidgetLink"]
