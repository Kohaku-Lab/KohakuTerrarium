"""Saved-session list / delete / index for the persistence layer.

Verbatim port of the listing helpers and per-target history shaping
that previously lived in ``api/routes/sessions.py``. The HTTP route
files in ``api/routes/persistence/`` provide the FastAPI surface; all
filesystem and SessionStore logic lives here so the CLI and HTTP
share one implementation.
"""

import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.paths import (
    all_session_files,
    all_versions_for_session,
    normalize_session_stem,
    pick_canonical_per_session,
    resolve_session_path,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Default session directory. The HTTP route layer monkey-patches this
# in tests via ``studio.persistence.store._SESSION_DIR``; the helpers
# below also accept an explicit ``session_dir`` argument so callers
# that need full isolation (CLI tooling) can opt out of the singleton.
_SESSION_DIR = Path.home() / ".kohakuterrarium" / "sessions"


# In-memory session index (built once, refreshed on demand)
_session_index: list[dict] = []
_index_built_at: float = 0


def _session_dir() -> Path:
    """Return the live ``_SESSION_DIR`` (read fresh each call so tests
    that monkey-patch the module-level value continue to work)."""
    return _SESSION_DIR


def _max_mtime(path: Path) -> float:
    """Most-recent mtime across the session file + its SQLite sidecars.

    SQLite WAL mode writes most data to ``foo.kohakutr-wal`` and
    ``foo.kohakutr-shm`` *before* the main file is checkpointed.
    Reading only the main file's mtime would mis-order in-progress
    sessions — the canonical file may be hours behind the active wal.

    O(N) with three ``stat`` calls per session — `path` always exists
    (caller is :func:`pick_canonical_per_session`); the ``-wal`` and
    ``-shm`` sidecars are checked via :func:`os.path.exists` first and
    only stat'd when present. Returns 0 if the main file vanished
    between listdir and stat.
    """
    try:
        best = path.stat().st_mtime
    except OSError:
        return 0.0
    for sidecar_suffix in ("-wal", "-shm"):
        sidecar = str(path) + sidecar_suffix
        if not os.path.exists(sidecar):
            continue
        try:
            mt = os.stat(sidecar).st_mtime
        except OSError:
            continue
        if mt > best:
            best = mt
    return best


def _extract_text_preview(content: Any, limit: int = 200) -> str:
    """Pull display text out of an event's ``content`` field.

    Multi-modal events store ``content`` as a list of parts
    ``[{"type":"text","text":"..."}, {"type":"image_url",...}]``. We
    flatten by joining every text part with a space; non-text parts
    contribute a single ``[image]`` / ``[file]`` token so the user
    sees that *something* multi-modal exists without leaking a base64
    blob into the preview.

    String content passes through unchanged.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content[:limit]
    if isinstance(content, list):
        bits: list[str] = []
        for part in content:
            if isinstance(part, str):
                bits.append(part)
            elif isinstance(part, dict):
                kind = part.get("type") or ""
                if kind == "text":
                    bits.append(str(part.get("text") or ""))
                elif kind == "image_url" or kind == "image":
                    bits.append("[image]")
                elif kind == "file":
                    bits.append("[file]")
                else:
                    # Unknown structured part — keep a hint without
                    # dumping internals.
                    bits.append(f"[{kind or 'attachment'}]")
        return " ".join(b for b in bits if b)[:limit]
    if isinstance(content, dict):
        # Single multi-modal part stored as a dict.
        return _extract_text_preview([content], limit)
    return str(content)[:limit]


def _read_session_entry(path: Path) -> dict:
    """Open one session file, extract index payload, close. Used in pool."""
    try:
        store = SessionStore(path)
        try:
            meta = store.load_meta()

            # Read first user message for preview. Multi-modal events
            # carry ``content`` as a list of parts; flatten via
            # :func:`_extract_text_preview` so the listing surface
            # never embeds a base64 image blob.
            preview = ""
            try:
                agent_name = (meta.get("agents") or [""])[0]
                if agent_name:
                    events = store.get_resumable_events(agent_name)
                    for evt in events:
                        if evt.get("type") == "user_input":
                            preview = _extract_text_preview(evt.get("content"))
                            if preview:
                                break
            except Exception as e:
                logger.debug(
                    "Failed to read session preview", error=str(e), exc_info=True
                )

            lineage = meta.get("lineage") or {}
            forked_children = meta.get("forked_children") or []
            return {
                # Canonical name strips the version suffix so v1+v2 of
                # the same session show as one entry — and the name
                # round-trips through delete/resume cleanly.
                "name": normalize_session_stem(path),
                "filename": path.name,
                "config_type": meta.get("config_type", "unknown"),
                "config_path": meta.get("config_path", ""),
                "terrarium_name": meta.get("terrarium_name", ""),
                "agents": meta.get("agents", []),
                "status": meta.get("status", ""),
                "created_at": meta.get("created_at", ""),
                "last_active": meta.get("last_active", ""),
                "preview": preview,
                "pwd": meta.get("pwd", ""),
                "format_version": meta.get("format_version", 1),
                # Wave E lineage for the fork tree in the lister.
                "parent_session_id": (
                    (lineage.get("fork") or {}).get("parent_session_id")
                    if isinstance(lineage, dict)
                    else None
                ),
                "fork_point": (
                    (lineage.get("fork") or {}).get("fork_point")
                    if isinstance(lineage, dict)
                    else None
                ),
                "forked_children": [
                    c.get("session_id") if isinstance(c, dict) else c
                    for c in forked_children
                ],
                "migrated_from_version": (
                    lineage.get("migration", {}).get("source_version")
                    if isinstance(lineage, dict)
                    else None
                ),
            }
        finally:
            store.close(update_status=False)
    except Exception as e:
        _ = e  # corrupt session file, show as error entry
        return {
            "name": normalize_session_stem(path),
            "filename": path.name,
            "error": True,
        }


# Cap thread count: SQLite-over-network is GIL-friendly for I/O but
# a runaway pool also opens too many file handles. ``min(32, cpus*4)``
# matches Python's default ThreadPoolExecutor heuristic.
_MAX_INDEX_WORKERS = min(32, (os.cpu_count() or 4) * 4)


def build_session_index() -> list[dict]:
    """Build index of all sessions. Cached in memory.

    Two-phase:

    1. **mtime sort** — stat the session file + its ``-wal`` / ``-shm``
       sidecars to derive a true "last touched" timestamp without
       opening the database. This is O(N) tiny syscalls — fast enough
       to handle thousands of sessions in milliseconds.
    2. **Parallel reads** — open each SessionStore on a worker thread
       to read meta + first user-message preview. SQLite open + a
       handful of point queries is I/O-bound, so the GIL doesn't
       serialise the wait. Workers are capped at
       :data:`_MAX_INDEX_WORKERS`.
    """
    global _session_index, _index_built_at

    session_dir = _session_dir()
    if not session_dir.exists():
        _session_index = []
        _index_built_at = time.time()
        return _session_index

    # Wave D auto-migration leaves both ``foo.kohakutr`` (v1 rollback)
    # and ``foo.kohakutr.v2`` (live) on disk. Surface only the highest-
    # versioned file per logical session; delete/resume still reach
    # both files via ``all_versions_for_session``.
    session_files = pick_canonical_per_session(session_dir)

    # Phase 1: sort by max(file, wal, shm) mtime — newest first. This
    # is the canonical sort key for the index; we never re-sort by
    # DB-derived ``last_active`` because that would require every
    # entry's meta to be readable + parseable just to order the list.
    # Strict O(N) — three stats per session, sidecars existence-
    # checked first.
    sortable = sorted(
        ((p, _max_mtime(p)) for p in session_files),
        key=lambda pair: pair[1],
        reverse=True,
    )
    ordered_paths = [p for p, _ in sortable]

    # Phase 2: parallel SQLite opens to extract meta + preview for
    # display. ``ThreadPoolExecutor.map`` preserves input order, so
    # the mtime-derived sort survives. SQLite open + a handful of
    # point queries is I/O-bound, so the GIL doesn't serialise.
    if not ordered_paths:
        results = []
    else:
        worker_count = min(_MAX_INDEX_WORKERS, len(ordered_paths))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            results = list(pool.map(_read_session_entry, ordered_paths))

    _session_index = results
    _index_built_at = time.time()
    return results


def get_session_index(max_age: float = 30.0) -> list[dict]:
    """Get cached session index, rebuild if stale."""
    if time.time() - _index_built_at > max_age:
        return build_session_index()
    return _session_index


def all_session_files_default() -> list[Path]:
    """Every session file under the default ``_SESSION_DIR`` (Wave-D-aware)."""
    return all_session_files(_session_dir())


def session_stats() -> dict[str, Any]:
    """Aggregations over the cached session index.

    Pure read of :func:`get_session_index` — does not force a rebuild,
    so it's cheap (server returns sub-millisecond after the first
    index build). Returns:

        {
            "count": int,
            "by_config_type":  {<type>: <n>, ...},
            "by_status":       {<status>: <n>, ...},
            "by_recency":      {"1d": <n>, "7d": <n>, "30d": <n>, "older": <n>},
            "by_format_version": {"1": <n>, "2": <n>, ...},
            "agents_top": [[<agent>, <n>], ...],
            "average_age_seconds": float | None,
        }
    """
    sessions = get_session_index()
    if not sessions:
        return {
            "count": 0,
            "by_config_type": {},
            "by_status": {},
            "by_recency": {"1d": 0, "7d": 0, "30d": 0, "older": 0},
            "by_format_version": {},
            "agents_top": [],
            "average_age_seconds": None,
        }

    by_config_type: Counter = Counter()
    by_status: Counter = Counter()
    by_format: Counter = Counter()
    agents: Counter = Counter()
    by_recency = {"1d": 0, "7d": 0, "30d": 0, "older": 0}

    now = time.time()
    age_total = 0.0
    age_count = 0

    def _to_ts(s: str) -> float | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    for entry in sessions:
        if entry.get("error"):
            continue
        by_config_type[entry.get("config_type", "unknown")] += 1
        by_status[entry.get("status", "unknown") or "unknown"] += 1
        by_format[str(entry.get("format_version", 1))] += 1

        for agent in entry.get("agents") or []:
            if agent:
                agents[agent] += 1

        ts = _to_ts(entry.get("last_active") or entry.get("created_at") or "")
        if ts is not None:
            age = now - ts
            if age >= 0:
                age_total += age
                age_count += 1
                if age < 86400:
                    by_recency["1d"] += 1
                elif age < 86400 * 7:
                    by_recency["7d"] += 1
                elif age < 86400 * 30:
                    by_recency["30d"] += 1
                else:
                    by_recency["older"] += 1

    return {
        "count": len(sessions),
        "by_config_type": dict(by_config_type),
        "by_status": dict(by_status),
        "by_recency": by_recency,
        "by_format_version": dict(by_format),
        "agents_top": [list(p) for p in agents.most_common(5)],
        "average_age_seconds": (age_total / age_count) if age_count else None,
    }


def disk_usage() -> dict[str, Any]:
    """Aggregate disk usage of the saved-session directory.

    Stats every session file + its ``-wal`` / ``-shm`` sidecars. Pure
    filesystem; no DB open. Returns:

        {
            "count": int,            # canonical session entries
            "total_bytes": int,      # incl. sidecars
            "oldest_at": float|None, # min mtime across canonical files
            "newest_at": float|None, # max mtime
            "session_dir": str,
        }
    """
    session_dir = _session_dir()
    if not session_dir.exists():
        return {
            "count": 0,
            "total_bytes": 0,
            "oldest_at": None,
            "newest_at": None,
            "session_dir": str(session_dir),
        }

    canonical = pick_canonical_per_session(session_dir)
    total = 0
    oldest: float | None = None
    newest: float | None = None
    for path in canonical:
        try:
            st = path.stat()
        except OSError:
            continue
        total += st.st_size
        if oldest is None or st.st_mtime < oldest:
            oldest = st.st_mtime
        if newest is None or st.st_mtime > newest:
            newest = st.st_mtime
        # Add sidecars so the surfaced number matches what the user
        # sees on disk.
        for suffix in ("-wal", "-shm"):
            sidecar = str(path) + suffix
            if not os.path.exists(sidecar):
                continue
            try:
                total += os.stat(sidecar).st_size
            except OSError:
                continue

    return {
        "count": len(canonical),
        "total_bytes": total,
        "oldest_at": oldest,
        "newest_at": newest,
        "session_dir": str(session_dir),
    }


def resolve_session_path_default(session_name: str) -> Path | None:
    """Resolve ``session_name`` against the default ``_SESSION_DIR``."""
    return resolve_session_path(session_name, _session_dir())


def all_versions_for_session_default(session_name: str) -> list[Path]:
    """Every file belonging to the given session (v1 + v2 rollback pair)."""
    return all_versions_for_session(session_name, _session_dir())


def session_targets(store: SessionStore, meta: dict[str, Any]) -> list[str]:
    """Return the ordered list of read-only history targets in a session.

    Includes every agent listed in meta + every channel + any extra
    targets discovered from the events / conversation tables.
    """
    targets: list[str] = []
    seen: set[str] = set()

    for target in meta.get("agents", []):
        if target and target not in seen:
            seen.add(target)
            targets.append(target)

    for ch in meta.get("terrarium_channels", []):
        name = ch.get("name", "")
        target = f"ch:{name}" if name else ""
        if target and target not in seen:
            seen.add(target)
            targets.append(target)

    if targets:
        return targets

    for key, _evt in store.get_all_events():
        if ":e" not in key:
            continue
        target = key.split(":e", 1)[0]
        if target and target not in seen:
            seen.add(target)
            targets.append(target)

    for key_bytes in store.conversation.keys(limit=2**31 - 1):
        target = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
        if target and target not in seen:
            seen.add(target)
            targets.append(target)

    return targets


def session_history_payload(store: SessionStore, target: str) -> dict[str, Any]:
    """Read-only history slice for a given agent/root/channel target."""
    if target.startswith("ch:"):
        channel = target[3:]
        messages = store.get_channel_messages(channel)
        return {
            "target": target,
            "messages": [],
            "events": [
                {
                    "type": "channel_message",
                    "channel": channel,
                    "sender": m.get("sender", ""),
                    "content": m.get("content", ""),
                    "ts": m.get("ts", 0),
                }
                for m in messages
            ],
        }

    get_events = getattr(store, "get_resumable_events", None) or store.get_events
    return {
        "target": target,
        "messages": store.load_conversation(target) or [],
        "events": get_events(target),
    }


def delete_session_files(session_name: str) -> list[Path]:
    """Delete every on-disk file belonging to ``session_name``.

    Returns the list of deleted paths. Returns an empty list when no
    matching file exists; the caller maps that to a 404. Falls back to
    fuzzy lookup if the user passes a legacy raw stem.
    """
    targets = all_versions_for_session_default(session_name)
    if not targets:
        resolved = resolve_session_path_default(session_name)
        if resolved is not None:
            targets = all_versions_for_session_default(normalize_session_stem(resolved))
            if not targets:
                targets = [resolved]

    if not targets:
        return []

    for path in targets:
        path.unlink()
    return targets
