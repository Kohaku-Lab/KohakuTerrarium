"""Persist session metadata, events, state, and indexes in KohakuVault."""

import json
import platform
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kohakuterrarium.session.artifacts import artifacts_dir_for, write_artifact_bytes
from kohakuterrarium.session.branch_index import BranchIndex, BranchIndexMixin
from kohakuterrarium.session.event_key_index import EventKeyIndex
from kohakuterrarium.session.history import (
    dedupe_adjacent_duplicate_events,
    normalize_resumable_events,
)
from kohakuterrarium.session.identity import legacy_conversation_id, new_conversation_id
from kohakuterrarium.session.rollup import (
    get_turn_rollup,
    list_turn_rollups,
    save_turn_rollup,
)
from kohakuterrarium.session.snapshot import load_branch_snapshot, save_branch_snapshot
from kohakuterrarium.session.store_affinity import StoreAffinityMixin
from kohakuterrarium.session.store_counters import (
    persist_event_counter,
    reserve_event_ids,
    restore_event_counters,
    restore_subagent_counters,
    restore_suffix_counters,
    scan_max_event_id,
)
from kohakuterrarium.session.store_discovery import (
    discover_agents,
    discover_attached_agents,
)
from kohakuterrarium.session.store_events import _iter_event_values
from kohakuterrarium.session.store_flush import EventFlushPipeline, insert_fts_rows
from kohakuterrarium.session.store_fork import perform_fork
from kohakuterrarium.session.store_resources import StoreResourceMixin
from kohakuterrarium.session.store_tables import open_store_tables
from kohakuterrarium.session.table_policy import TablePolicy, resolve_table_policy
from kohakuterrarium.session.token_views import (
    token_usage as _token_usage_impl,
    token_usage_all_loops as _token_usage_all_loops_impl,
)
from kohakuterrarium.session.vault_handles import dispose
from kohakuterrarium.session.vault_io import (
    key_text,
    read_prefix,
    read_values,
    scan_keys,
)
from kohakuterrarium.session.vault_read import open_read_text
from kohakuterrarium.session.version import FORMAT_VERSION, require_writable_format
from kohakuterrarium.utils.fs_path import coerce_fs_path
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SessionStore(StoreAffinityMixin, StoreResourceMixin, BranchIndexMixin):
    """Persistent session storage backed by KohakuVault.

    One ``.kohakutr`` file contains metadata, per-agent state, append-only
    events, channel history, child-run records, conversation caches, turn
    rollups, job records, and full-text search data.
    """

    # Buffered events and FTS rows commit after N appends or T seconds.
    DEFAULT_FLUSH_EVERY_N_EVENTS = 4
    DEFAULT_FLUSH_EVERY_N_SECONDS = 1.0
    # One watermark write covers this many newly assigned event ids.
    EVENT_ID_BLOCK = 64

    def __init__(
        self,
        path: str | Path,
        *,
        flush_every_n_events: int | None = None,
        flush_every_n_seconds: float | None = None,
        writer_lock: bool = False,
        read_only: bool = False,
        table_policy: Mapping[str, TablePolicy] | None = None,
    ) -> None:
        self._init_affinity()
        self._path = str(coerce_fs_path(path))
        # Read-only stores open existing files and never write to them.
        self._readonly = read_only
        if read_only:
            Path(self._path).resolve(strict=True)
        else:
            require_writable_format(self._path)
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

        # Occupancy is enrolled before native open; the writer lock is optional.
        self._ready = False
        self._enroll_resources(writer_lock=writer_lock and not read_only)

        self._table_policy = resolve_table_policy(table_policy)
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
        self._channel_seq: dict[str, int] = {}
        self._subagent_runs: dict[str, int] = {}
        # ``None`` until a read-only store is first asked for its maximum.
        self._global_event_id: int | None = 0
        # Artifact storage is created only on first use.
        self._artifacts_dir: Path | None = None
        # Companion resources close in LIFO order before the store lock is released.
        self._companion_closers: list[Callable[[], None]] = []
        # Subscribers observe events once they are buffered for persistence.
        self._event_subscribers: list[Callable[[str, dict], None]] = []

        # Construction failures must not strand tables or the writer lock.
        try:
            self._open_tables()
            self._restore_counters()
        except BaseException:
            self._discard_failed_open()
            raise
        self._ready = True
        logger.debug("SessionStore opened", path=self._path)

    @property
    def is_ready(self) -> bool:
        """Whether the tables finished opening (readers must wait until then)."""
        return self._ready

    def _open_tables(self) -> None:
        """Open every table; a writable store also gets its flush pipeline."""
        open_store_tables(
            self, self._path, read_only=self._readonly, policy=self._table_policy
        )
        if self._readonly:
            return
        self.events.enable_cache()
        self._flush = EventFlushPipeline(
            self.events,
            self.fts,
            every_n=self._flush_every_n_events,
            every_seconds=self._flush_every_n_seconds,
        )

    @property
    def session_id(self) -> str:
        """Session id from meta (preferred) or the filename stem."""
        stored = self.meta.get("session_id") if "session_id" in self.meta else None
        if stored:
            return str(stored)
        return Path(self._path).stem

    @property
    def artifacts_dir(self) -> Path:
        """Session-local directory for binary artifacts (``<stem>.artifacts/``)."""
        if self._artifacts_dir is None:
            self._artifacts_dir = artifacts_dir_for(Path(self._path))
        return self._artifacts_dir

    def write_artifact(self, filename: str, data: bytes) -> Path:
        """Write raw bytes to ``artifacts_dir/<filename>``. Traversal-safe."""
        return write_artifact_bytes(self.artifacts_dir, filename, data)

    def register_companion_closer(self, closer: Callable[[], None]) -> None:
        # Registration is idempotent; companion resources close in reverse order.
        if closer not in self._companion_closers:
            self._companion_closers.append(closer)

    def _restore_counters(self) -> None:
        """Restore sequence counters; a clean-close counter skips the value scan.

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
        restore_suffix_counters(self.channels, ":m", self._channel_seq)
        restore_subagent_counters(self.subagents, self._subagent_runs)

        if self._event_seq or self._channel_seq:
            logger.debug(
                "Counters restored",
                event_agents=list(self._event_seq.keys()),
                channel_count=len(self._channel_seq),
            )

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

    def get_events(self, agent: str, since_event_id: int | None = None) -> list[dict]:
        """Get events for an agent, ordered by sequence.

        Returns list of event dicts with keys sorted chronologically. With
        ``since_event_id``, only events whose ``event_id`` exceeds it are
        returned (cursor-style incremental reads).
        """
        return list(self._iter_events(agent, since_event_id))

    def _iter_events(
        self, agent: str, since_event_id: int | None = None
    ) -> Iterator[dict]:
        """Iterate values in physical-key order inside the owning store operation."""
        self._flush_events_cache()
        keys = scan_keys(self.events, f"{agent}:e")
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

    def save_conversation(self, agent: str, messages: list[dict] | str) -> None:
        """Save a conversation snapshot (overwritten each time).

        Accepts either a list of message dicts (preferred, stored via msgpack)
        or a JSON string (legacy, stored as-is).
        """
        self.state[f"{agent}:branch_snapshots"] = []
        self.conversation[agent] = messages

    def save_branch_snapshot(
        self,
        agent: str,
        messages: list[dict],
        *,
        branch: dict | None,
        watermark: int,
        compact_state: dict | None = None,
    ) -> None:
        """Save a branch-local checkpoint alongside the latest conversation."""
        save_branch_snapshot(
            self,
            agent,
            messages,
            branch=branch,
            watermark=watermark,
            compact_state=compact_state,
        )

    def load_branch_snapshot(
        self, agent: str, branch_view: dict[int, int]
    ) -> dict | None:
        """Load a detached checkpoint on a resolved historical lineage."""
        return load_branch_snapshot(self, agent, branch_view)

    def load_conversation(self, agent: str) -> list[dict] | None:
        """Load the latest conversation snapshot for an agent.

        Returns a list of message dicts (OpenAI format), or None if not found.
        """
        try:
            val = self.conversation[agent]
            if isinstance(val, list):
                return val
            # Older sessions may contain JSON rather than auto-packed lists.
            if isinstance(val, (str, bytes)):
                s = (
                    val.decode("utf-8", errors="replace")
                    if isinstance(val, bytes)
                    else val
                )
                data = json.loads(s)
                if isinstance(data, dict) and "messages" in data:
                    return data["messages"]
                if isinstance(data, list):
                    return data
            return None
        except KeyError:
            return None

    def save_state(
        self,
        agent: str,
        *,
        scratchpad: dict[str, str] | None = None,
        turn_count: int | None = None,
        token_usage: dict[str, int] | None = None,
        triggers: list[dict] | None = None,
        compact_count: int | None = None,
    ) -> None:
        """Save per-agent runtime state."""
        if scratchpad is not None:
            self.state[f"{agent}:scratchpad"] = scratchpad
        if turn_count is not None:
            self.state[f"{agent}:turn_count"] = turn_count
        if token_usage is not None:
            self.state[f"{agent}:token_usage"] = token_usage
        if triggers is not None:
            self.state[f"{agent}:triggers"] = triggers
        if compact_count is not None:
            self.state[f"{agent}:compact_count"] = compact_count

    def load_scratchpad(self, agent: str) -> dict[str, str]:
        """Load scratchpad for an agent."""
        try:
            val = self.state[f"{agent}:scratchpad"]
            return val if isinstance(val, dict) else {}
        except KeyError:
            return {}

    def load_turn_count(self, agent: str) -> int:
        """Load turn count for an agent."""
        try:
            return int(self.state[f"{agent}:turn_count"])
        except (KeyError, TypeError, ValueError):
            return 0

    def load_token_usage(self, agent: str) -> dict[str, int]:
        """Load cumulative token usage for an agent."""
        try:
            val = self.state[f"{agent}:token_usage"]
            return val if isinstance(val, dict) else {}
        except KeyError:
            return {}

    def load_triggers(self, agent: str) -> list[dict]:
        """Load saved resumable triggers for an agent."""
        try:
            val = self.state[f"{agent}:triggers"]
            return val if isinstance(val, list) else []
        except KeyError:
            return []

    def save_turn_rollup(
        self, agent: str, turn_index: int, data: dict[str, Any]
    ) -> None:
        """Write a per-turn rollup row. See :mod:`session.rollup`."""
        save_turn_rollup(self.turn_rollup, agent, turn_index, data)

    def get_turn_rollup(self, agent: str, turn_index: int) -> dict | None:
        """Load a per-turn rollup row. Returns ``None`` when missing."""
        return get_turn_rollup(self.turn_rollup, agent, turn_index)

    def list_turn_rollups(self, agent: str) -> list[dict]:
        """List rollup rows for an agent, ordered by ``turn_index``."""
        return list_turn_rollups(self.turn_rollup, agent)

    def _next_channel_seq(self, channel: str) -> int:
        seq = self._channel_seq.get(channel, 0)
        self._channel_seq[channel] = seq + 1
        return seq

    def save_channel_message(self, channel: str, data: dict) -> str:
        """Append a channel message. Returns the key."""
        seq = self._next_channel_seq(channel)
        key = f"{channel}:m{seq:06d}"
        if "ts" not in data:
            data["ts"] = time.time()
        self.channels[key] = data

        content = data.get("content", "")
        # The channel row commits now, so its FTS row does too.
        if self.fts is not None and isinstance(content, str) and len(content) > 10:
            insert_fts_rows(
                self.fts,
                [
                    (
                        content,
                        {
                            "channel_key": key,
                            "channel": channel,
                            "sender": data.get("sender", ""),
                            "type": "channel",
                        },
                    )
                ],
            )
        return key

    def get_channel_messages(self, channel: str) -> list[dict]:
        """Get all messages for a channel, ordered."""
        rows = read_prefix(self.channels, f"{channel}:m", what="channel message")
        return [value for _, value in rows]

    def next_subagent_run(self, parent: str, name: str) -> int:
        """Get the next run index for a sub-agent."""
        sa_key = f"{parent}:{name}"
        run = self._subagent_runs.get(sa_key, 0)
        self._subagent_runs[sa_key] = run + 1
        return run

    def save_subagent(
        self,
        parent: str,
        name: str,
        run: int,
        meta: dict,
        conv_json: str | None = None,
    ) -> None:
        """Save sub-agent run metadata and optional conversation."""
        prefix = f"{parent}:{name}:{run}"
        if "ts" not in meta:
            meta["ts"] = time.time()
        self.subagents[f"{prefix}:meta"] = meta
        if conv_json is not None:
            self.subagents[f"{prefix}:conversation"] = conv_json

    def load_subagent_meta(self, parent: str, name: str, run: int) -> dict | None:
        """Load sub-agent run metadata."""
        try:
            return self.subagents[f"{parent}:{name}:{run}:meta"]
        except KeyError:
            return None

    def load_subagent_conversation(
        self, parent: str, name: str, run: int
    ) -> str | None:
        """Load sub-agent conversation JSON."""
        try:
            val = self.subagents[f"{parent}:{name}:{run}:conversation"]
            return val.decode() if isinstance(val, bytes) else val
        except KeyError:
            return None

    def list_subagent_runs(
        self, *, parent: str | None = None, name: str | None = None
    ) -> list[dict[str, Any]]:
        """Return persisted sub-agent run metadata in key order."""
        wanted: list[str] = []
        identities: dict[str, tuple[str, str, int]] = {}
        for key in map(key_text, scan_keys(self.subagents)):
            if not key.endswith(":meta"):
                continue
            identity = key[: -len(":meta")].rsplit(":", 2)
            if len(identity) != 3:
                continue
            entry_parent, entry_name, run_text = identity
            try:
                run = int(run_text)
            except ValueError:
                continue
            if parent is not None and entry_parent != parent:
                continue
            if name is not None and entry_name != name:
                continue
            wanted.append(key)
            identities[key] = (entry_parent, entry_name, run)
        rows: list[dict[str, Any]] = []
        for key, meta in read_values(self.subagents, wanted, what="sub-agent run"):
            entry_parent, entry_name, run = identities[key]
            rows.append(
                {
                    "parent": entry_parent,
                    "name": entry_name,
                    "run": run,
                    "job_id": meta.get("job_id") if isinstance(meta, dict) else None,
                    "meta": meta,
                }
            )
        return rows

    def find_subagent_run(
        self, job_id: str, *, parent: str | None = None
    ) -> dict[str, Any] | None:
        """Return the persisted run carrying an exact job identifier."""
        if not job_id:
            return None
        for row in self.list_subagent_runs(parent=parent):
            if row.get("job_id") == job_id:
                return row
        return None

    def save_job(self, job_id: str, data: dict) -> None:
        """Save a job execution record."""
        if "ts" not in data:
            data["ts"] = time.time()
        self.jobs[job_id] = data

    def load_job(self, job_id: str) -> dict | None:
        """Load a job record."""
        try:
            return self.jobs[job_id]
        except KeyError:
            return None

    def init_meta(
        self,
        session_id: str,
        config_type: str,
        config_path: str,
        pwd: str,
        agents: list[str],
        config_snapshot: dict | None = None,
        terrarium_name: str | None = None,
        terrarium_channels: list[dict] | None = None,
        terrarium_creatures: list[dict] | None = None,
    ) -> None:
        """Initialize session metadata. Called once when session is created.

        ``config_type`` must be a supported resume discriminator.
        """
        if config_type not in ("agent", "terrarium"):
            raise ValueError(
                f"config_type must be 'agent' or 'terrarium', got "
                f"{config_type!r} — resume dispatches on this value"
            )

        now = datetime.now(timezone.utc).isoformat()

        self.meta["session_id"] = session_id
        self.meta["format_version"] = FORMAT_VERSION
        self.meta["config_type"] = config_type
        self.meta["config_path"] = config_path
        self.meta["config_snapshot"] = config_snapshot or {}
        self.meta["pwd"] = pwd
        self.meta["created_at"] = now
        self.meta["last_active"] = now
        self.meta["status"] = "running"
        self.meta["conversation_open"] = True
        if not self.meta.get("conversation_id"):
            self.meta["conversation_id"] = new_conversation_id()
        self.meta["agents"] = agents
        self.meta["hostname"] = platform.node()
        self.meta["python_version"] = platform.python_version()

        if terrarium_name:
            self.meta["terrarium_name"] = terrarium_name
        if terrarium_channels:
            self.meta["terrarium_channels"] = terrarium_channels
        if terrarium_creatures:
            self.meta["terrarium_creatures"] = terrarium_creatures

    def update_status(self, status: str) -> None:
        """Update session status (running, paused, completed, crashed)."""

        self.meta["status"] = status
        self.meta["last_active"] = datetime.now(timezone.utc).isoformat()

    def set_conversation_open(self, is_open: bool) -> None:
        """Persist whether the user still considers this conversation open."""

        self.meta["conversation_open"] = bool(is_open)
        if is_open:
            self.ensure_conversation_id()

    def ensure_conversation_id(self) -> str | None:
        """Return the immutable persisted identity for a marked conversation.

        Legacy files are backfilled only when they carry the explicit open
        marker. Their path-derived UUID is deterministic across retries.
        """
        existing = self.meta.get("conversation_id")
        if isinstance(existing, str) and existing:
            return existing
        if not self.meta.get("conversation_open"):
            return None
        conversation_id = legacy_conversation_id(self._path)
        self.meta["conversation_id"] = conversation_id
        return conversation_id

    def set_viewer_default_agent(self, namespace: str) -> None:
        """Record ``namespace`` as the session viewer's default agent.

        The most recent attachment wins. The value remains separate from
        ``meta["agents"]`` so main-creature discovery and resume are unaffected.
        """

        if not isinstance(namespace, str) or not namespace:
            return
        self.meta["viewer_default_agent"] = namespace

    def touch(self) -> None:
        """Update last_active timestamp."""

        self.meta["last_active"] = datetime.now(timezone.utc).isoformat()

    def load_meta(self) -> dict[str, Any]:
        """Load all metadata as a dict.

        Event namespaces augment ``meta["agents"]`` so hot-plugged creatures are
        visible to resume even when metadata was not updated explicitly.
        """
        result = {
            key_text(key): value
            for key, value in read_prefix(self.meta, what="meta key")
        }
        known = list(result.get("agents") or [])
        discovered = self.discover_agents_from_events()
        for name in discovered:
            if name not in known:
                known.append(name)
        result["agents"] = known
        return result

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

    def search(self, query: str, k: int = 10) -> list[dict]:
        """Search session content via FTS5 (BM25 keyword search).

        Queued FTS rows commit first. Returns dicts with score and metadata.
        """
        results = []
        try:
            self._flush_events_cache()
            fts = self.fts
            if fts is None:
                fts = open_read_text(Path(self._path), "fts", snapshot=False)
            if fts is None:
                return results
            try:
                for doc_id, score, meta in fts.search(query, k=k):
                    results.append({"doc_id": doc_id, "score": score, "meta": meta})
            finally:
                if fts is not self.fts:
                    dispose(fts)
        except Exception as e:
            logger.warning("FTS search failed", error=str(e))
        return results

    @property
    def path(self) -> str:
        """Path to the .kohakutr file."""
        return self._path

    def flush(self) -> None:
        """Commit buffered events and FTS rows."""
        self._flush_events_cache()

    def checkpoint(self) -> None:
        """Commit buffered rows and checkpoint the WAL — without closing.

        Raw file copies omit cached and WAL-only rows. Call this before copying
        bytes from an open store so metadata and recent events are complete.
        """
        try:
            self._flush_events_cache()
        except Exception:  # pragma: no cover - the checkpoint still runs
            logger.warning("checkpoint: flush failed", exc_info=True)
        # One WAL serves every table of the file.
        try:
            self.events.checkpoint()
        except Exception:  # pragma: no cover - checkpoint is best effort
            logger.warning("checkpoint: WAL checkpoint failed", exc_info=True)

    @classmethod
    def open_readonly(cls, path: "str | Path") -> "SessionStore":
        """Open an existing session read-only: no writes, no sidecars on close.

        Listing and viewer consumers use this mode to preserve recency.
        """
        return cls(path, read_only=True)

    def close(self, update_status: bool = True) -> None:
        """Idempotently flush and close the store and companion resources.

        Writable stores optionally transition to paused; read-only stores never
        mutate metadata.
        """
        try:
            if not self._begin_close():
                return
        except BaseException:
            if self._closed:
                self._finish_resource_lease(False)
            raise
        try:
            if self._readonly:
                update_status = False
            if self._flush is not None:
                # The clean-close counter is written only once every row is committed.
                try:
                    self._flush.close()
                    persist_event_counter(
                        self.state, self.global_max_event_id, self._reserved_event_id
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to commit buffered events on close",
                        error=str(e),
                        exc_info=True,
                    )
            if update_status:
                try:
                    self.update_status("paused")
                except Exception as e:
                    logger.warning(
                        "Failed to update session status on close",
                        error=str(e),
                        exc_info=True,
                    )
        except BaseException as primary:
            try:
                self._close_enrolled_resources()
            except BaseException as cleanup:
                raise primary from cleanup
            raise
        else:
            self._close_enrolled_resources()

    def fork(
        self,
        target_path: str,
        *,
        at_event_id: int,
        mutate: Callable[[dict], dict] | None = None,
        name: str | None = None,
        pending_job_ids: set[str] | None = None,
    ) -> "SessionStore":
        """Create an independent child session through ``at_event_id``."""
        return perform_fork(
            self,
            target_path,
            at_event_id=at_event_id,
            mutate=mutate,
            name=name,
            pending_job_ids=pending_job_ids,
        )

    def token_usage(
        self,
        agent: str | None = None,
        *,
        include_subagents: bool = False,
        include_attached: bool = False,
        by_turn: bool = False,
    ) -> dict[str, Any]:
        """Return token counters for ``agent``.

        The agent namespace is required. Optional mappings expose sub-agent and
        attached-agent counters, while ``by_turn`` uses rollups with event-based
        fallback.
        """
        return _token_usage_impl(
            self,
            agent,
            include_subagents=include_subagents,
            include_attached=include_attached,
            by_turn=by_turn,
        )

    def token_usage_all_loops(self) -> list[tuple[str, dict[str, int]]]:
        """Flat enumeration of every controller loop in the session.

        Paths identify main, sub-agent, and attached-agent loops. Each usage
        dictionary contains only that loop's counters, without aggregation.
        """
        return _token_usage_all_loops_impl(self)

    def __repr__(self) -> str:
        return f"SessionStore({self._path!r})"
