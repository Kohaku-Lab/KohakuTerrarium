"""Persist session metadata, events, state, and indexes in KohakuVault."""

import json
import platform
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kohakuvault import KVault, TextVault

from kohakuterrarium.session.artifacts import artifacts_dir_for, write_artifact_bytes
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
from kohakuterrarium.session.store_counters import (
    persist_event_counter,
    restore_event_counters,
    restore_subagent_counters,
    restore_suffix_counters,
)
from kohakuterrarium.session.store_fork import perform_fork
from kohakuterrarium.session.store_lock import (
    acquire_writer_lock,
    close_tables,
    release_writer_lock,
)
from kohakuterrarium.session.token_views import (
    token_usage as _token_usage_impl,
    token_usage_all_loops as _token_usage_all_loops_impl,
)
from kohakuterrarium.session.version import FORMAT_VERSION
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# KVault defaults to 10,000 keys, which can silently truncate long sessions.
_KV_KEYS_LIMIT: int = 2**31 - 1


def iter_kv_keys(
    table: Any,
    *,
    prefix: Any = None,
    limit: int = _KV_KEYS_LIMIT,
) -> Iterable[Any]:
    """Iterate every key of a KVault table, bypassing its 10k default.

    ``prefix`` filters the result, and an explicit ``limit`` can override the
    large session-safe default.
    """
    if prefix is None:
        return table.keys(limit=limit)
    return table.keys(prefix=prefix, limit=limit)


