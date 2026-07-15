"""Provide a read-only high-level view of completed session files.

Reader operations preserve session status and recency metadata.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kohakuterrarium.session.history import (
    dedupe_adjacent_duplicate_events,
    select_live_event_ids,
)
from kohakuterrarium.session.memory import SearchResult, SessionMemory
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TurnView:
    """One conversational turn, reassembled from the event log.

    Only the selected live branch is included; regenerated and edited siblings
    remain excluded.
    """

    index: int
    user_text: str = ""
    assistant_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    source: str = ""
    ts: float = 0.0


class SessionReader:
    """Read-only view over a session file (context-manager friendly)."""

    def __init__(self, path: "str | Path") -> None:
        path = Path(path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Session file not found: {path}")
        self._path = path
        self._store = SessionStore.open_readonly(path)

    def __enter__(self) -> "SessionReader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._store.close()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def meta(self) -> dict[str, Any]:
        """Session metadata (session_id, config_type/path, status, ...)."""
        return self._store.load_meta()

    @property
    def agents(self) -> list[str]:
        """Agent names recorded in this session."""
        return list(self.meta.get("agents") or [])

    def events(self, agent: str | None = None) -> list[dict[str, Any]]:
        """The append-only event log (every chunk / tool call / trigger).

        ``agent=None`` concatenates every agent's events.
        """
        names = [agent] if agent else self.agents
        out: list[dict[str, Any]] = []
        for name in names:
            out.extend(self._store.get_events(name))
        return out

    def conversation(self, agent: str | None = None) -> list[dict[str, Any]]:
        """The final conversation snapshot (OpenAI message dicts)."""
        name = agent or (self.agents[0] if self.agents else "")
        return self._store.load_conversation(name) or []

    def channel_messages(self, channel: str) -> list[dict[str, Any]]:
        """Message history of one terrarium channel."""
        return self._store.get_channel_messages(channel)

    def turns(self, agent: str | None = None) -> list[TurnView]:
        """Reassemble live-branch turns from the event log."""
        raw = self.events(agent)
        if not raw:
            return []
        raw = dedupe_adjacent_duplicate_events(raw)
        live_ids = select_live_event_ids(raw)

        turns: list[TurnView] = []
        current: TurnView | None = None
        for evt in raw:
            eid = evt.get("event_id")
            if isinstance(eid, int) and eid not in live_ids:
                continue
            etype = evt.get("type", "")
            if etype in ("user_input", "trigger_fired"):
                if current is not None and (
                    current.user_text or current.assistant_text
                ):
                    turns.append(current)
                current = TurnView(
                    index=len(turns),
                    user_text=evt.get("content", ""),
                    source=evt.get("source", etype),
                    ts=evt.get("ts", 0.0),
                )
            elif current is None:
                continue
            elif etype in ("text", "text_chunk"):
                current.assistant_text += evt.get("content", "")
            elif etype == "tool_call":
                current.tool_calls.append(
                    {
                        "name": evt.get("name", ""),
                        "args": evt.get("args", {}),
                    }
                )
        if current is not None and (current.user_text or current.assistant_text):
            turns.append(current)
        return turns

    def search(
        self,
        query: str,
        *,
        mode: str = "fts",
        k: int = 10,
        agent: str | None = None,
    ) -> list[SearchResult]:
        """Full-text (or vector, if indexed) search over the session.

        Sessions without an existing memory index return no hits; call
        :meth:`index` first for ad-hoc keyword search.
        """
        memory = SessionMemory(str(self._path))
        try:
            return memory.search(query, mode=mode, k=k, agent=agent)
        finally:
            memory.close()

    def index(self) -> int:
        """Index this session's events for :meth:`search` (FTS only)."""
        memory = SessionMemory(str(self._path))
        try:
            total = 0
            for name in self.agents:
                total += memory.index_events(name, self._store.get_events(name))
            return total
        finally:
            memory.close()
