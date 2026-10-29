"""Long-lived, addressable, two-way UI objects on the output bus.

A widget is opened by a tool or plugin through :class:`WidgetService`, drawn
by a renderer the attached outputs support, updated at any time, and sends
messages back. A message resolves a waiting consumer, or reaches the agent as
a ``widget_event`` trigger, or is dropped.
"""

import asyncio
import json
import secrets
import time
import weakref
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.errors import RendererUnavailable
from kohakuterrarium.modules.output.event import OutputEvent, Surface
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

MAX_MESSAGE_BYTES = 16 * 1024
MAX_STATE_BYTES = 64 * 1024
MESSAGE_BURST = 10
MESSAGE_WINDOW_S = 10.0

_clock = time.monotonic

Origin = Literal["renderer", "app"]
Routing = Literal["agent", "drop"]

WIDGET_EVENT_TYPES = frozenset(
    {"widget", "widget_close", "widget_state", "widget_message"}
)


@dataclass(frozen=True)
class WidgetMessage:
    """One message a rendered widget sends back."""

    widget_id: str
    name: str
    data: dict[str, Any] = field(default_factory=dict)
    origin: Origin = "renderer"
    user: str | None = None
    ts: float = 0.0


@dataclass
class WidgetRecord:
    """What the backend knows about one widget."""

    widget_id: str
    renderer: str
    version: int = 1
    title: str = ""
    surface: Surface = "chat"
    routing: Routing = "agent"
    interactive: bool = True
    state: dict[str, Any] = field(default_factory=dict)
    closed: bool = False
    recent: deque = field(default_factory=deque)
    consumers: list["_Consumer"] = field(default_factory=list)


@dataclass
class _Consumer:
    """A waiter or a message stream registered on one widget."""

    names: frozenset[str] | None
    queue: asyncio.Queue
    once: bool

    def accepts(self, name: str) -> bool:
        return self.names is None or name in self.names


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return MAX_STATE_BYTES + 1


class WidgetRegistry:
    """Per-creature map of widget id to :class:`WidgetRecord`."""

    def __init__(self) -> None:
        self._records: dict[str, WidgetRecord] = {}

    def get(self, widget_id: str) -> WidgetRecord | None:
        return self._records.get(widget_id)

    def add(self, record: WidgetRecord) -> None:
        self._records[record.widget_id] = record

    def __contains__(self, widget_id: object) -> bool:
        return widget_id in self._records

    def __len__(self) -> int:
        return len(self._records)

    def restore(self, events: list[dict]) -> int:
        """Rebuild records from recorded session events; return how many were added.

        Ids already present are left as they are.
        """
        restored: dict[str, WidgetRecord] = {}
        for evt in events:
            wid = evt.get("widget_id")
            if not isinstance(wid, str) or (wid in self._records):
                continue
            match evt.get("type"):
                case "widget":
                    restored[wid] = WidgetRecord(
                        widget_id=wid,
                        renderer=str(evt.get("renderer") or ""),
                        version=int(evt.get("version") or 1),
                        title=str(evt.get("title") or ""),
                        surface=evt.get("surface") or "chat",
                        routing=evt.get("routing") or "agent",
                        interactive=bool(evt.get("interactive", True)),
                        state=dict(evt.get("state") or {}),
                    )
                case "widget_update" if wid in restored:
                    patch = evt.get("patch") or {}
                    if isinstance(patch.get("state"), dict):
                        restored[wid].state.update(patch["state"])
                    if patch.get("title"):
                        restored[wid].title = str(patch["title"])
                case "widget_state" if wid in restored:
                    if isinstance(evt.get("state"), dict):
                        restored[wid].state = dict(evt["state"])
                case "widget_close" if wid in restored:
                    restored[wid].closed = True
        self._records.update(restored)
        return len(restored)


