"""Widget delivery and renderer-capability aggregation for :class:`OutputRouter`."""

from collections.abc import Callable, Mapping
from typing import Any

from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

RenderersListener = Callable[[dict[str, int], dict[str, int]], None]


def output_renderers(output: Any) -> Mapping[str, int]:
    """Return the renderer map one output reports, or an empty map."""
    method = getattr(output, "ui_renderers", None)
    if method is None:
        return {}
    try:
        result = method()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("ui_renderers raised", error=str(e), exc_info=True)
        return {}
    return result if isinstance(result, Mapping) else {}


class OutputRouterWidgetsMixin:
    """Deliver widget events and track which renderers attached outputs support."""

    def _init_widgets(self) -> None:
        self._renderers_listeners: list[RenderersListener] = []
        self._renderers_snapshot: dict[str, int] = {}

    def _widget_targets(self) -> list[Any]:
        return [self.default_output, *self._secondary_outputs]

    def ui_renderers(self) -> dict[str, int]:
        """Union of every attached output's renderers, with the highest version each."""
        merged: dict[str, int] = {}
        for output in self._widget_targets():
            for renderer, version in output_renderers(output).items():
                if not isinstance(renderer, str) or not isinstance(version, int):
                    continue
                if version > merged.get(renderer, 0):
                    merged[renderer] = version
        return merged

    def add_renderers_listener(self, callback: RenderersListener) -> None:
        """Call ``callback(old, new)`` whenever the renderer union changes."""
        if callback not in self._renderers_listeners:
            self._renderers_listeners.append(callback)

    def remove_renderers_listener(self, callback: RenderersListener) -> None:
        if callback in self._renderers_listeners:
            self._renderers_listeners.remove(callback)

    def refresh_renderers(self) -> None:
        """Recompute the renderer union and notify listeners only on a real change."""
        new = self.ui_renderers()
        old = self._renderers_snapshot
        if new == old:
            return
        self._renderers_snapshot = new
        for callback in list(self._renderers_listeners):
            try:
                callback(dict(old), dict(new))
            except Exception as e:
                logger.warning("renderers listener raised", error=str(e), exc_info=True)

    async def deliver_legacy(self, legacy: OutputEvent, widget: OutputEvent) -> None:
        """Deliver a legacy-kind event: ``widget`` to outputs with ``on_widget``,
        the original ``legacy`` event to the others."""
        for target in self._widget_targets():
            try:
                handler = getattr(target, "on_widget", None)
                if handler is not None:
                    await handler(widget)
                else:
                    await target.emit(legacy)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "output emit failed",
                    output=type(target).__name__,
                    event_type=legacy.type,
                    error=str(e),
                    exc_info=True,
                )

    async def emit_widget(self, event: OutputEvent, *, exclude: Any = None) -> None:
        """Deliver a widget-family event to every output except ``exclude``.

        Outputs with ``on_widget`` get the event; the others get the
        widget's fallback text as an activity when a widget opens.
        """
        for target in self._widget_targets():
            if target is exclude:
                continue
            try:
                handler = getattr(target, "on_widget", None)
                if handler is not None:
                    await handler(event)
                elif (
                    event.type == "widget"
                    and event.update_target is None
                    and event.payload.get("fallback")
                ):
                    target.on_activity("widget", str(event.payload["fallback"]))
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "widget delivery failed",
                    output=type(target).__name__,
                    event_type=event.type,
                    error=str(e),
                    exc_info=True,
                )
