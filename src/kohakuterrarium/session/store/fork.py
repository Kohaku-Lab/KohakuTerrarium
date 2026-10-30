"""Create independent session forks with event-range and lineage controls."""

import shutil
import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kohakuvault import KVault

from kohakuterrarium.session.artifacts import artifacts_dir_for
from kohakuterrarium.session.errors import ForkNotStableError
from kohakuterrarium.session.history import (
    InvalidBranchViewError,
    _coerce_branch_view,
    resolve_branch_view_strict,
)
from kohakuterrarium.session.identity import new_conversation_id
from kohakuterrarium.session.resource_gate import acquire_legacy
from kohakuterrarium.session.store.events.counters import (
    EXACT_EVENT_ID_KEY,
    MAX_EVENT_ID_KEY,
)
from kohakuterrarium.session.store.protocol import SessionStoreLike
from kohakuterrarium.session.vault_io import (
    copy_rows,
    read_prefix,
    scan_keys,
    write_rows,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# Fork stability pairs each call event with its corresponding result event.
_OPEN_CALL_TYPES = {"tool_call", "subagent_call"}
_CLOSE_RESULT_TYPES = {
    "tool_call": "tool_result",
    "subagent_call": "subagent_result",
}


def _decode_key(key_bytes: bytes | str) -> str:
    if isinstance(key_bytes, bytes):
        return key_bytes.decode("utf-8", errors="replace")
    return key_bytes


def _call_id_for(evt: dict[str, Any]) -> str:
    """Extract a stable identifier for a call event.

    Tool calls prefer ``call_id`` and fall back to ``job_id``; sub-agent calls
    use ``job_id``. Unidentified calls cannot participate in span matching.
    """
    return str(evt.get("call_id") or evt.get("job_id") or "")


def check_fork_stability(
    events_in_range: list[tuple[str, dict[str, Any]]],
    *,
    at_event_id: int,
    pending_job_ids: set[str] | None = None,
) -> None:
    """Raise :class:`ForkNotStableError` if the fork point is unsafe.

    Events must be in storage order and bounded by ``at_event_id``. A call
    without an in-range result is unsafe only when its identifier is still in
    ``pending_job_ids``; completed calls whose result lies after the fork point
    do not prevent forking.
    """
    pending = set(pending_job_ids or set())

    opened: dict[str, dict[str, Any]] = {}
    closed: set[str] = set()
    for _key, evt in events_in_range:
        etype = evt.get("type", "")
        if etype in _OPEN_CALL_TYPES:
            call_id = _call_id_for(evt)
            if call_id:
                opened[call_id] = evt
        elif etype in _CLOSE_RESULT_TYPES.values():
            call_id = _call_id_for(evt)
            if call_id:
                closed.add(call_id)

    unstable: list[str] = []
    for call_id in opened:
        if call_id in closed:
            continue
        if call_id in pending:
            unstable.append(call_id)

    if unstable:
        raise ForkNotStableError(
            "Fork would split in-flight job(s) that are still running: "
            + ", ".join(sorted(unstable))
            + ". Interrupt or wait for completion before forking."
        )


def _iter_keys(table: KVault) -> list[str]:
    """Return every key in a KVault decoded to str."""
    return [_decode_key(k) for k in scan_keys(table)]


def _copy_table(src: KVault, dst: KVault, keys: list[str] | None = None) -> int:
    """Copy selected rows from ``src`` to ``dst`` and return the count.

    ``keys=None`` copies the complete table.
    """
    return copy_rows(src, dst, keys, what="fork table row")


def _copy_branch_views(source: SessionStoreLike, dest: SessionStoreLike) -> None:
    """Project persisted selections onto the child's surviving user history."""
    for key in _iter_keys(source.state):
        if not key.endswith(":branch_view"):
            continue
        events = dest.get_events(key.removesuffix(":branch_view"))
        pairs = {
            (event.get("turn_index"), event.get("branch_id"))
            for event in events
            if event.get("type") in ("user_message", "user_input")
        }
        try:
            requested = _coerce_branch_view(source.state[key])
            surviving = {t: b for t, b in requested.items() if (t, b) in pairs}
            selected = (
                resolve_branch_view_strict(events, surviving) if surviving else {}
            )
        except InvalidBranchViewError:
            continue
        if selected:
            dest.state[key] = {str(t): b for t, b in selected.items()}


def _build_lineage(
    parent_meta: dict[str, Any],
    parent_session_id: str,
    at_event_id: int,
    mutate: Callable[[dict[str, Any]], dict[str, Any]] | None,
    created_at: str,
) -> dict[str, Any]:
    """Build the fork lineage record for the child's meta.

    Existing lineage entries retain their original keys so ancestry remains
    traversable.
    """
    existing = parent_meta.get("lineage")
    if isinstance(existing, dict):
        merged: dict[str, Any] = dict(existing)
    else:
        merged = {}

    if mutate is None:
        mutation_label: str | None = None
    else:
        mutation_label = getattr(mutate, "__name__", None) or "custom"

    fork_record = {
        "parent_session_id": parent_session_id,
        "parent_format_version": parent_meta.get("format_version", 2),
        "fork_point": at_event_id,
        "fork_mutation": mutation_label,
        "fork_created_at": created_at,
        "parent_path": parent_meta.get("__parent_path__"),
    }
    merged["fork"] = fork_record
    return merged


def _record_child_in_parent(
    parent_meta_table: KVault,
    child_session_id: str,
    child_path: str,
    at_event_id: int,
    created_at: str,
) -> None:
    """Append a child record to the parent's ``forked_children`` meta list."""
    try:
        existing = parent_meta_table["forked_children"]
    except KeyError:
        existing = []
    if not isinstance(existing, list):
        existing = []
    existing.append(
        {
            "session_id": child_session_id,
            "path": child_path,
            "fork_point": at_event_id,
            "fork_created_at": created_at,
        }
    )
    parent_meta_table["forked_children"] = existing


def _copy_artifacts(source_path: Path, dest_path: Path) -> None:
    """Copy the parent's artifact tree into independent child storage.

    Physical copies keep relative references valid after detachment and avoid
    shared mutable files. A missing source directory is a no-op.
    """
    source_art = source_path.parent / f"{source_path.stem}.artifacts"
    if not source_art.exists():
        return
    dest_art = artifacts_dir_for(dest_path)
    for item in source_art.iterdir():
        target = dest_art / item.name
        if target.exists():
            continue
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def _collect_copy_range(
    source: SessionStoreLike,
    at_event_id: int,
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any] | None]:
    """Return ordered events through the fork point and that point's event."""
    source.events.flush_cache()
    in_range: list[tuple[str, dict[str, Any]]] = []
    fork_point: dict[str, Any] | None = None
    for key_bytes, evt in read_prefix(source.events, what="event during fork scan"):
        key = _decode_key(key_bytes)
        if not isinstance(evt, dict):
            continue
        eid = evt.get("event_id")
        if not isinstance(eid, int):
            continue
        if eid > at_event_id:
            continue
        in_range.append((key, dict(evt)))
        if eid == at_event_id:
            fork_point = dict(evt)
    # Match replay ordering, using the storage key only as a stable tiebreaker.
    in_range.sort(key=lambda pair: (pair[1].get("event_id", 0), pair[0]))
    return in_range, fork_point


