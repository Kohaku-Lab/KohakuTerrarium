"""Event identity, counters, buffering and subscription for ``SessionStore``."""

import time
from collections.abc import Callable
from typing import Any

from kohakuterrarium.session.branch_index import BranchIndex
from kohakuterrarium.session.event_key_index import EventKeyIndex
from kohakuterrarium.session.store.events.counters import (
    reserve_event_ids,
    restore_event_counters,
    scan_max_event_id,
)
from kohakuterrarium.session.store.events.flush import EventFlushPipeline
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class EventAppendMixin:
    """Assign event ids and keys, buffer rows for persistence, notify subscribers."""

    # Buffered events and FTS rows commit after N appends or T seconds.
    DEFAULT_FLUSH_EVERY_N_EVENTS = 4
    DEFAULT_FLUSH_EVERY_N_SECONDS = 1.0
    # One watermark write covers this many newly assigned event ids.
    EVENT_ID_BLOCK = 64

    # Owned by the handle part.
    _path: str
    _readonly: bool
    events: Any
    state: Any

    def _init_events(
        self,
        flush_every_n_events: int | None,
        flush_every_n_seconds: float | None,
    ) -> None:
        self._flush_every_n_events: int = (
            flush_every_n_events
            if flush_every_n_events is not None
            else self.DEFAULT_FLUSH_EVERY_N_EVENTS
        )
        self._flush_every_n_seconds: float = (
            flush_every_n_seconds
            if flush_every_n_seconds is not None
            else self.DEFAULT_FLUSH_EVERY_N_SECONDS
        )
        self._flush: EventFlushPipeline | None = None
        # New ids are assigned above the persisted watermark (``_id_floor``);
        # ``_reserved_event_id`` is the watermark this store last wrote.
        self._id_floor = 0
        self._reserved_event_id = 0
        # Per-namespace sequences coexist with one session-wide event identifier.
        self._event_keys = EventKeyIndex()
        self._branch_maxima = BranchIndex()
        self._event_seq: dict[str, int] = {}
        # ``None`` until a read-only store is first asked for its maximum.
        self._global_event_id: int | None = 0
        # Subscribers observe events once they are buffered for persistence.
        self._event_subscribers: list[Callable[[str, dict], None]] = []

    def _restore_event_counters(self) -> None:
        """Restore event sequence counters; a clean-close counter skips the value scan.

        A read-only store defers the scan until its maximum is requested.
        """
        self._event_keys.clear()
        self._branch_maxima.clear()
        counters = restore_event_counters(
            self.events, self._event_seq, state=self.state
        )
        self._id_floor = counters.floor
        self._reserved_event_id = 0
        self._global_event_id = counters.exact
        if self._global_event_id is None and not self._readonly:
            self._global_event_id = scan_max_event_id(self.events)

    def _next_event_seq(self, agent: str) -> int:
        """Get and increment the event sequence counter for an agent."""
        seq = self._event_seq.get(agent, 0)
        self._event_seq[agent] = seq + 1
        return seq

    @property
    def global_max_event_id(self) -> int:
        """Largest event id persisted or written by this store, across agents."""
        if self._global_event_id is None:
            self._global_event_id = scan_max_event_id(self.events)
        return self._global_event_id

    def _next_global_event_id(self) -> int:
        """Assign the next id above both the known maximum and the watermark."""
        self._global_event_id = max(self.global_max_event_id, self._id_floor) + 1
        return self._global_event_id

    def _reserve_through(self, event_id: int) -> None:
        """Persist a watermark covering ``event_id`` before its row is buffered."""
        if event_id <= self._reserved_event_id:
            return
        upto = event_id + self.EVENT_ID_BLOCK
        if reserve_event_ids(self.state, upto):
            self._reserved_event_id = upto

    def append_event(
        self,
        agent: str,
        event_type: str,
        data: dict,
        *,
        turn_index: int | None = None,
        spawned_in_turn: int | None = None,
        branch_id: int | None = None,
        parent_branch_path: list[tuple[int, int]] | None = None,
    ) -> tuple[str, int]:
        """Append one event. Returns ``(key, event_id)``.

        Every event carries a per-session monotonic ``event_id`` plus
        optional ``turn_index`` / ``spawned_in_turn`` / ``branch_id``.

        ``turn_index`` identifies a logical user turn across reruns. Sibling
        ``branch_id`` values represent alternate responses; replay selects the
        latest branch unless a caller requests another.
        """
        if self._flush is None:
            raise PermissionError(f"Session store is read-only: {self._path}")
        with self._flush.lock:
            key, event_id = self._buffer_event(
                agent,
                event_type,
                data,
                turn_index=turn_index,
                spawned_in_turn=spawned_in_turn,
                branch_id=branch_id,
                parent_branch_path=parent_branch_path,
            )

        # Subscriber failures cannot invalidate a successfully appended event.
        for cb in tuple(self._event_subscribers):
            try:
                cb(key, data)
            except Exception as e:
                logger.warning("Event subscriber failed", error=str(e), exc_info=True)

        self._flush.appended()
        return key, event_id

    def _buffer_event(
        self,
        agent: str,
        event_type: str,
        data: dict,
        *,
        turn_index: int | None,
        spawned_in_turn: int | None,
        branch_id: int | None,
        parent_branch_path: list[tuple[int, int]] | None,
    ) -> tuple[str, int]:
        """Assign the key and id, then buffer the event row and its FTS row."""
        seq = self._next_event_seq(agent)
        key = f"{agent}:e{seq:06d}"
        # Mirrors preserve source event identifiers for consistent cross-node cursors.
        preset_eid = data.get("event_id")
        if isinstance(preset_eid, int) and preset_eid > 0:
            event_id = preset_eid
            # Future local identifiers must remain above imported identifiers.
            if event_id > self.global_max_event_id:
                self._global_event_id = event_id
        else:
            event_id = self._next_global_event_id()
        self._reserve_through(self._global_event_id)
        data["type"] = event_type
        data["event_id"] = event_id
        if turn_index is not None:
            data["turn_index"] = turn_index
        if spawned_in_turn is not None:
            data["spawned_in_turn"] = spawned_in_turn
        elif turn_index is not None and "spawned_in_turn" not in data:
            data["spawned_in_turn"] = turn_index
        if branch_id is not None:
            data["branch_id"] = branch_id
        # Persist lineage in JSON-safe form to avoid replay-time history scans.
        if parent_branch_path is not None:
            data["parent_branch_path"] = [list(p) for p in parent_branch_path]
        if "ts" not in data:
            data["ts"] = time.time()
        self._branch_maxima.append(self.events, key, agent, data)
        self._event_keys.record(key, event_id)

        text = data.get("content") or data.get("output") or data.get("text") or ""
        if isinstance(text, str) and len(text) > 10:
            self._flush.queue_fts(
                text,
                {
                    "event_key": key,
                    "agent": agent,
                    "type": event_type,
                    "event_id": event_id,
                },
            )
        return key, event_id

    @property
    def _unflushed_event_count(self) -> int:
        return 0 if self._flush is None else self._flush.unflushed

    def _flush_events_cache(self) -> None:
        """Commit buffered events and FTS rows so reads observe them."""
        if self._flush is None:
            return
        self._flush.flush()

    def subscribe(self, callback: Callable[[str, dict], None]) -> None:
        """Register a callback fired after each ``append_event``.

        Re-registering the same callable is a no-op, allowing safe reconnects.
        """
        if callback not in self._event_subscribers:
            self._event_subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[str, dict], None]) -> None:
        """Remove a previously-registered callback. Safe if not present."""
        try:
            self._event_subscribers.remove(callback)
        except ValueError:
            pass
