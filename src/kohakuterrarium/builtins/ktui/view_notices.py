"""Notice-like cells of a ``CreatureView``: notices, Markdown notices, errors, cards, the
"older turns" mark, pictures, videos and sounds (kept as the conversation's artifacts),
compaction and progress rows. Mixed into ``CreatureView``, whose ``render`` dispatches here;
the state (``doc``, ``messages``, ``artifacts``, ``_compacting``, ``_progress``) belongs to the
view."""

from typing import Any

from kohakutui.ui import Card, Cell, Markdown, Node, Progress, Status, Text

from kohakuterrarium.builtins.ktui.format import one_line
from kohakuterrarium.builtins.ktui.tool_bodies import (
    MediaPlace,
    image_cell,
    media_cell,
)
from kohakuterrarium.builtins.ktui.view_state import Message


class NoticeCells:
    app: Any
    doc: Any
    focused: bool
    messages: dict[int, Message]
    artifacts: list[tuple[str, str, str]]
    media: MediaPlace | None
    _compacting: Node | None
    _progress: dict[str, Node]

    def _notice(self, text: str, tone: str = "muted", icon: str | None = None) -> None:
        self._assistant_end()
        self._seal(Cell(role="system", icon=icon, children=[Text(text, tone=tone)]))

    def _markdown_notice(self, title: str, body: str, collapsed: bool = False) -> None:
        self._assistant_end()
        spec = Cell(
            role="system",
            title=title,
            tone="accent",
            collapsed=collapsed or None,
            summary=one_line(body, 70) if collapsed else None,
            children=[Markdown(source=body)],
        )
        self._seal(spec)

    def _error(self, text: str) -> None:
        self._assistant_end()
        self._seal(Cell(role="error", children=[Text(text)]))

    def _older(self, turns: int) -> None:
        """The mark where a long history starts: its keys load the turns before it."""
        spec = Cell(
            role="system",
            icon="↑",
            title=f"{turns} older turns not shown",
            detail="click, or ctrl+↑ then l, to load them (inline: /older)",
            actions=[{"key": "l", "label": "load older", "value": "msg:older"}],
        )
        node = self.doc.add(spec)
        node.seal()
        self.messages[node.id] = Message("older", "", None, node)

    def _artifact(self, kind: str, url: str, name: str) -> None:
        if all(a[1] != url for a in self.artifacts):
            self.artifacts.append((kind, url, name))

    def _media(self, url: str, name: str, kind: str) -> None:
        self._artifact(kind, url, name)
        self._assistant_end()
        self._seal(media_cell(url, name, kind, self.media))

    def _image(self, url: str, name: str = "") -> None:
        """A picture: drawn in half blocks from a data URL or a file, a link otherwise."""
        self._artifact("image", url, name)
        self._assistant_end()
        self._seal(image_cell(url, name, place=self.media))

    def _card(self, title: str, body: str) -> None:
        self._assistant_end()
        self._seal(Card(title=title or None, body=body))

    def _compact_start(self, round_num: int) -> None:
        if self._compacting is None:
            label = f"compacting context · round {round_num} · you can keep chatting"
            self._compacting = self.app.live.add(Status(label=label, tone="attention"))

    def _compact_end(self, round_num: int, summary: str, skipped: str) -> None:
        if self._compacting is not None:
            self._compacting.remove()
            self._compacting = None
        if skipped:
            self._notice(f"compaction skipped: {skipped}")
        elif summary:
            self._markdown_notice(
                f"context compacted · round {round_num}", summary, True
            )
        else:
            self._notice(f"context compacted · round {round_num}", tone="accent")

    def _progress_update(
        self, key: str, label: str, value: Any, total: Any, done: bool
    ) -> None:
        node = self._progress.get(key)
        if done:
            if node is not None:
                node.remove()
                self._progress.pop(key, None)
            self._notice(f"✓ {label}", tone="ok")
            return
        if node is None:
            if not self.focused:
                return
            node = self.app.live.add(Progress(label=label, total=total))
            self._progress[key] = node
        if value is not None:
            node.update(label=label, total=total, value=value)
