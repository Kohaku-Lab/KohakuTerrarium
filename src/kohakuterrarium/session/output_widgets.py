"""Record widget events for :class:`SessionOutput`."""

from kohakuterrarium.modules.output.event import OutputEvent

# Display-only legacy kinds were never part of the session history.
UNRECORDED_RENDERERS = frozenset({"kt.progress", "kt.notification"})
MAX_UNRECORDED = 1024


class SessionWidgetsMixin:
    """Persist widget opens, updates, state, messages, closes and inline-UI state."""

    _unrecorded_widgets: dict[str, None]

    async def on_widget(self, event: OutputEvent) -> None:
        if not self._capture_activity:
            return
        payload = event.payload
        match event.type:
            case "widget" if event.update_target is None:
                if payload.get("renderer") in UNRECORDED_RENDERERS:
                    self._unrecorded_widgets[event.id] = None
                    while len(self._unrecorded_widgets) > MAX_UNRECORDED:
                        self._unrecorded_widgets.pop(
                            next(iter(self._unrecorded_widgets))
                        )
                    return
                self._record(
                    "widget",
                    {
                        "widget_id": event.id,
                        "renderer": payload.get("renderer"),
                        "version": payload.get("version", 1),
                        "surface": event.surface,
                        "interactive": bool(event.interactive),
                        "title": payload.get("title", ""),
                        "spec": payload.get("spec"),
                        "state": dict(payload.get("state") or {}),
                        "fallback": payload.get("fallback", ""),
                        "routing": payload.get("routing", "agent"),
                    },
                )
            case "widget" if set(payload) == {"awaiting"}:
                return
            case "widget" if event.update_target not in self._unrecorded_widgets:
                self._record(
                    "widget_update",
                    {"widget_id": event.update_target, "patch": dict(payload)},
                )
            case "widget_state":
                self._record(
                    "widget_state",
                    {
                        "widget_id": event.update_target,
                        "state": dict(payload.get("state") or {}),
                    },
                )
            case "widget_message":
                self._record(
                    "widget_message",
                    {
                        "widget_id": event.update_target,
                        "name": payload.get("name"),
                        "data": payload.get("data"),
                        "origin": payload.get("origin"),
                        "routed_to": payload.get("routed_to"),
                    },
                )
            case "widget_close":
                self.on_widget_close(event)
            case "inline_ui_state":
                self._record("inline_ui_state", dict(payload))

    def on_widget_close(self, event: OutputEvent) -> None:
        """Record a close, with the reason a legacy interaction ended."""
        if not self._capture_activity:
            return
        widget_id = event.update_target
        if widget_id in self._unrecorded_widgets:
            self._unrecorded_widgets.pop(widget_id, None)
            return
        self._record("widget_close", {"widget_id": widget_id, **dict(event.payload)})
