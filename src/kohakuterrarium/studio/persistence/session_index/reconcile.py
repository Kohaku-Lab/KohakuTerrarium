"""Reconciler — sync the central session index against the disk.

Two modes:

* ``full=True``      — re-read every file regardless of fingerprint.
                       Used for the initial bootstrap (when the
                       sidecar is empty) and for the explicit
                       "Rebuild index" button in the UI.
* ``full=False``     — only re-read files whose ``(mtime, size)``
                       fingerprint differs from what the sidecar
                       has cached.  Used by the periodic /
                       on-demand reconcile path that backs
                       ``?refresh=true`` on the listing endpoint.

In both modes we drop sidecar entries whose backing file is gone.
Parallelism uses a ``ThreadPoolExecutor`` capped at ``cpus * 4`` (32
ceiling) to keep the cold-bootstrap cost in line.

Lives in the ``session_index`` package so it can pull in
``SessionStore`` lazily — the reconciler is the one place that
opens individual session files; everything else reads from the
sidecar.
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


# SQLite-over-files is GIL-friendly because most time is in I/O
# wait.  ``cpus * 4`` per Python's default ThreadPoolExecutor
# heuristic, capped at 32 so a pathological session dir doesn't open
# thousands of file handles.
_MAX_WORKERS = min(32, (os.cpu_count() or 4) * 4)


@dataclass
class ReconcileReport:
    """What :func:`reconcile` actually did, surfaced to the API."""

    read: int
    deleted: int
    total: int
    elapsed_ms: float


def _extract_text_preview(content, limit: int = 200) -> str:
    """Flatten an event ``content`` (str / list-of-parts / dict / other)
    into a short text preview suitable for the listing payload.

    Multimodal parts (``image_url`` / ``file`` / unknown) contribute a
    bracketed token (``[image]`` / ``[file]`` / ``[<kind>]``) so the
    listing surface never embeds raw base64 blobs.
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
    """Read the first user input for the preview column.

    Scans the primary agent's (``meta['agents'][0]``) resumable
    events for the first ``user_input`` entry and feeds it through
    :func:`_extract_text_preview`.  Returns ``""`` when the session
    has no user input yet (e.g. a fresh ``init_meta`` with nothing
    typed).
    """
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
        logger.debug("preview read failed; using empty", error=str(exc))
    return ""


def _max_mtime_with_wal(path: Path, *, fallback: float = 0.0) -> float:
    """Same logic as ``entry._max_mtime_with_wal`` but takes the
    main-file mtime as a precomputed fallback to save one ``stat``
    call inside the reconcile hot loop (the caller has already
    stat'd ``path`` for the size).
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
    """Cheap probe: did the embedder ever write the dimensions row?

    Reads one state-table row; doesn't open ``SessionMemory`` (which
    would open three additional native SQLite handles per session
    and would dominate the reconcile cost on a populated index).
    """
    try:
        if "vec_dimensions" in store.state:
            v = store.state.get("vec_dimensions")
            return isinstance(v, int) and v > 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("vector-index probe failed", error=str(exc))
    return False


def read_entry_from_disk(path: Path) -> SessionIndexEntry | None:
    """Open a ``.kohakutr`` file and build a sidecar entry from it.

    Returns ``None`` when the file can't be opened (corrupt, locked,
    permission denied).  Caller treats ``None`` as "skip this file
    for now" — the next reconcile retries.

    Uses ``close(update_status=False)`` to avoid the read-only side
    effect of bumping ``last_active`` (which would flip our cache
    invalidator every time we scanned).

    The fingerprint (file_mtime, file_size) is captured BEFORE opening
    the store.  SQLite's read-side WAL initialisation can touch the
    ``-wal`` / ``-shm`` mtimes during ``SessionStore(path)``; using
    the post-open stat would make every successful read invalidate
    its own cache entry on the next reconcile.
    """
    try:
        try:
            st = path.stat()
            pre_mtime = _max_mtime_with_wal(path, fallback=st.st_mtime)
            pre_size = st.st_size
        except OSError as exc:
            logger.debug("pre-open stat failed", path=str(path), error=str(exc))
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
        logger.debug("read_entry_from_disk failed", path=str(path), error=str(exc))
        return None


def reconcile(
    index: SessionIndex,
    session_dir: Path,
    *,
    full: bool = False,
    workers: int | None = None,
) -> ReconcileReport:
    """Sync the sidecar against the on-disk truth of ``session_dir``.

    See module docstring for ``full`` semantics.  Always drops
    sidecar entries whose backing file is gone — this is the only
    code path that removes index entries automatically.

    The return value is the small ``ReconcileReport`` dataclass —
    routes can surface it to the user (`"Rebuilt 12, dropped 3"`).
    """
    started = time.monotonic()
    if not session_dir.exists():
        return ReconcileReport(read=0, deleted=0, total=0, elapsed_ms=0.0)

    on_disk_paths = {p.name: p for p in pick_canonical_per_session(session_dir)}
    in_index = set(index.all_filenames())

    # 1. Drop entries whose file is gone.  Doing this first means a
    #    subsequent fingerprint-skip can't accidentally resurrect
    #    them (it can't anyway, but ordering matters for invariants).
    gone = in_index - on_disk_paths.keys()
    for fname in gone:
        index.delete(fname)

    # 2. Decide which on-disk files need a re-read.  Use the WAL-
    #    aware mtime helper so an active session that's only writing
    #    to ``-wal`` (no checkpoint yet → main file untouched) still
    #    invalidates its cache entry.  Pre-fix this missed every
    #    in-flight session between WAL checkpoints — preview /
    #    last_active / status fields could lag minutes behind.
    to_read: list[Path] = []
    for fname, path in on_disk_paths.items():
        if full or fname not in in_index:
            to_read.append(path)
            continue
        try:
            st = path.stat()
        except OSError as exc:
            logger.debug(
                "stat failed; will retry next reconcile", path=str(path), error=str(exc)
            )
            continue
        live_mtime = _max_mtime_with_wal(path, fallback=st.st_mtime)
        cached = index.fingerprint(fname)
        if not cached or abs(cached[0] - live_mtime) > 0.001 or cached[1] != st.st_size:
            to_read.append(path)

    # 3. Parallel re-read of the changed / new files.  ``map``
    #    preserves order but the order doesn't matter here — the
    #    sidecar's KV table is unordered and the listing endpoint
    #    sorts on demand.
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
