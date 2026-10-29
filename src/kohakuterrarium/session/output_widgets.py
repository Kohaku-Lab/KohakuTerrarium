"""Record widget events for :class:`SessionOutput`."""

from kohakuterrarium.modules.output.event import OutputEvent


class SessionWidgetsMixin:
    """Persist widget opens, updates, state, messages and closes as session events."""

    async def on_widget(self, event: OutputEvent) -> None:
        if not self._capture_activity:
            return
        payload = event.payload
        match event.type:
            case "widget" if event.update_target is None:
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
            case "widget":
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
                self._record("widget_close", {"widget_id": event.update_target})
