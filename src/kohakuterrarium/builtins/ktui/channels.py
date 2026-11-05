"""A channel tab: one graph channel's traffic in a document of its own, and sending to it.

Opening the tab replays the channel's kept history, then follows ``CHANNEL_MESSAGE`` engine
events for that channel. While the tab is shown, the composer sends to the channel as
``human`` (the web's channel view does the same).
"""

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from kohakutui.ui import Cell, Markdown, Node

from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.terrarium.events import EventFilter, EventKind
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

logger = get_logger(__name__)

HUMAN = "human"
# history replayed into a fresh tab
REPLAY = 200


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict)]
        return "\n".join(p for p in parts if p) or str(content)
    return str(content)


def _clock(when: Any) -> str | None:
    """HH:MM of a message time (a datetime or an ISO string), None when unknown."""
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when)
        except ValueError:
            return None
    if not isinstance(when, datetime):
        return None
    return when.astimezone().strftime("%H:%M")


class ChannelView:
    """Draws one channel into its own document; unread counts while another tab is shown."""

    def __init__(self, shell: "KtuiShell", name: str):
        self.shell = shell
        self.name = name
        self.graph_id = shell.focused.creature.graph_id
        self.focused = False
        self.unread = 0
        self.doc: Node = shell.app.add_document()
        self._seen: set[str] = set()
        # subscribed before the history is read, so nothing falls between (dupes are dropped)
        events = shell.engine.subscribe(
            EventFilter(
                kinds={EventKind.CHANNEL_MESSAGE},
                graph_ids={self.graph_id},
                channels={name},
            )
        )
        channel = self._channel()
        history = list(getattr(channel, "history", []) or [])[-REPLAY:]
        for msg in history:
            self._add(msg.sender, msg.content, msg.message_id, msg.timestamp)
        if not history:
            self.doc.add(
                Cell(role="system", children=[Markdown(source=self._empty())])
            ).seal()
        self._task: asyncio.Task | None = shell.spawn(self._follow(events))

    @staticmethod
    def names(engine: Any, entry: Any) -> list[str]:
        """Channel names of the graph ``entry``'s creature is in (none when unknown)."""
        try:
            graph = engine.get_graph(entry.creature.graph_id)
        except (KeyError, AttributeError):
            return []
        return sorted(graph.channels)

    def _channel(self) -> Any:
        try:
            return self.shell.engine.channel(self.graph_id, self.name)
        except KeyError:
            return None

    def _empty(self) -> str:
        try:
            graph = self.shell.engine.get_graph(self.graph_id)
        except KeyError:
            return f"no messages on #{self.name} yet"
        names = {cid: e.view.name for cid, e in self.shell.entries.items()}
        listen = [
            names.get(c, c) for c, chs in graph.listen_edges.items() if self.name in chs
        ]
        send = [
            names.get(c, c) for c, chs in graph.send_edges.items() if self.name in chs
        ]
        info = graph.channels.get(self.name)
        lines = [f"no messages on **#{self.name}** yet"]
        if info is not None and info.description:
            lines.append(f"*{info.description}*")
        lines.append(
            f"listen: {', '.join(listen) or '—'} · send: {', '.join(send) or '—'}"
        )
        lines.append("type below to send to the channel")
        return "\n\n".join(lines)

    def _add(
        self, sender: str, content: Any, message_id: str = "", when: Any = None
    ) -> None:
        if message_id:
            if message_id in self._seen:
                return
            self._seen.add(message_id)
        role = "user" if sender == HUMAN else "assistant"
        cell = Cell(
            role=role,
            title=(
                None
                if role == "user"
                else " · ".join(filter(None, (sender, _clock(when))))
            ),
            children=[Markdown(source=_text(content))],
        )
        self.doc.add(cell).seal()

    async def _follow(self, events: Any) -> None:
        async for ev in events:
            payload = ev.payload or {}
            self._add(
                str(payload.get("sender", "")),
                payload.get("content", ""),
                str(payload.get("message_id", "")),
                payload.get("timestamp"),
            )
            if not self.focused:
                self.unread += 1
                self.shell.chrome.channel_changed(self)

    async def send(self, text: str) -> None:
        channel = self._channel()
        if channel is None:
            self.shell.app.notify(f"#{self.name} is gone", "warning")
            return
        await channel.send(ChannelMessage(sender=HUMAN, content=text))

    def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.doc.remove()
