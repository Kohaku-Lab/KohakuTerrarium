"""Approvals and questions from every creature, shown one at a time and never trapping.

An interactive event (``confirm``, ``ask_text``, ``selection``, a replyable ``card``) opens as
an overlay. Esc or "Later" only hides it: the agent keeps waiting, the item stays in the
active-items row, and alt+j (or the row) shows it again unchanged. ctrl+k picks the approving
option of the oldest one without opening it. Answers go back to the waiting router as a
``UIReply``; a supersede or the event's own timeout removes the item.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kohakutui import App
from kohakutui.ui import Card, Picker, Prompt

from kohakuterrarium.builtins.ktui.format import one_line
from kohakuterrarium.modules.output.event import OutputEvent, UIReply
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.view import CreatureView

logger = get_logger(__name__)

LATER = "__later"
APPROVING = ("allow_once", "approve", "allow", "yes", "confirm", "ok", "submit")


@dataclass
class Pending:
    """One unanswered interactive event."""

    event: OutputEvent
    router: Any
    view: "CreatureView"
    created: float = field(default_factory=time.monotonic)

    @property
    def id(self) -> str:
        return str(self.event.id or "")

    @property
    def title(self) -> str:
        payload = self.event.payload or {}
        return one_line(
            payload.get("prompt") or payload.get("title") or self.event.type, 70
        )

    def options(self) -> list[dict]:
        payload = self.event.payload or {}
        match self.event.type:
            case "confirm":
                return list(payload.get("options") or []) or [
                    {"id": "confirm", "label": "Yes", "style": "primary"},
                    {"id": "cancel", "label": "No", "style": "danger"},
                ]
            case "card":
                return [
                    a for a in payload.get("actions") or [] if a.get("style") != "link"
                ]
            case _:
                return []

    def remaining(self) -> float | None:
        timeout = self.event.timeout_s
        return (
            None
            if not timeout
            else max(0.0, timeout - (time.monotonic() - self.created))
        )


class Interactions:
    """The queue of pending interactive events and the one overlay showing its head."""

    def __init__(self, app: App, on_change: Callable[[], None]):
        self.app = app
        self.on_change = on_change
        self.items: list[Pending] = []
        self._shown: tuple[Pending, asyncio.Task] | None = None
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._tasks: set[asyncio.Task] = set()

    @property
    def count(self) -> int:
        return len(self.items)

    def add(self, event: OutputEvent, router: Any, view: "CreatureView") -> None:
        if not event.id or any(p.id == event.id for p in self.items):
            return
        pending = Pending(event, router, view)
        self.items.append(pending)
        if event.timeout_s:
            loop = asyncio.get_running_loop()
            self._timers[pending.id] = loop.call_later(
                event.timeout_s, self.supersede, pending.id, "timed out"
            )
        self.app.bell()
        self.on_change()
        if self._shown is None:
            self.show(pending)

    def show(self, pending: Pending | None = None) -> None:
        """Opens ``pending`` (default: the oldest) unless another one is open."""
        pending = pending or (self.items[0] if self.items else None)
        if pending is None or self._shown is not None:
            return
        # hide() cancels only the dialog: once it has answered, the cancel is a no-op and
        # the answer still reaches _ask
        dialog = asyncio.ensure_future(self.app.run_dialog_async(self._spec(pending)))
        self._shown = (pending, dialog)
        task = asyncio.create_task(self._ask(pending, dialog))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def show_next(self) -> None:
        """alt+j: the item after the one on screen, or the oldest."""
        if not self.items:
            self.app.notify("nothing is waiting for you", "info")
            return
        current = self._shown[0] if self._shown else None
        self.hide()
        index = (
            (self.items.index(current) + 1) % len(self.items)
            if current in self.items
            else 0
        )
        self.show(self.items[index])

    def hide(self) -> None:
        if self._shown is not None:
            _, dialog = self._shown
            self._shown = None
            dialog.cancel()

    def approve_oldest(self) -> None:
        """ctrl+k: answers the oldest item with its approving option."""
        for pending in self.items:
            option = _approving(pending.options())
            if option is not None:
                self.app.notify(f"approved · {pending.title}", "success")
                self._reply(pending, option, {"action_id": option})
                return
        self.app.notify("no approval to answer with ctrl+k", "info")

    def supersede(
        self, event_id: str | None, reason: str = "answered elsewhere"
    ) -> None:
        pending = next((p for p in self.items if p.id == event_id), None)
        if pending is None:
            return
        self._drop(pending)
        pending.view.render("notice", f"{pending.title} · {reason}", "muted", None)

    def _drop(self, pending: Pending) -> None:
        if pending in self.items:
            self.items.remove(pending)
        timer = self._timers.pop(pending.id, None)
        if timer is not None:
            timer.cancel()
        if self._shown is not None and self._shown[0] is pending:
            self.hide()
            # the one on screen went away: the next waiting item takes its place
            if self.items:
                self.show()
        self.on_change()

    def _reply(self, pending: Pending, action_id: str, values: dict) -> None:
        self._drop(pending)
        router = pending.router
        if router is None:
            logger.warning("ktui reply without a router", event_id=pending.id)
            return
        reply = UIReply(event_id=pending.id, action_id=action_id, values=values)
        try:
            submit = getattr(router, "submit_reply_with_status", None)
            if callable(submit):
                accepted, status = submit(reply)
            else:
                accepted, status = bool(router.submit_reply(reply)), "unknown"
            if not accepted:
                self.app.notify(f"{pending.title} · too late ({status})", "warning")
        except Exception as exc:
            logger.warning(
                "ktui reply failed", event_id=pending.id, error=str(exc), exc_info=True
            )

    async def _ask(self, pending: Pending, dialog: asyncio.Future) -> None:
        try:
            answer = await dialog
        except asyncio.CancelledError:
            return
        if self._shown is not None and self._shown[0] is pending:
            self._shown = None
        if answer is None or answer == LATER:
            if self.app.running and pending in self.items:
                self.app.notify("hidden · alt+j or ↓ brings it back", "info")
            return

        match pending.event.type:
            case "ask_text":
                self._reply(pending, "submit", {"text": str(answer)})
            case "selection":
                # like the web and kt-cli: a list for multi-select, one id otherwise
                selected = (
                    [str(a) for a in answer]
                    if isinstance(answer, list)
                    else str(answer)
                )
                self._reply(pending, "submit", {"selected": selected})
            case _:
                self._reply(pending, str(answer), {"action_id": str(answer)})
        if self.items:
            self.show()

    def _spec(self, pending: Pending) -> Any:
        payload = pending.event.payload or {}
        source = f"{pending.view.name} · " if pending.view.name else ""
        remaining = pending.remaining()
        expiry = f" · {int(remaining)}s left" if remaining else ""
        title = f"{payload.get('prompt') or payload.get('title') or 'Question'}"
        match pending.event.type:
            case "ask_text":
                return Prompt(
                    dock="input",
                    question=f"{title}\n{source}esc hides it for later{expiry}",
                    placeholder=payload.get("placeholder") or None,
                    multiline=bool(payload.get("multiline")) or None,
                    value=payload.get("default") or None,
                )
            case "selection":
                multi = bool(payload.get("multi"))
                default = payload.get("default")
                ticked = (
                    {str(d) for d in default} if isinstance(default, list) else set()
                )
                items = []
                for o in payload.get("options") or []:
                    value = str(o.get("id", o.get("label", "")))
                    items.append(
                        {
                            "label": str(o.get("label", o.get("id", ""))),
                            "value": value,
                            "detail": o.get("description"),
                            "checked": (value in ticked) if multi else None,
                            "current": (value == str(default)) if not multi else None,
                        }
                    )
                return Picker(
                    dock="input",
                    items=items,
                    multi=multi or None,
                    title=f"{title}  ({source}esc hides{expiry})",
                )

        detail = str(payload.get("detail") or payload.get("body") or "")
        body = (
            f"```\n{detail}\n```"
            if pending.event.type == "confirm" and detail
            else detail
        )
        actions = [
            {"id": str(o.get("id")), "label": str(o.get("label", o.get("id")))}
            for o in pending.options()
        ]
        actions.append({"id": LATER, "label": "Later"})
        footer = f"\n\n*{source}esc or Later keeps it waiting{expiry}*"
        return Card(title=title, body=body + footer, actions=actions, dock="input")


def _approving(options: list[dict]) -> str | None:
    ids = [str(o.get("id", "")) for o in options]
    for preferred in APPROVING:
        if preferred in ids:
            return preferred
    primary = [str(o.get("id")) for o in options if o.get("style") == "primary"]
    return primary[0] if primary else None
