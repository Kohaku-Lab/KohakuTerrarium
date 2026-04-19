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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kohakuvault import KVault, TextVault

from kohakuterrarium.session.history import normalize_resumable_events
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SessionStore:
    """Persistent session storage backed by KohakuVault.

    All KVault tables use auto-pack (msgpack for dicts/lists, utf8 for strings).
    Keys are strings, values are Python objects (auto-encoded).

    Key schemas per table:
      meta:         "session_id", "config_type", "config_path", ...
      state:        "{agent}:scratchpad", "{agent}:turn_count", ...
      events:       "{agent}:e{seq:06d}"
      channels:     "{channel}:m{seq:06d}"
      subagents:    "{parent}:{name}:{run}:meta", "{parent}:{name}:{run}:conversation"
      jobs:         "{job_id}"
      conversation: "{agent}"
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)

        # Ensure parent directory exists
        parent = Path(self._path).parent
        parent.mkdir(parents=True, exist_ok=True)

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

        # FTS for search
        self.fts = TextVault(self._path, table="fts")
        self.fts.enable_auto_pack()

        # Sequence counters (per-agent event, per-channel message)
        self._event_seq: dict[str, int] = {}
        self._channel_seq: dict[str, int] = {}
        self._subagent_runs: dict[str, int] = {}

        # Restore counters from existing data
        self._restore_counters()

        logger.debug("SessionStore opened", path=self._path)

    def _restore_counters(self) -> None:
        """Scan existing keys to restore sequence counters after restart."""
        # Event counters
        for key_bytes in self.events.keys():
            key = (
                key_bytes.decode("utf-8", errors="replace")
                if isinstance(key_bytes, bytes)
                else key_bytes
            )
            # Format: "{agent}:e{seq:06d}"
            parts = key.rsplit(":e", 1)
            if len(parts) == 2:
                agent = parts[0]
                try:
                    seq = int(parts[1])
                    if agent not in self._event_seq or seq >= self._event_seq[agent]:
                        self._event_seq[agent] = seq + 1
                except ValueError:
                    pass

        # Channel counters
        for key_bytes in self.channels.keys():
            key = (
                key_bytes.decode("utf-8", errors="replace")
                if isinstance(key_bytes, bytes)
                else key_bytes
            )
            # Format: "{channel}:m{seq:06d}"
            parts = key.rsplit(":m", 1)
            if len(parts) == 2:
                channel = parts[0]
                try:
                    seq = int(parts[1])
                    if (
                        channel not in self._channel_seq
                        or seq >= self._channel_seq[channel]
                    ):
                        self._channel_seq[channel] = seq + 1
                except ValueError:
                    pass

        # Sub-agent run counters
        for key_bytes in self.subagents.keys():
            key = (
                key_bytes.decode("utf-8", errors="replace")
                if isinstance(key_bytes, bytes)
                else key_bytes
            )
            # Format: "{parent}:{name}:{run}:meta" or "...:conversation"
            if key.endswith(":meta"):
                parts = key[: -len(":meta")].rsplit(":", 2)
                if len(parts) == 3:
                    parent, name, run_str = parts
                    sa_key = f"{parent}:{name}"
                    try:
                        run = int(run_str)
                        if (
                            sa_key not in self._subagent_runs
                            or run >= self._subagent_runs[sa_key]
                        ):
                            self._subagent_runs[sa_key] = run + 1
                    except ValueError:
                        pass

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

    def append_event(self, agent: str, event_type: str, data: dict) -> str:
        """Append one event to the log. Returns the event key.

        Args:
            agent: Agent name (root, swe, reviewer, etc.)
            event_type: Event type (user_input, text, tool_call, etc.)
            data: Event payload dict (auto-packed via msgpack)
        """
        seq = self._next_event_seq(agent)
        key = f"{agent}:e{seq:06d}"
        data["type"] = event_type
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
                    },
                )
            except Exception as e:
                logger.debug("FTS indexing failed", error=str(e), exc_info=True)

        return key

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
        self.meta["format_version"] = 1
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
        """Load all metadata as a dict."""
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
        return result

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
        logger.debug("SessionStore closed", path=self._path)

    def __repr__(self) -> str:
        return f"SessionStore({self._path!r})"