def _child_session_id(parent_session_id: str) -> str:
    """Build a child session identifier from its parent and a short UUID."""
    short = uuid.uuid4().hex[:8]
    return f"{parent_session_id}-fork-{short}"


def _new_purge_cursors() -> list[sqlite3.Cursor]:
    return []


def _close_purge_connection(
    conn: sqlite3.Connection | None,
    cursors: list[sqlite3.Cursor],
) -> bool:
    """Close every cursor, then the DB. Always attempt DB close."""
    if conn is None:
        return False
    released = True
    primary: BaseException | None = None
    for cursor in cursors:
        try:
            cursor.close()
        except sqlite3.ProgrammingError:
            released = False
        except BaseException as exc:
            released = False
            if primary is None:
                primary = exc
    cursors.clear()
    try:
        conn.close()
    except BaseException as exc:
        released = False
        if primary is None:
            primary = exc
        else:
            raise primary from exc
    if primary is not None:
        raise primary
    return released


def _purge_drive_tables(target_path: str) -> None:
    """Remove Drive tables so a conversation fork carries no commitments.

    Independent branches must never mutate the same Drive state. Raw SQL
    keeps the session package independent of Drive. Enroll before connect.
    """
    cursors: list[sqlite3.Cursor] = _new_purge_cursors()
    lease = acquire_legacy(target_path, "fork_drive_purge")
    conn: sqlite3.Connection | None = None
    source_uncertain = False
    primary: BaseException | None = None
    try:
        conn = sqlite3.connect(target_path)

        def execute(sql: str) -> sqlite3.Cursor:
            nonlocal source_uncertain
            cursor = conn.execute(sql)
            try:
                cursors.append(cursor)
            except BaseException as tracking:
                source_uncertain = True
                try:
                    cursor.close()
                except sqlite3.ProgrammingError:
                    pass
                except BaseException as cleanup:
                    raise tracking from cleanup
                raise
            return cursor

        try:
            execute("PRAGMA busy_timeout = 5000")
            rows = execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'drive%'"
            ).fetchall()
            for (name,) in rows:
                execute(f'DROP TABLE IF EXISTS "{name}"')
            if rows:
                conn.commit()
        except sqlite3.Error as exc:
            logger.warning("Fork drive-table purge failed", error=str(exc))
    except BaseException as exc:
        primary = exc
        raise
    finally:
        close_error: BaseException | None = None
        source_released = False
        try:
            source_released = _close_purge_connection(conn, cursors)
        except BaseException as exc:
            source_released = False
            close_error = exc
        if source_uncertain:
            source_released = False
        lease.finish(source_released=source_released)
        if primary is not None:
            if close_error is not None:
                raise primary from close_error
            raise primary
        if close_error is not None:
            raise close_error


