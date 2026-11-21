"""Event reads and agent discovery for ``SessionStore``."""

from collections.abc import Iterator
from typing import Any

from kohakuterrarium.session.store.events.key_index import EventKeyIndex
from kohakuterrarium.session.history.paging import _ordered_event_refs
from kohakuterrarium.session.history.replay import (
    dedupe_adjacent_duplicate_events,
    normalize_resumable_events,
)
from kohakuterrarium.session.store.events.discovery import (
    discover_agents,
    discover_attached_agents,
)
from kohakuterrarium.session.store.events.values import _iter_event_values
from kohakuterrarium.session.vault.io import (
    key_text,
    read_prefix,
    read_values,
    scan_keys,
)


class MemoryEvent(dict):
    """Detached event with a source locator that is never persisted as payload."""

    __slots__ = ("source_ref",)

    def __init__(self, content: dict, source_ref: str):
        super().__init__(content)
        self.source_ref = source_ref


class EventReadMixin:
    """Read persisted events back by agent, id cursor, or resume normalization."""

    # Owned by the events append part.
    _event_keys: EventKeyIndex
    global_max_event_id: int
    events: Any

    def get_events(self, agent: str, since_event_id: int | None = None) -> list[dict]:
        """Get events for an agent, ordered by sequence.

        Returns list of event dicts with keys sorted chronologically. With
        ``since_event_id``, only events whose ``event_id`` exceeds it are
        returned (cursor-style incremental reads).
        """
        return list(self._iter_events(agent, since_event_id))

    def get_memory_events(self, agent: str) -> list[dict]:
        """Read indexed history with its exact physical source refs attached.

        Ref identity lives on an ephemeral dict subclass, not inside the
        persistent event content. Unreadable rows are skipped without shifting
        the physical locator of subsequent readable events.
        """
        self._flush_events_cache()
        keys = _ordered_event_refs(self, agent, preserve_noncanonical=True)
        return [
            MemoryEvent(event, key_text(key))
            for key, event in read_values(self.events, keys, what="memory event")
            if isinstance(event, dict)
        ]

    def _iter_events(
        self, agent: str, since_event_id: int | None = None
    ) -> Iterator[dict]:
        """Iterate values in physical-key order inside the owning store operation."""
        self._flush_events_cache()
        keys = _ordered_event_refs(self, agent, preserve_noncanonical=True)
        return _iter_event_values(self.events, keys, since_event_id)

    def max_event_id(self, agent: str) -> int:
        """Return the session-global max ``event_id`` (monotonic, per-session).

        Local events take ids from ``_global_event_id`` and mirrored events
        ratchet it upward, so the counter is the authoritative maximum and
        this is O(1). ``event_id`` is a session-global (not per-agent) cursor,
        so ``agent`` is accepted for API symmetry with ``get_events`` and
        intentionally unused.
        """
        del agent
        return self.global_max_event_id

    def get_event_by_id(self, agent: str, event_id: int) -> dict | None:
        """Fetch a current event using a lazily built, per-agent key index.

        The first lookup scans historical values; subsequent lookups read only
        the matching payload. Appends update locators, including mirrored ids.
        """
        self._flush_events_cache()
        return self._event_keys.get(self.events, agent, event_id)

    def get_resumable_events(
        self,
        agent: str,
        *,
        live_job_ids: set[str] | None = None,
        since_event_id: int | None = None,
    ) -> list[dict]:
        """Get agent events normalized for resume/history replay.

        ``live_job_ids`` (when provided) lists currently-in-flight job
        ids; those jobs are NOT synthesized as interrupted. Callers
        serving a live session's history should pass the running jobs;
        resume-time callers (post-restart, every unfinished job IS dead)
        leave it ``None``.
        """
        events = dedupe_adjacent_duplicate_events(
            self.get_events(agent, since_event_id=since_event_id)
        )
        return normalize_resumable_events(events, live_job_ids=live_job_ids)

    def get_all_events(self) -> list[tuple[str, dict]]:
        """Get ALL events across all agents, sorted by timestamp.

        Returns list of (key, event_dict) tuples.
        """
        self.events.flush_cache()
        all_events = [
            (key_text(key), evt)
            for key, evt in read_prefix(self.events, what="event in get_all_events")
        ]
        all_events.sort(key=lambda x: x[1].get("ts", 0))
        return all_events

    def discover_agents_from_events(self) -> list[str]:
        """Scan event keys, returning agent names in first-seen order.

        Framework namespaces and attached-agent namespaces are excluded so
        resume does not rebuild them as standalone creatures.
        """
        return discover_agents(self._event_key_names())

    def discover_attached_agents(self) -> list[dict[str, Any]]:
        """Return attached-agent namespaces from events in first-seen order."""
        return discover_attached_agents(self._event_key_names())

    def _event_key_names(self) -> list[str]:
        """Event keys as text; buffered keys are flushed so they are visible."""
        self._flush_events_cache()
        return [key_text(key) for key in scan_keys(self.events)]