class SessionStore:
    """Persistent session storage backed by KohakuVault.

    One ``.kohakutr`` file contains metadata, per-agent state, append-only
    events, channel history, child-run records, conversation caches, turn
    rollups, job records, and full-text search data.
    """

    # Flush on either explicit threshold; KVault's interval remains a fallback.
    DEFAULT_FLUSH_EVERY_N_EVENTS = 4
    DEFAULT_FLUSH_EVERY_N_SECONDS = 1.0

    def __init__(
        self,
        path: str | Path,
        *,
        flush_every_n_events: int | None = None,
        flush_every_n_seconds: float | None = None,
        writer_lock: bool = False,
    ) -> None:
        self._path = str(Path(path).expanduser())
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # Read-only consumers must not alter status or recency on close.
        self._readonly = False

        # The optional cross-process writer lock is released during close.
        self._writer_lock = acquire_writer_lock(self._path) if writer_lock else None

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
        self._unflushed_event_count: int = 0
        self._last_flush_at: float = time.monotonic()

        # Per-namespace sequences coexist with one session-wide event identifier.
        self._event_seq: dict[str, int] = {}
        self._channel_seq: dict[str, int] = {}
        self._subagent_runs: dict[str, int] = {}
        self._global_event_id: int = 0
        # Artifact storage is created only on first use.
        self._artifacts_dir: Path | None = None
        # Companion resources close in LIFO order before the store lock is released.
        self._companion_closers: list[Callable[[], None]] = []
        # Subscribers observe events only after persistence and FTS indexing.
        self._event_subscribers: list[Callable[[str, dict], None]] = []

        # Construction failures must not strand the cross-process writer lock.
        try:
            self._open_tables()
            self._restore_counters()
        except BaseException:
            release_writer_lock(self._writer_lock)
            self._writer_lock = None
            raise

        logger.debug("SessionStore opened", path=self._path)

    def _open_tables(self) -> None:
        """Open every KVault / TextVault table backing this store."""
        self.meta = KVault(self._path, table="meta")
        self.meta.enable_auto_pack()
        self.state = KVault(self._path, table="state")
        self.state.enable_auto_pack()
        self.events = KVault(self._path, table="events")
        self.events.enable_auto_pack()
        # The passive interval backs up the explicit append-time flush gates.
        self.events.enable_cache(flush_interval=2.0)
        self.channels = KVault(self._path, table="channels")
        self.channels.enable_auto_pack()
        self.subagents = KVault(self._path, table="subagents")
        self.subagents.enable_auto_pack()
        self.jobs = KVault(self._path, table="jobs")
        self.jobs.enable_auto_pack()
        self.conversation = KVault(self._path, table="conversation")
        self.conversation.enable_auto_pack()
        # Denormalized turn rows accelerate viewers without replacing source events.
        self.turn_rollup = KVault(self._path, table="turn_rollup")
        self.turn_rollup.enable_auto_pack()
        self.fts = TextVault(self._path, table="fts")
        self.fts.enable_auto_pack()

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
        """Restore sequence counters; persisted counters skip the value scan."""
        self._global_event_id = restore_event_counters(
            self.events, self._event_seq, state=self.state
        )
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

    def _next_global_event_id(self) -> int:
        """Get and increment the global monotonic event id counter."""
        self._global_event_id += 1
        return self._global_event_id

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
        seq = self._next_event_seq(agent)
        key = f"{agent}:e{seq:06d}"
        # Mirrors preserve source event identifiers for consistent cross-node cursors.
        preset_eid = data.get("event_id")
        if isinstance(preset_eid, int) and preset_eid > 0:
            event_id = preset_eid
            # Future local identifiers must remain above imported identifiers.
            if event_id > self._global_event_id:
                self._global_event_id = event_id
        else:
            event_id = self._next_global_event_id()
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
        self.events[key] = data

        text = data.get("content") or data.get("output") or data.get("text") or ""
        if isinstance(text, str) and len(text) > 10:
            try:
                self.fts.insert(
                    text,
                    {
                        "event_key": key,
                        "agent": agent,
                        "type": event_type,
                        "event_id": event_id,
                    },
                )
            except Exception as e:
                logger.warning("FTS indexing failed", error=str(e), exc_info=True)

        # Subscriber failures cannot invalidate a successfully appended event.
        for cb in tuple(self._event_subscribers):
            try:
                cb(key, data)
            except Exception as e:
                logger.warning("Event subscriber failed", error=str(e), exc_info=True)

        # Either durability threshold may trigger a cache flush.
        self._unflushed_event_count += 1
        self._maybe_flush_events()

        if not self._readonly:
            persist_event_counter(self.state, event_id)

        return key, event_id

    def _flush_events_cache(self) -> None:
        """Flush pending events and reset the durability counters."""
        self.events.flush_cache()
        self._unflushed_event_count = 0
        self._last_flush_at = time.monotonic()

    def _maybe_flush_events(self) -> None:
        """Flush the events cache when either durability gate trips."""
        n_gate = (
            self._flush_every_n_events > 0
            and self._unflushed_event_count >= self._flush_every_n_events
        )
        t_gate = (
            self._flush_every_n_seconds > 0
            and (time.monotonic() - self._last_flush_at) >= self._flush_every_n_seconds
        )
        if not (n_gate or t_gate):
            return
        try:
            self.events.flush_cache()
        except Exception as e:
            logger.warning("Events flush_cache failed", error=str(e), exc_info=True)
            return
        self._unflushed_event_count = 0
        self._last_flush_at = time.monotonic()

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
        self._flush_events_cache()
        prefix = f"{agent}:e"
        result = []
        for key_bytes in sorted(iter_kv_keys(self.events, prefix=prefix)):
            try:
                evt = self.events[key_bytes]
            except Exception as e:
                logger.warning("Failed to read event", error=str(e), exc_info=True)
                continue
            if since_event_id is not None:
                eid = evt.get("event_id") if isinstance(evt, dict) else None
                if not isinstance(eid, int) or eid <= since_event_id:
                    continue
            result.append(evt)
        return result

    def max_event_id(self, agent: str) -> int:
        """Return the session-global max ``event_id`` (monotonic, per-session).

        Local events take ids from ``_global_event_id`` and mirrored events
        ratchet it upward, so the counter is the authoritative maximum and
        this is O(1). ``event_id`` is a session-global (not per-agent) cursor,
        so ``agent`` is accepted for API symmetry with ``get_events`` and
        intentionally unused.
        """
        del agent
        return self._global_event_id

    def get_event_by_id(self, agent: str, event_id: int) -> dict | None:
        """Fetch a single event by its session-global ``event_id``.

        Scans the agent's keys but reads only values until the id matches —
        the lazy companion to ``output_preview``-bounded history payloads
        (frontend expands fetch the full output on demand).
        """
        self._flush_events_cache()
        for key_bytes in iter_kv_keys(self.events, prefix=f"{agent}:e"):
            try:
                evt = self.events[key_bytes]
            except Exception as e:
                logger.warning("Failed to read event", error=str(e), exc_info=True)
                continue
            if isinstance(evt, dict) and evt.get("event_id") == event_id:
                return evt
        return None

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
        all_events = []
        for key_bytes in iter_kv_keys(self.events):
            key = (
                key_bytes.decode("utf-8", errors="replace")
                if isinstance(key_bytes, bytes)
                else key_bytes
            )
            try:
                evt = self.events[key_bytes]
                all_events.append((key, evt))
            except Exception as e:
                logger.warning(
                    "Failed to read event in get_all_events",
                    error=str(e),
                    exc_info=True,
                )
        all_events.sort(key=lambda x: x[1].get("ts", 0))
        return all_events

    def save_conversation(self, agent: str, messages: list[dict] | str) -> None:
        """Save a conversation snapshot (overwritten each time).

        Accepts either a list of message dicts (preferred, stored via msgpack)
        or a JSON string (legacy, stored as-is).
        """
        self.conversation[agent] = messages

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
        if isinstance(content, str) and len(content) > 10:
            try:
                self.fts.insert(
                    content,
                    {
                        "channel_key": key,
                        "channel": channel,
                        "sender": data.get("sender", ""),
                        "type": "channel",
                    },
                )
            except Exception as e:
                logger.warning(
                    "FTS indexing channel message failed", error=str(e), exc_info=True
                )

        return key

    def get_channel_messages(self, channel: str) -> list[dict]:
        """Get all messages for a channel, ordered."""
        prefix = f"{channel}:m"
        result = []
        for key_bytes in sorted(iter_kv_keys(self.channels, prefix=prefix)):
            try:
                result.append(self.channels[key_bytes])
            except Exception as e:
                logger.warning(
                    "Failed to read channel message", error=str(e), exc_info=True
                )
        return result

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
        rows: list[dict[str, Any]] = []
        for key_bytes in sorted(iter_kv_keys(self.subagents)):
            key = (
                key_bytes.decode("utf-8", errors="replace")
                if isinstance(key_bytes, bytes)
                else key_bytes
            )
            if not key.endswith(":meta"):
                continue
            prefix = key[: -len(":meta")]
            identity = prefix.rsplit(":", 2)
            if len(identity) != 3:
                continue
            entry_parent, entry_name, run_text = identity
            try:
                run = int(run_text)
                meta = self.subagents[key_bytes]
            except (KeyError, TypeError, ValueError):
                continue
            if parent is not None and entry_parent != parent:
                continue
            if name is not None and entry_name != name:
                continue
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
        result = {}
        for key_bytes in iter_kv_keys(self.meta):
            key = (
                key_bytes.decode("utf-8", errors="replace")
                if isinstance(key_bytes, bytes)
                else key_bytes
            )
            try:
                result[key] = self.meta[key_bytes]
            except Exception as e:
                logger.warning("Failed to read meta key", error=str(e), exc_info=True)
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
        # Buffered event keys must be flushed before namespace discovery.
        self.events.flush_cache()
        seen: list[str] = []
        excluded = {"terrarium"}
        for key_bytes in iter_kv_keys(self.events):
            key = (
                key_bytes.decode("utf-8", errors="replace")
                if isinstance(key_bytes, bytes)
                else key_bytes
            )
            parts = key.rsplit(":e", 1)
            if len(parts) != 2:
                continue
            agent = parts[0]
            # Attached namespaces are reported by the dedicated discovery API.
            if ":attached:" in agent:
                continue
            if agent not in excluded and agent not in seen:
                seen.append(agent)
        return seen

    def discover_attached_agents(self) -> list[dict[str, Any]]:
        """Return attached-agent namespaces from events in first-seen order."""
        # Buffered event keys must be visible before discovery.
        self.events.flush_cache()
        seen: dict[str, dict[str, Any]] = {}
        for key_bytes in iter_kv_keys(self.events):
            key = (
                key_bytes.decode("utf-8", errors="replace")
                if isinstance(key_bytes, bytes)
                else key_bytes
            )
            parts = key.rsplit(":e", 1)
            if len(parts) != 2:
                continue
            ns = parts[0]
            # Roles may contain colons, so split the sequence from the right.
            segments = ns.split(":attached:", 1)
            if len(segments) != 2:
                continue
            host = segments[0]
            remainder = segments[1]
            role_and_seq = remainder.rsplit(":", 1)
            if len(role_and_seq) != 2:
                continue
            role, seq_str = role_and_seq
            try:
                attach_seq = int(seq_str)
            except ValueError:
                continue
            if ns in seen:
                continue
            seen[ns] = {
                "host": host,
                "role": role,
                "attach_seq": attach_seq,
                "namespace": ns,
            }
        return list(seen.values())

    def search(self, query: str, k: int = 10) -> list[dict]:
        """Search session content via FTS5 (BM25 keyword search).

        Returns list of dicts with score, metadata, and the matched text.
        """
        results = []
        try:
            for doc_id, score, meta in self.fts.search(query, k=k):
                results.append({"doc_id": doc_id, "score": score, "meta": meta})
        except Exception as e:
            logger.warning("FTS search failed", error=str(e))
        return results

    @property
    def path(self) -> str:
        """Path to the .kohakutr file."""
        return self._path

    def flush(self) -> None:
        """Flush all caches to disk."""
        self.events.flush_cache()
        self._unflushed_event_count = 0
        self._last_flush_at = time.monotonic()

    def checkpoint(self) -> None:
        """Flush every table cache and checkpoint the WAL — without closing.

        Raw file copies omit cached and WAL-only rows. Call this before copying
        bytes from an open store so metadata and recent events are complete.
        """
        for table in (
            self.events,
            self.meta,
            self.state,
            self.channels,
            self.subagents,
            self.jobs,
            self.conversation,
            self.turn_rollup,
        ):
            try:
                table.flush_cache()
            except Exception:  # pragma: no cover - checkpoint continues per table
                logger.warning("checkpoint: flush_cache failed", exc_info=True)
            try:
                table.checkpoint()
            except Exception:  # pragma: no cover - checkpoint continues per table
                logger.warning("checkpoint: WAL checkpoint failed", exc_info=True)
        self._unflushed_event_count = 0
        self._last_flush_at = time.monotonic()

    @classmethod
    def open_readonly(cls, path: "str | Path") -> "SessionStore":
        """Open a store whose close operation never mutates session metadata.

        Listing and viewer consumers must use this mode to preserve recency.
        """
        store = cls(path)
        store._readonly = True
        return store

    def close(self, update_status: bool = True) -> None:
        """Idempotently flush and close the store and companion resources.

        Writable stores optionally transition to paused; read-only stores never
        mutate metadata.
        """
        if getattr(self, "_closed", False):
            return
        self._closed = True
        if self._readonly:
            update_status = False
        if not self._readonly:
            persist_event_counter(self.state, self._global_event_id)
        if update_status:
            try:
                self.update_status("paused")
            except Exception as e:
                logger.warning(
                    "Failed to update session status on close",
                    error=str(e),
                    exc_info=True,
                )
        tables = (
            self.events,
            self.meta,
            self.state,
            self.channels,
            self.subagents,
            self.jobs,
            self.conversation,
            self.turn_rollup,
        )
        # Close helpers release the writer lock even if a companion closer fails.
        close_tables(tables, self.fts, self._writer_lock, self._companion_closers)
        self._writer_lock = None
        logger.debug("SessionStore closed", path=self._path)

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
