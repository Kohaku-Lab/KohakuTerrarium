"""Reconciler — sync the central session index against the disk.

Full reconciliation rereads every canonical session file; incremental
reconciliation only rereads files whose WAL-aware ``(mtime, size)`` fingerprint
changed. Both modes remove entries for missing files.

Session files are opened only here. Parallel reads are bounded to four workers
per CPU and at most 32 to limit cold-start latency and file-handle pressure.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import os
import time

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.session_index.entry import SessionIndexEntry
from kohakuterrarium.studio.persistence.session_index.store import SessionIndex
from kohakuterrarium.studio.persistence.viewer.paths import pick_canonical_per_session
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# SQLite reads spend most time waiting on I/O; the cap prevents excessive
# file-handle pressure in large session directories.
_MAX_WORKERS = min(32, (os.cpu_count() or 4) * 4)


@dataclass
class ReconcileReport:
    """What :func:`reconcile` actually did, surfaced to the API."""

    read: int
    deleted: int
    total: int
    elapsed_ms: float


def _extract_text_preview(content, limit: int = 200) -> str:
    """Flatten event content into a bounded listing preview.

    Multimodal and unknown parts become bracketed markers so previews never
    embed binary or base64 payloads.
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
                elif kind in ("image_url", "image"):
                    bits.append("[image]")
                elif kind == "file":
                    bits.append("[file]")
                else:
                    bits.append(f"[{kind or 'attachment'}]")
        return " ".join(b for b in bits if b)[:limit]
    if isinstance(content, dict):
        return _extract_text_preview([content], limit)
    return str(content)[:limit]


def _first_user_input_preview(store: SessionStore) -> str:
    """Return the primary agent's first resumable user-input preview."""
    try:
        meta = store.load_meta()
        agent = (meta.get("agents") or [""])[0]
        if not agent:
            return ""
        for evt in store.get_resumable_events(agent):
            if evt.get("type") == "user_input":
                preview = _extract_text_preview(evt.get("content"))
                if preview:
                    return preview
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "preview read failed; using empty", error=str(exc), exc_info=True
        )
    return ""


def _max_mtime_with_wal(path: Path, *, fallback: float = 0.0) -> float:
    """Return the newest main or SQLite-sidecar mtime.

    The caller supplies the already-read main-file mtime to avoid another stat
    in the reconciliation hot path.
    """
    best = fallback
    for suffix in ("-wal", "-shm"):
        sidecar = str(path) + suffix
        if not os.path.exists(sidecar):
            continue
        try:
            mt = os.stat(sidecar).st_mtime
        except OSError:
            continue
        if mt > best:
            best = mt
    return best


def _has_vector_index(store: SessionStore) -> bool:
    """Probe vector-index existence from the dimensions state row.

    Avoiding ``SessionMemory`` prevents three extra native SQLite handles per
    session during reconciliation.
    """
    try:
        if "vec_dimensions" in store.state:
            v = store.state.get("vec_dimensions")
            return isinstance(v, int) and v > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("vector-index probe failed", error=str(exc), exc_info=True)
    return False


def read_entry_from_disk(path: Path) -> SessionIndexEntry | None:
    """Build an index entry from a session file, or return ``None`` on failure.

    Failures are retried by later reconciliation. The fingerprint is captured
    before opening because SQLite read initialization may touch WAL sidecars
    and otherwise invalidate the new entry immediately. Closing without a
    status update preserves read-only behavior and ``last_active`` stability.
    """
    try:
        try:
            st = path.stat()
            pre_mtime = _max_mtime_with_wal(path, fallback=st.st_mtime)
            pre_size = st.st_size
        except OSError as exc:
            logger.warning(
                "pre-open stat failed", path=str(path), error=str(exc), exc_info=True
            )
            return None
        store = SessionStore(path)
        try:
            meta = store.load_meta()
            preview = _first_user_input_preview(store)
            has_vec = _has_vector_index(store)
            return SessionIndexEntry.from_meta(
                path=path,
                meta=meta,
                preview=preview,
                has_vector_index=has_vec,
                file_mtime=pre_mtime,
                file_size=pre_size,
            )
        finally:
            store.close(update_status=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "read_entry_from_disk failed",
            path=str(path),
            error=str(exc),
            exc_info=True,
        )
        return None


def reconcile(
    index: SessionIndex,
    session_dir: Path,
    *,
    full: bool = False,
    workers: int | None = None,
) -> ReconcileReport:
    """Synchronize the sidecar with canonical files in ``session_dir``.

    Missing files are always removed. ``full`` controls whether unchanged
    fingerprints may skip reads. The report exposes read, deletion, and timing
    counts to callers.
    """
    started = time.monotonic()
    if not session_dir.exists():
        return ReconcileReport(read=0, deleted=0, total=0, elapsed_ms=0.0)

    on_disk_paths = {p.name: p for p in pick_canonical_per_session(session_dir)}
    in_index = set(index.all_filenames())

    # Remove missing files before fingerprint checks so the sidecar reflects
    # current disk membership throughout reconciliation.
    gone = in_index - on_disk_paths.keys()
    for fname in gone:
        index.delete(fname)

    # WAL-aware mtimes invalidate active sessions before checkpointing updates
    # the main file, keeping preview, status, and activity metadata current.
    to_read: list[Path] = []
    for fname, path in on_disk_paths.items():
        if full or fname not in in_index:
            to_read.append(path)
            continue
        try:
            st = path.stat()
        except OSError as exc:
            logger.warning(
                "stat failed; will retry next reconcile",
                path=str(path),
                error=str(exc),
                exc_info=True,
            )
            continue
        live_mtime = _max_mtime_with_wal(path, fallback=st.st_mtime)
        cached = index.fingerprint(fname)
        if not cached or abs(cached[0] - live_mtime) > 0.001 or cached[1] != st.st_size:
            to_read.append(path)

    # Read order is irrelevant because the sidecar is unordered and listings
    # apply their own sort.
    if to_read:
        worker_count = (
            workers if workers is not None else min(_MAX_WORKERS, len(to_read))
        )
        worker_count = max(1, worker_count)
        if worker_count == 1:
            for path in to_read:
                entry = read_entry_from_disk(path)
                if entry is not None:
                    index.upsert(entry)
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                for entry in pool.map(read_entry_from_disk, to_read):
                    if entry is not None:
                        index.upsert(entry)

    index.meta_put("last_reconcile_at", time.time())
    elapsed = (time.monotonic() - started) * 1000.0
    report = ReconcileReport(
        read=len(to_read),
        deleted=len(gone),
        total=len(on_disk_paths),
        elapsed_ms=elapsed,
    )
    logger.info(
        "session index reconciled",
        read=report.read,
        deleted=report.deleted,
        total=report.total,
        elapsed_ms=round(report.elapsed_ms, 1),
        full=full,
    )
    return report
