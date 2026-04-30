"""Saved-session list / delete / index for the persistence layer.

Verbatim port of the listing helpers and per-target history shaping
that previously lived in ``api/routes/sessions.py``. The HTTP route
files in ``api/routes/persistence/`` provide the FastAPI surface; all
filesystem and SessionStore logic lives here so the CLI and HTTP
share one implementation.
"""

import time
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


def build_session_index() -> list[dict]:
    """Build index of all sessions. Cached in memory."""
    global _session_index, _index_built_at

    session_dir = _session_dir()
    if not session_dir.exists():
        _session_index = []
        return _session_index

    # Wave D auto-migration leaves both ``foo.kohakutr`` (v1 rollback)
    # and ``foo.kohakutr.v2`` (live) on disk. Surface only the highest-
    # versioned file per logical session; delete/resume still reach
    # both files via ``all_versions_for_session``.
    session_files = pick_canonical_per_session(session_dir)

    results = []
    for path in session_files:
        try:
            store = SessionStore(path)
            meta = store.load_meta()

            # Read first user message for preview
            preview = ""
            try:
                agent_name = (meta.get("agents") or [""])[0]
                if agent_name:
                    events = store.get_resumable_events(agent_name)
                    for evt in events:
                        if evt.get("type") == "user_input":
                            preview = (evt.get("content") or "")[:200]
                            break
            except Exception as e:
                logger.debug(
                    "Failed to read session preview", error=str(e), exc_info=True
                )

            store.close(update_status=False)

            lineage = meta.get("lineage") or {}
            forked_children = meta.get("forked_children") or []
            results.append(
                {
                    # Canonical name strips the version suffix so v1+v2
                    # of the same session show as one entry — and the
                    # name round-trips through delete/resume cleanly.
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
                    # ``parent_session_id`` is set on forked children;
                    # ``forked_children`` lists the child session_ids
                    # this session is the parent of. Frontend uses
                    # them to render parent/child grouping.
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
            )
        except Exception as e:
            _ = e  # corrupt session file, show as error entry
            results.append(
                {
                    "name": normalize_session_stem(path),
                    "filename": path.name,
                    "error": True,
                }
            )

    results.sort(
        key=lambda s: s.get("last_active") or s.get("created_at") or "",
        reverse=True,
    )

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