class WidgetHandle:
    """Tool- and plugin-side handle on one open widget."""

    def __init__(self, service: "WidgetService", widget_id: str) -> None:
        self._service = service
        self.id = widget_id

    @property
    def state(self) -> dict[str, Any]:
        record = self._service.registry.get(self.id)
        return dict(record.state) if record else {}

    @property
    def closed(self) -> bool:
        record = self._service.registry.get(self.id)
        return record is None or record.closed

    async def update(
        self,
        *,
        spec: Any = None,
        state: dict[str, Any] | None = None,
        title: str | None = None,
        fallback: str | None = None,
    ) -> None:
        """Replace the spec and/or merge state into the rendered widget."""
        await self._service.update(
            self.id, spec=spec, state=state, title=title, fallback=fallback
        )

    async def close(self) -> None:
        """Mark the widget inactive; it accepts no more messages."""
        await self._service.close(self.id)

    async def wait(
        self, names: set[str] | None = None, timeout_s: float | None = None
    ) -> WidgetMessage | None:
        """Return the next message whose name is in ``names``.

        ``None`` on timeout or when the widget is (or becomes) closed.
        """
        if self.closed:
            return None
        consumer = self._service._add_consumer(self.id, names, once=True)
        try:
            if timeout_s is None:
                return await consumer.queue.get()
            return await asyncio.wait_for(consumer.queue.get(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None
        finally:
            self._service._remove_consumer(self.id, consumer)

    async def messages(
        self, names: set[str] | None = None
    ) -> AsyncIterator[WidgetMessage]:
        """Yield every accepted message until the widget closes."""
        if self.closed:
            return
        consumer = self._service._add_consumer(self.id, names, once=False)
        try:
            while True:
                msg = await consumer.queue.get()
                if msg is None:
                    return
                yield msg
        finally:
            self._service._remove_consumer(self.id, consumer)


class WidgetService:
    """Open widgets on a router and route what they send back."""

    def __init__(self, router: Any) -> None:
        self._router = router
        self.registry = WidgetRegistry()
        self._agent_ref: Callable[[], Any] | None = None
        self._restored_from: int | None = None
        self._tasks: set[asyncio.Task] = set()

    def bind_agent(self, agent: Any) -> None:
        """Route unclaimed messages to ``agent`` and restore from its session."""
        try:
            self._agent_ref = weakref.ref(agent)
        except TypeError:
            self._agent_ref = lambda: agent

    @property
    def agent(self) -> Any:
        return self._agent_ref() if self._agent_ref else None

    async def open(
        self,
        renderer: str,
        spec: Any,
        *,
        state: dict[str, Any] | None = None,
        title: str = "",
        surface: Surface = "chat",
        fallback: str = "",
        routing: Routing = "agent",
        version: int = 1,
        interactive: bool = True,
    ) -> WidgetHandle:
        """Emit a new widget and return its handle.

        Raises :class:`RendererUnavailable` when no attached output reports
        ``renderer`` at ``version`` or higher.
        """
        supported = self._router.ui_renderers().get(renderer, 0)
        if supported < version:
            raise RendererUnavailable(
                f"no attached output renders {renderer!r} v{version}"
            )
        state = dict(state or {})
        if _json_size(state) > MAX_STATE_BYTES:
            raise ValueError(f"widget state exceeds {MAX_STATE_BYTES} bytes")
        widget_id = f"w_{secrets.token_hex(6)}"
        self.registry.add(
            WidgetRecord(
                widget_id=widget_id,
                renderer=renderer,
                version=version,
                title=title,
                surface=surface,
                routing=routing,
                interactive=interactive,
                state=state,
            )
        )
        await self._router.emit_widget(
            OutputEvent(
                type="widget",
                id=widget_id,
                surface=surface,
                interactive=interactive,
                payload={
                    "renderer": renderer,
                    "version": version,
                    "title": title,
                    "spec": spec,
                    "state": state,
                    "fallback": fallback,
                    "routing": routing,
                },
            )
        )
        return WidgetHandle(self, widget_id)

    async def update(
        self,
        widget_id: str,
        *,
        spec: Any = None,
        state: dict[str, Any] | None = None,
        title: str | None = None,
        fallback: str | None = None,
    ) -> None:
        """Send a patch for an open widget."""
        record = self._require(widget_id)
        patch: dict[str, Any] = {}
        if spec is not None:
            patch["spec"] = spec
        if state:
            merged = {**record.state, **state}
            if _json_size(merged) > MAX_STATE_BYTES:
                raise ValueError(f"widget state exceeds {MAX_STATE_BYTES} bytes")
            record.state = merged
            patch["state"] = dict(state)
        if title is not None:
            record.title = title
            patch["title"] = title
        if fallback is not None:
            patch["fallback"] = fallback
        if not patch:
            return
        await self._router.emit_widget(
            OutputEvent(
                type="widget",
                id=widget_id,
                update_target=widget_id,
                surface=record.surface,
                payload=patch,
            )
        )

    async def close(self, widget_id: str) -> None:
        """Close a widget and end its message streams."""
        record = self.registry.get(widget_id)
        if record is None or record.closed:
            return
        record.closed = True
        for consumer in list(record.consumers):
            consumer.queue.put_nowait(None)
        await self._router.emit_widget(
            OutputEvent(type="widget_close", id=widget_id, update_target=widget_id)
        )

    def submit_message(self, msg: WidgetMessage) -> str:
        """Route one message; return ``accepted``, ``dropped``, ``unknown`` or ``rate_limited``."""
        record = self._lookup(msg.widget_id)
        if record is None or record.closed or not record.interactive or not msg.name:
            return "unknown"
        if _json_size(msg.data) > MAX_MESSAGE_BYTES:
            return "dropped"
        now = _clock()
        while record.recent and now - record.recent[0] > MESSAGE_WINDOW_S:
            record.recent.popleft()
        if len(record.recent) >= MESSAGE_BURST:
            return "rate_limited"
        record.recent.append(now)

        routed_to = "drop"
        for consumer in record.consumers:
            if consumer.accepts(msg.name):
                consumer.queue.put_nowait(msg)
                if consumer.once:
                    record.consumers.remove(consumer)
                routed_to = "waiter"
                break
        else:
            inject = getattr(self.agent, "inject_event", None)
            if record.routing == "agent" and inject is not None:
                self._schedule(inject(self._trigger_for(record, msg)))
                routed_to = "agent"
        self._schedule(
            self._router.emit_widget(
                OutputEvent(
                    type="widget_message",
                    id=msg.widget_id,
                    update_target=msg.widget_id,
                    payload={
                        "name": msg.name,
                        "data": msg.data,
                        "origin": msg.origin,
                        "routed_to": routed_to,
                    },
                )
            )
        )
        return "dropped" if routed_to == "drop" else "accepted"

    def submit_state(
        self, widget_id: str, state: dict[str, Any], *, source: Any = None
    ) -> str:
        """Merge client state, rebroadcast it to the other outputs; never wakes the agent."""
        record = self._lookup(widget_id)
        if record is None or record.closed:
            return "unknown"
        if not isinstance(state, dict):
            return "dropped"
        merged = {**record.state, **state}
        if _json_size(merged) > MAX_STATE_BYTES:
            return "dropped"
        record.state = merged
        self._schedule(
            self._router.emit_widget(
                OutputEvent(
                    type="widget_state",
                    id=widget_id,
                    update_target=widget_id,
                    payload={"state": dict(merged)},
                ),
                exclude=source,
            )
        )
        return "accepted"

    def _require(self, widget_id: str) -> WidgetRecord:
        record = self._lookup(widget_id)
        if record is None:
            raise KeyError(f"unknown widget: {widget_id}")
        return record

    def _lookup(self, widget_id: str) -> WidgetRecord | None:
        record = self.registry.get(widget_id)
        if record is None:
            self._restore_once()
            record = self.registry.get(widget_id)
        return record

    def _restore_once(self) -> None:
        """Rebuild the registry from the bound agent's session store, once per store."""
        agent = self.agent
        store = getattr(agent, "session_store", None) if agent else None
        if store is None or self._restored_from == id(store):
            return
        self._restored_from = id(store)
        try:
            events = store.get_events(agent.config.name)
        except Exception as e:
            logger.warning("widget restore failed", error=str(e), exc_info=True)
            return
        added = self.registry.restore(events)
        if added:
            logger.debug("Widgets restored", count=added)

    def _add_consumer(
        self, widget_id: str, names: set[str] | None, *, once: bool
    ) -> _Consumer:
        record = self._require(widget_id)
        consumer = _Consumer(
            names=frozenset(names) if names else None,
            queue=asyncio.Queue(),
            once=once,
        )
        record.consumers.append(consumer)
        return consumer

    def _remove_consumer(self, widget_id: str, consumer: _Consumer) -> None:
        record = self.registry.get(widget_id)
        if record is not None and consumer in record.consumers:
            record.consumers.remove(consumer)

    @staticmethod
    def _trigger_for(record: WidgetRecord, msg: WidgetMessage) -> TriggerEvent:
        """Build the ``widget_event`` trigger for an unclaimed message."""
        trust = ' trust="untrusted"' if msg.origin == "app" else ""
        body = json.dumps(msg.data, ensure_ascii=False, default=str)
        title = record.title.replace('"', "'")
        content = (
            f'<widget_event widget="{record.widget_id}" title="{title}" '
            f'name="{msg.name}" origin="{msg.origin}"{trust}>{body}</widget_event>'
        )
        return TriggerEvent(
            type="widget_event",
            content=content,
            context={
                "widget_id": record.widget_id,
                "renderer": record.renderer,
                "name": msg.name,
                "origin": msg.origin,
                "data": msg.data,
            },
        )

    def _schedule(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "widget task failed",
                error=str(task.exception()),
                exc_info=task.exception(),
            )

    async def drain(self) -> None:
        """Wait for scheduled emits and injections (used by tests and shutdown)."""
        while pending := [t for t in self._tasks if not t.done()]:
            await asyncio.gather(*pending, return_exceptions=True)


__all__ = [
    "MAX_MESSAGE_BYTES",
    "MAX_STATE_BYTES",
    "MESSAGE_BURST",
    "MESSAGE_WINDOW_S",
    "RendererUnavailable",
    "WIDGET_EVENT_TYPES",
    "WidgetHandle",
    "WidgetMessage",
    "WidgetRecord",
    "WidgetRegistry",
    "WidgetService",
]
