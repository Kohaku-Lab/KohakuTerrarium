"""
SessionStore - persistent session storage backed by KohakuVault.

Single .kohakutr file (SQLite) containing:
  - meta:       Session metadata, config snapshots
  - state:      Per-agent scratchpad, counters, token usage
  - events:     Append-only ordered event log (everything)
  - channels:   Channel message history
  - subagents:  Sub-agent conversation snapshots
  - jobs:       Tool/sub-agent job execution records
  - conversation: Per-agent conversation snapshots (for fast resume)
  - fts:        Full-text search index (TextVault)
"""

import json
import platform
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kohakuvault import KVault, TextVault

from kohakuterrarium.session.artifacts import artifacts_dir_for, write_artifact_bytes
from kohakuterrarium.session.history import normalize_resumable_events
from kohakuterrarium.session.rollup import (
    get_turn_rollup,
    list_turn_rollups,
    save_turn_rollup,
)
from kohakuterrarium.session.store_counters import (
    restore_event_counters,
    restore_subagent_counters,
    restore_suffix_counters,
)
from kohakuterrarium.session.store_fork import perform_fork
from kohakuterrarium.session.token_views import (
    token_usage as _token_usage_impl,
    token_usage_all_loops as _token_usage_all_loops_impl,
)
from kohakuterrarium.session.version import FORMAT_VERSION
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SessionStore:
    """Persistent session storage backed by KohakuVault.

    Tables (msgpack auto-pack):
      - meta: session_id, config_type, config_path, …
      - state: ``{agent}:scratchpad``, ``{agent}:turn_count``, …
      - events: ``{agent}:e{seq:06d}`` (append-only event log)
      - channels: ``{channel}:m{seq:06d}``
      - subagents: ``{parent}:{name}:{run}:meta`` + ``:conversation``
      - jobs: ``{job_id}``
      - conversation: ``{agent}`` (snapshot cache — see history.replay)
      - turn_rollup: ``{agent}:turn:{turn_index:06d}`` (Wave B §3.3)
      - fts: TextVault-backed FTS5 index
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)

        # Core tables
        self.meta = KVault(self._path, table="meta")
        self.meta.enable_auto_pack()
        self.state = KVault(self._path, table="state")
        self.state.enable_auto_pack()
        self.events = KVault(self._path, table="events")
        self.events.enable_auto_pack()
        self.events.enable_cache(flush_interval=2.0)
        self.channels = KVault(self._path, table="channels")
        self.channels.enable_auto_pack()
        self.subagents = KVault(self._path, table="subagents")
        self.subagents.enable_auto_pack()
        self.jobs = KVault(self._path, table="jobs")
        self.jobs.enable_auto_pack()
        self.conversation = KVault(self._path, table="conversation")
        self.conversation.enable_auto_pack()
        # Per-turn rollup table (Wave B §3.3) — denormalised per-turn
        # counters, cheap for viewers without reducing the event log.
        self.turn_rollup = KVault(self._path, table="turn_rollup")
        self.turn_rollup.enable_auto_pack()
        # FTS for search
        self.fts = TextVault(self._path, table="fts")
        self.fts.enable_auto_pack()

        # Sequence counters + Wave B global monotonic event id
        self._event_seq: dict[str, int] = {}
        self._channel_seq: dict[str, int] = {}
        self._subagent_runs: dict[str, int] = {}
        self._global_event_id: int = 0

        self._restore_counters()
        # Artifacts directory — created lazily.
        self._artifacts_dir: Path | None = None

        logger.debug("SessionStore opened", path=self._path)

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

    def _restore_counters(self) -> None:
        """Scan existing keys to restore sequence counters after restart."""
        self._global_event_id = restore_event_counters(self.events, self._event_seq)
        restore_suffix_counters(self.channels, ":m", self._channel_seq)
        restore_subagent_counters(self.subagents, self._subagent_runs)

        if self._event_seq or self._channel_seq:
            logger.debug(
                "Counters restored",
                event_agents=list(self._event_seq.keys()),
                channel_count=len(self._channel_seq),
            )

    # ─── Event Log ──────────────────────────────────────────────────

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

        ``turn_index`` identifies the user-input turn the event belongs
        to (stable across regenerate / edit+rerun). ``branch_id`` is
        which branch of that turn (1 = original, 2 = first regen, …).
        Events of the same ``turn_index`` but different ``branch_id``
        are siblings — replay default picks the latest branch; the
        ``<1/N>`` navigator surfaces the others.
        """
        seq = self._next_event_seq(agent)
        key = f"{agent}:e{seq:06d}"
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
        # Stamp parent_branch_path so nested-branch filtering can run
        # without scanning event history at replay time. Stored as a
        # JSON-friendly list-of-pairs.
        if parent_branch_path is not None:
            data["parent_branch_path"] = [list(p) for p in parent_branch_path]
        if "ts" not in data:
            data["ts"] = time.time()
        self.events[key] = data

        # Index searchable text in FTS
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
                logger.debug("FTS indexing failed", error=str(e), exc_info=True)

        return key, event_id

    def get_events(self, agent: str) -> list[dict]:
        """Get all events for an agent, ordered by sequence.

        Returns list of event dicts with keys sorted chronologically.
        """
        self.events.flush_cache()
        prefix = f"{agent}:e"
        result = []
        for key_bytes in sorted(self.events.keys(prefix=prefix)):
            try:
                result.append(self.events[key_bytes])
            except Exception as e:
                logger.debug("Failed to read event", error=str(e), exc_info=True)
        return result

    def get_resumable_events(self, agent: str) -> list[dict]:
        """Get agent events normalized for resume/history replay."""
        return normalize_resumable_events(self.get_events(agent))

    def get_all_events(self) -> list[tuple[str, dict]]:
        """Get ALL events across all agents, sorted by timestamp.

        Returns list of (key, event_dict) tuples.
        """
        self.events.flush_cache()
        all_events = []
        for key_bytes in self.events.keys():
            key = (
                key_bytes.decode("utf-8", errors="replace")
                if isinstance(key_bytes, bytes)
                else key_bytes
            )
            try:
                evt = self.events[key_bytes]
                all_events.append((key, evt))
            except Exception as e:
                logger.debug(
                    "Failed to read event in get_all_events",
                    error=str(e),
                    exc_info=True,
                )
        all_events.sort(key=lambda x: x[1].get("ts", 0))
        return all_events

    # ─── Conversation Snapshots ─────────────────────────────────────

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
            # msgpack auto-decode returns list directly
            if isinstance(val, list):
                return val
            # Legacy: JSON string from older sessions
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

    # ─── Per-Agent State ────────────────────────────────────────────

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

    # ─── Per-Turn Rollup (Wave B §3.3) ──────────────────────────────

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

    # ─── Channel Messages ───────────────────────────────────────────

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

        # FTS index
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
                logger.debug(
                    "FTS indexing channel message failed", error=str(e), exc_info=True
                )

        return key

    def get_channel_messages(self, channel: str) -> list[dict]:
        """Get all messages for a channel, ordered."""
        prefix = f"{channel}:m"
        result = []
        for key_bytes in sorted(self.channels.keys(prefix=prefix)):
            try:
                result.append(self.channels[key_bytes])
            except Exception as e:
                logger.debug(
                    "Failed to read channel message", error=str(e), exc_info=True
                )
        return result

    # ─── Sub-Agent Conversations ────────────────────────────────────

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

    # ─── Job Records ────────────────────────────────────────────────

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

    # ─── Meta ───────────────────────────────────────────────────────

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
        """Initialize session metadata. Called once when session is created."""

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

    def touch(self) -> None:
        """Update last_active timestamp."""

        self.meta["last_active"] = datetime.now(timezone.utc).isoformat()

    def load_meta(self) -> dict[str, Any]:
        """Load all metadata as a dict.

        Wave B: ``meta["agents"]`` is augmented with any agent names
        discovered by scanning event-key prefixes. Hot-plugged creatures
        (whose writes land directly via ``append_event`` without going
        through ``add_creature``) become visible to resume.
        """
        result = {}
        for key_bytes in self.meta.keys():
            key = (
                key_bytes.decode("utf-8", errors="replace")
                if isinstance(key_bytes, bytes)
                else key_bytes
            )
            try:
                result[key] = self.meta[key_bytes]
            except Exception as e:
                logger.debug("Failed to read meta key", error=str(e), exc_info=True)
        known = list(result.get("agents") or [])
        discovered = self.discover_agents_from_events()
        for name in discovered:
            if name not in known:
                known.append(name)
        result["agents"] = known
        return result

    def discover_agents_from_events(self) -> list[str]:
        """Scan event keys, returning agent names in first-seen order.

        Excludes framework-scope names like ``terrarium`` and Wave F
        attached-agent namespaces (``<host>:attached:<role>:<seq>``) —
        the latter are exposed separately via
        :meth:`discover_attached_agents` so ``resume_terrarium`` does
        not try to rebuild them as standalone creatures.
        """
        seen: list[str] = []
        excluded = {"terrarium"}
        for key_bytes in self.events.keys():
            key = (
                key_bytes.decode("utf-8", errors="replace")
                if isinstance(key_bytes, bytes)
                else key_bytes
            )
            parts = key.rsplit(":e", 1)
            if len(parts) != 2:
                continue
            agent = parts[0]
            # Wave F: skip attached namespaces here.
            if ":attached:" in agent:
                continue
            if agent not in excluded and agent not in seen:
                seen.append(agent)
        return seen

    def discover_attached_agents(self) -> list[dict[str, Any]]:
        """Return Wave F attached-agent namespaces discovered in ``events``.

        Each entry is ``{"host": ..., "role": ..., "attach_seq": ...,
        "namespace": "<host>:attached:<role>:<seq>"}``. Ordering is
        first-seen (matches :meth:`discover_agents_from_events`).
        """
        seen: dict[str, dict[str, Any]] = {}
        for key_bytes in self.events.keys():
            key = (
                key_bytes.decode("utf-8", errors="replace")
                if isinstance(key_bytes, bytes)
                else key_bytes
            )
            parts = key.rsplit(":e", 1)
            if len(parts) != 2:
                continue
            ns = parts[0]
            # Expected shape: ``<host>:attached:<role>:<attach_seq>``.
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

    # ─── Search ─────────────────────────────────────────────────────

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

    # ─── Lifecycle ──────────────────────────────────────────────────

    @property
    def path(self) -> str:
        """Path to the .kohakutr file."""
        return self._path

    def flush(self) -> None:
        """Flush all caches to disk."""
        self.events.flush_cache()

    def close(self, update_status: bool = True) -> None:
        """Flush and close all tables.

        Args:
            update_status: If True (default), mark session as paused and
                update last_active. Set False for read-only access (e.g.,
                listing sessions) to avoid corrupting timestamps.
        """
        if update_status:
            try:
                self.update_status("paused")
            except Exception as e:
                logger.debug(
                    "Failed to update session status on close",
                    error=str(e),
                    exc_info=True,
                )
        self.events.close()
        self.meta.close()
        self.state.close()
        self.channels.close()
        self.subagents.close()
        self.jobs.close()
        self.conversation.close()
        self.turn_rollup.close()
        logger.debug("SessionStore closed", path=self._path)

    # ─── Fork / Branch (Wave E) ─────────────────────────────────────

    def fork(
        self,
        target_path: str,
        *,
        at_event_id: int,
        mutate: Callable[[dict], dict] | None = None,
        name: str | None = None,
        pending_job_ids: set[str] | None = None,
    ) -> "SessionStore":
        """Copy-on-fork delegating to :mod:`session.store_fork`."""
        return perform_fork(
            self,
            target_path,
            at_event_id=at_event_id,
            mutate=mutate,
            name=name,
            pending_job_ids=pending_job_ids,
        )

    # ─── Token-usage read API (Wave G) ──────────────────────────────

    def token_usage(
        self,
        agent: str | None = None,
        *,
        include_subagents: bool = False,
        include_attached: bool = False,
        by_turn: bool = False,
    ) -> dict[str, Any]:
        """Return token counters for ``agent``.

        ``agent`` — controller-loop namespace to read. ``None`` raises
        (Q2 in ``plans/session-system/implementation-plan.md``: no
        silent "main" pick).

        ``include_subagents`` — when ``True``, adds a ``"subagents"``
        sub-dict keyed by ``<agent>:subagent:<name>:<run>``. Sub-agent
        tokens are recovered from the parent's ``subagent_result``
        events (matched against the authoritative ``subagents`` KVault
        table).

        ``include_attached`` — when ``True``, adds an ``"attached"``
        sub-dict keyed by ``<host>:attached:<role>:<attach_seq>``
        (discovered via :meth:`discover_attached_agents`). Missing keys
        become empty dicts, never absent.

        ``by_turn`` — when ``True``, adds a ``"by_turn"`` list of
        ``{turn_index, prompt, completion, cached}`` rows. Reads the
        ``turn_rollup`` table first; falls back to walking
        ``token_usage`` events grouped by ``turn_index`` when the
        rollup emitter has not fired for this agent yet.
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

        Returns ``[(agent_path, usage_dict)]`` where ``agent_path`` is:

        * ``<host>`` for each main agent,
        * ``<host>:subagent:<name>:<run>`` for a tracked sub-agent,
        * ``<host>:attached:<role>:<attach_seq>`` for an attached agent.

        Each ``usage_dict`` is that loop's own counters only (no
        aggregation). Consumers display / sum as they see fit.
        """
        return _token_usage_all_loops_impl(self)

    def __repr__(self) -> str:
        return f"SessionStore({self._path!r})"
