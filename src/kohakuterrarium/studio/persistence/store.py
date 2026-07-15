"""Per-session filesystem + history helpers for the persistence layer.

Filesystem and per-store operations live here so HTTP and programmatic
surfaces share one implementation. Listing, search, and aggregation belong to
the session-index sidecar; this module handles resolution, file enumeration,
deletion, history, and disk usage for individual sessions.
"""

import gc
import os
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.store_lock import acquire_writer_lock, release_writer_lock
from kohakuterrarium.studio.persistence.delete_family import (
    detach_file_family,
    remove_detached_family,
)
from kohakuterrarium.studio.persistence.session_index import (
    get_session_index_default,
)
from kohakuterrarium.studio.persistence.viewer.paths import (
    all_session_files,
    all_versions_for_session,
    normalize_session_stem,
    pick_canonical_per_session,
    resolve_session_path,
)
from kohakuterrarium.utils import drive_migration_lock
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Explicit session directories avoid this process-wide default when callers
# require namespace isolation. Tests also replace the default directly.
_SESSION_DIR = Path.home() / ".kohakuterrarium" / "sessions"


def _session_dir() -> Path:
    """Return the session directory shared by persistence and lifecycle APIs.

    ``KT_SESSION_DIR`` has highest precedence. A replaced module default is
    honored next; otherwise the configured application directory is used.
    Values are read on every call so environment and test overrides remain
    live.
    """
    env = os.environ.get("KT_SESSION_DIR")
    if env:
        return Path(env)
    # A replaced module default takes precedence; otherwise deriving from
    # ``config_dir`` keeps configuration-directory overrides isolated.
    _docs_default = Path.home() / ".kohakuterrarium" / "sessions"
    if _SESSION_DIR != _docs_default:
        return _SESSION_DIR
    return config_dir() / "sessions"


def all_session_files_default() -> list[Path]:
    """Return every supported session file under the default directory."""
    return all_session_files(_session_dir())


def disk_usage() -> dict[str, Any]:
    """Return canonical session count, timestamps, and on-disk byte usage.

    Byte totals include SQLite ``-wal`` and ``-shm`` sidecars without opening
    any session database. Timestamps come from canonical session files only.
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
        # Sidecars are part of the session's observable disk footprint.
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


def resolve_session_path_in(session_name: str, session_dir: Path) -> Path | None:
    """Resolve ``session_name`` against an explicit ``session_dir``.

    The saved-session Drive viewer resolves inside the authenticated user's L4
    namespace (R1-01); it must never fall back to the process-global directory,
    so this takes the directory explicitly rather than reading the module global.
    """
    return resolve_session_path(session_name, session_dir)


def all_versions_for_session_default(session_name: str) -> list[Path]:
    """Every file belonging to the given session (v1 + v2 rollback pair)."""
    return all_versions_for_session(session_name, _session_dir())


def session_targets(store: SessionStore, meta: dict[str, Any]) -> list[str]:
    """Return ordered history targets from metadata or storage discovery.

    Metadata-listed agents and channels are authoritative when present.
    Sessions without those records fall back to event and conversation keys.
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


def session_history_payload(
    store: SessionStore,
    target: str,
    *,
    live_job_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Return history for an agent, root, or channel target.

    ``live_job_ids`` identifies work still running in a live session so it is
    not synthesized as interrupted. Saved-session callers omit it because any
    unfinished persisted job is no longer active.
    """
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

    resumable = getattr(store, "get_resumable_events", None)
    if resumable is not None:
        events = resumable(target, live_job_ids=live_job_ids)
    else:
        events = store.get_events(target)
    return {
        "target": target,
        "messages": store.load_conversation(target) or [],
        "events": events,
    }


def _unlink_with_retry(path: Path, attempts: int = 5, base_delay: float = 0.05) -> None:
    """Unlink a file, retrying transient Windows handle contention.

    Native store handles can outlive ``SessionStore.close`` briefly while
    refcount-driven cleanup finishes. Exponential backoff gives those handles
    time to close; persistent permission failures are re-raised after the
    bounded retry window. POSIX normally succeeds on the first attempt.
    """
    last_exc: OSError | None = None
    for i in range(attempts):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError as e:
            last_exc = e
            # Collection can release refcount-owned native SQLite handles
            # before the next attempt.
            gc.collect()
            time.sleep(base_delay * (2**i))
    assert last_exc is not None
    raise last_exc


def _sidecars_for(path: Path) -> list[Path]:
    """Return existing ``-wal`` / ``-shm`` sidecars for a SQLite file."""
    out: list[Path] = []
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            out.append(sidecar)
    return out


def _drive_sidecars_for(path: Path) -> list[Path]:
    """Return deletable Drive sidecars paired with a session database.

    The persistent ``.drives.migrate-lock`` is excluded because replacing its
    inode would allow processes to hold mutually ineffective locks.
    """
    return [
        candidate
        for suffix in (".drives", ".drives-wal", ".drives-shm")
        if (candidate := path.with_name(path.name + suffix)).exists()
    ]


def delete_session_files(session_name: str) -> list[Path]:
    """Delete a session file family and return the removed paths.

    Legacy raw stems use fuzzy resolution. An empty result means no matching
    session exists. Index entries are purged immediately so list and stats
    views do not retain deleted sessions until reconciliation.
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

    # Holding every writer and Drive migration lock before inspection makes
    # deletion atomic with sidecar publication. Bounded acquisition fails before
    # any removal when an active migration remains busy.
    with ExitStack() as guards:
        for path in sorted(targets, key=str):
            lock = acquire_writer_lock(str(path))
            guards.callback(release_writer_lock, lock)
        for path in sorted(targets, key=str):
            guards.enter_context(drive_migration_lock.drive_migration_guard(path))

        family = []
        for path in targets:
            family.extend([path, *_sidecars_for(path), *_drive_sidecars_for(path)])
            family.extend(path.parent.glob(f"{path.name}.drives.split-intent.json*"))
        detached = detach_file_family(family)
        deleted = remove_detached_family(detached, _unlink_with_retry)

    _purge_index_entries(targets)
    return deleted


def _purge_index_entries(deleted_paths: list[Path]) -> None:
    """Best-effort removal of deleted filenames from the session index.

    Index failure cannot undo file deletion; reconciliation later removes any
    stale entries left behind.
    """
    try:
        session_dir = _session_dir()
        index = get_session_index_default(session_dir)
        for path in deleted_paths:
            index.delete(path.name)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "session-index purge after delete failed", error=str(exc), exc_info=True
        )
