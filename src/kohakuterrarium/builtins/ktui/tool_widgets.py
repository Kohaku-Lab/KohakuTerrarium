"""kt.ui widgets that tools open (``widget`` / ``widget_state`` / ``widget_close`` events).

ktui defines no ``on_widget``, so cards, questions and selections keep arriving as their
legacy events (``interactions``); what comes here is a widget with ``renderer: kt.ui``. It is
drawn with the same mapper as inline blocks (``ui_block.BlockView`` with a ``WidgetLink``):
buttons send named widget messages and state goes to the widget service, never to the user's
chat. An open widget lives in the live region, so it never holds back scrollback; when the tool
closes it, it freezes and moves into the transcript where it was.
"""

from typing import TYPE_CHECKING, Any

from kohakutui.ui import Node, Stack, Text

from kohakuterrarium.builtins.kt_ui.spec import SpecError, validate_spec
from kohakuterrarium.builtins.ktui.ui_block import BlockView, UiContext, WidgetLink
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakutui import App

logger = get_logger(__name__)

RENDERER = "kt.ui"
KINDS = frozenset({"widget", "widget_state", "widget_close"})


class _Open:
    def __init__(self, holder: Node, block: BlockView, view: Any = None):
        self.holder = holder
        self.block = block
        # the creature view whose tool opened it: the final widget joins its document
        self.view = view


class ToolWidgets:
    """The open tool widgets of every attached creature."""

    def __init__(self, app: "App"):
        self.app = app
        self.open: dict[str, _Open] = {}

    def handles(self, event: OutputEvent) -> bool:
        if event.type not in KINDS:
            return False
        if event.type == "widget" and event.update_target is None:
            return (event.payload or {}).get("renderer") == RENDERER
        return (event.update_target or event.id) in self.open

    def handle(
        self, event: OutputEvent, router: Any, ctx: UiContext | None, source: Any
    ) -> None:
        """``source``: the output that got the event (its own state is not echoed back)."""
        payload = event.payload or {}
        target = event.update_target or event.id or ""
        with self.app.batch():
            match event.type:
                case "widget" if event.update_target is None:
                    self._open(event, payload, router, ctx, source)
                case "widget":
                    self._update(target, payload)
                case "widget_state" if isinstance(payload.get("state"), dict):
                    self.open[target].block.merge_state(payload["state"])
                case "widget_close":
                    self._close(target)

    def _open(
        self,
        event: OutputEvent,
        payload: dict,
        router: Any,
        ctx: UiContext | None,
        source: Any,
    ) -> None:
        widget_id = event.id or ""
        spec = payload.get("spec")
        # one title: the tool's (it can change it), else the spec's own
        fallback = spec.get("title") if isinstance(spec, dict) else None
        title = str(payload.get("title") or fallback or "") or None
        holder = self.app.live.add(Stack(title=title))
        service = getattr(router, "widgets", None)
        interactive = bool(event.interactive) and service is not None
        link = WidgetLink(widget_id, service, interactive, source)
        block = BlockView(holder, ctx, None, link)
        try:
            block.show(validate_spec(spec), dict(payload.get("state") or {}))
        except SpecError as error:
            holder.add(Text(f"widget could not be shown: {error}", tone="danger"))
        self.open[widget_id] = _Open(holder, block, getattr(source, "view", None))

    def _update(self, widget_id: str, patch: dict) -> None:
        shown = self.open[widget_id]
        if "title" in patch:
            shown.holder.update(title=str(patch.get("title") or "") or None)
        if "spec" in patch:
            try:
                validated = validate_spec(patch["spec"])
            except SpecError as error:
                logger.debug("kt.ui widget update not drawable", error=str(error))
                return
            state = {**shown.block.state, **(patch.get("state") or {})}
            shown.block.show(validated, state)
        elif isinstance(patch.get("state"), dict):
            shown.block.merge_state(patch["state"])

    def _close(self, widget_id: str) -> None:
        shown = self.open.pop(widget_id, None)
        if shown is None:
            return
        shown.block.freeze()
        # the final widget joins the conversation where the tool left it
        doc = getattr(shown.view, "doc", None) or self.app.transcript
        shown.holder.move(doc)
        shown.holder.seal()

    def close_all(self) -> None:
        for widget_id in list(self.open):
            self._close(widget_id)


__all__ = ["RENDERER", "ToolWidgets"]