def perform_fork(
    source: SessionStoreLike,
    target_path: str,
    *,
    at_event_id: int,
    mutate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    name: str | None = None,
    pending_job_ids: set[str] | None = None,
) -> SessionStoreLike:
    """Fork a session through one event, optionally rewriting that event.

    The child receives independent metadata, events, state, artifacts, and
    lineage while excluding stale snapshots and all Drive commitments.
    """
    if at_event_id < 1:
        raise ValueError(
            f"at_event_id must be >= 1 (event_ids start at 1, got {at_event_id})"
        )

    source.flush()
    in_range, fork_point_event = _collect_copy_range(source, at_event_id)
    if not in_range:
        raise ValueError(
            f"No events found with event_id <= {at_event_id}; refusing to fork"
        )

    check_fork_stability(
        in_range,
        at_event_id=at_event_id,
        pending_job_ids=pending_job_ids,
    )

    target = Path(target_path)
    if target.exists():
        raise FileExistsError(target)

    created_at = datetime.now(timezone.utc).isoformat()
    parent_meta: dict[str, Any] = {
        _decode_key(key): value
        for key, value in read_prefix(source.meta, what="parent meta key")
    }
    parent_session_id = str(parent_meta.get("session_id") or Path(source.path).stem)
    child_session_id = _child_session_id(parent_session_id)
    child_name = name or child_session_id

    dest = type(source)(str(target))
    try:
        # Child identity and lineage replace parent-local metadata after copying;
        # child discovery records belong only to the parent.
        write_rows(
            dest.meta,
            [
                (key, value)
                for key, value in parent_meta.items()
                if key not in ("forked_children", "resume_successor")
            ],
            what="child meta key",
        )

        dest.meta["session_id"] = child_session_id
        dest.meta["conversation_id"] = new_conversation_id()
        dest.meta["created_at"] = created_at
        dest.meta["last_active"] = created_at
        dest.meta["status"] = "paused"
        if name is not None:
            dest.meta["name"] = name

        lineage = _build_lineage(
            parent_meta={**parent_meta, "__parent_path__": source.path},
            parent_session_id=parent_session_id,
            at_event_id=at_event_id,
            mutate=mutate,
            created_at=created_at,
        )
        dest.meta["lineage"] = lineage

        # Only the fork-point event may be rewritten or dropped.
        mutated_fork_event: dict[str, Any] | None = None
        if mutate is not None and fork_point_event is not None:
            try:
                result = mutate(dict(fork_point_event))
            except Exception as exc:
                raise RuntimeError(f"Fork mutate callable raised: {exc}") from exc
            if result is None:
                mutated_fork_event = None
            elif isinstance(result, dict):
                mutated_fork_event = result
            else:
                raise TypeError(
                    "mutate callable must return a dict or None; "
                    f"got {type(result).__name__}"
                )

        fork_point_key: str | None = None
        if fork_point_event is not None:
            for key, evt in in_range:
                if evt.get("event_id") == at_event_id:
                    fork_point_key = key
                    break

        if mutate is None or fork_point_event is None:
            write_rows(dest.events, in_range, what="event")
        else:
            # Reinsert the fork point only when the mutator returns an event.
            write_rows(
                dest.events,
                [(key, evt) for key, evt in in_range if key != fork_point_key],
                what="non-fork event",
            )
            if mutated_fork_event is not None and fork_point_key is not None:
                # Preserve replay-critical identity when the mutator omits it.
                payload = dict(mutated_fork_event)
                payload.setdefault("event_id", at_event_id)
                payload.setdefault("type", fork_point_event.get("type", "user_message"))
                dest.events[fork_point_key] = payload

        # Snapshot markers, caches and event counters describe the parent's log;
        # the child rebuilds them from its truncated copy.
        state_keys = [
            k
            for k in _iter_keys(source.state)
            if k not in (MAX_EVENT_ID_KEY, EXACT_EVENT_ID_KEY)
            and not k.endswith(
                (
                    ":snapshot_event_id",
                    ":snapshot_branch",
                    ":branch_snapshots",
                    ":branch_view",
                )
            )
        ]
        _copy_table(source.state, dest.state, keys=state_keys)
        for key in _iter_keys(source.state):
            if key.endswith(":branch_snapshots"):
                dest.state[key] = []
        _copy_table(source.channels, dest.channels)
        _copy_table(source.subagents, dest.subagents)
        _copy_table(source.jobs, dest.jobs)
        _copy_table(source.turn_rollup, dest.turn_rollup)

        dest.flush()

        # Counters must reflect the child's copied rows before further appends.
        dest._event_seq.clear()
        dest._channel_seq.clear()
        dest._subagent_runs.clear()
        dest._global_event_id = 0
        dest._restore_counters()
        _copy_branch_views(source, dest)
        dest.flush()
    except Exception:
        try:
            dest.close(update_status=False)
        finally:
            if target.exists():
                lease = acquire_legacy(target, "fork_failed_output_delete")
                try:
                    target.unlink()
                except OSError as cleanup_exc:
                    logger.warning(
                        "Failed to remove partial fork output",
                        path=str(target),
                        error=str(cleanup_exc),
                    )
                finally:
                    lease.finish(source_released=True)
        raise

    _copy_artifacts(Path(source.path), target)

    # Conversation forks cannot inherit mutable Drive commitments.
    _purge_drive_tables(str(target))

    # Parent metadata provides the reverse edge for lineage traversal.
    try:
        _record_child_in_parent(
            source.meta,
            child_session_id=child_session_id,
            child_path=str(target),
            at_event_id=at_event_id,
            created_at=created_at,
        )
    except Exception as e:
        logger.warning(
            "Fork could not update parent forked_children",
            parent_path=source.path,
            error=str(e),
        )

    logger.info(
        "Session forked",
        parent_path=source.path,
        child_path=str(target),
        fork_point=at_event_id,
        mutation=getattr(mutate, "__name__", None) if mutate else None,
        child_name=child_name,
    )

    return dest
