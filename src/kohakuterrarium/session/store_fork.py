"""Fork / branch primitive for :class:`SessionStore` (Wave E).

Copy-on-fork implementation: every fork is an independent v2 file.
Lineage sidecar is deferred to a later wave (see
``plans/session-system/implementation-plan.md`` §2.2).

Kept out of ``session/store.py`` so the store module stays under the
600-line soft cap. :meth:`SessionStore.fork` delegates straight here.
"""

import shutil
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakuvault import KVault

from kohakuterrarium.session.artifacts import artifacts_dir_for
from kohakuterrarium.session.errors import ForkNotStableError
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.session.store import SessionStore

logger = get_logger(__name__)


# Event types that open a "pending" span until their matching result
# event arrives. Used by the stability check.
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

    Tool calls carry ``call_id`` (preferred) or ``job_id``. Sub-agent
    calls carry ``job_id``. Empty string means "no id" — we don't try
    to match those.
    """
    return str(evt.get("call_id") or evt.get("job_id") or "")


def check_fork_stability(
    events_in_range: list[tuple[str, dict[str, Any]]],
    *,
    at_event_id: int,
    pending_job_ids: set[str] | None = None,
) -> None:
    """Raise :class:`ForkNotStableError` if the fork point is unsafe.

    ``events_in_range`` are the (key, event) pairs whose
    ``event_id <= at_event_id``, in storage order. ``pending_job_ids``
    is the set of call ids the parent :class:`Session` reports as
    currently in-flight; an empty / ``None`` set means "nothing is
    actively running" — the common resume-from-disk case.

    Rules:

    * Every ``tool_call`` / ``subagent_call`` in the range whose
      matching result is ALSO in the range is fine (closed span).
    * Every call whose result is missing from the range is only
      unstable if its ``call_id`` is in ``pending_job_ids``. Otherwise
      the call finished after the fork point and the fork is clean.
    """
    pending = set(pending_job_ids or set())

    # Collect calls opened and results closed inside the copy range.
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
    return [_decode_key(k) for k in table.keys()]


def _copy_table(src: KVault, dst: KVault, keys: list[str] | None = None) -> int:
    """Copy entries from ``src`` to ``dst``. Returns rows copied.

    When ``keys`` is ``None``, every row is copied. Otherwise only the
    provided keys are copied (use-case: restrict events to the copy
    range).
    """
    target_keys = keys if keys is not None else _iter_keys(src)
    written = 0
    for key in target_keys:
        try:
            dst[key] = src[key]
            written += 1
        except KeyError:
            continue
        except Exception as e:
            logger.debug(
                "Fork failed to copy table row",
                key=key,
                error=str(e),
                exc_info=True,
            )
    return written


def _build_lineage(
    parent_meta: dict[str, Any],
    parent_session_id: str,
    at_event_id: int,
    mutate: Callable[[dict[str, Any]], dict[str, Any]] | None,
    created_at: str,
) -> dict[str, Any]:
    """Build the fork lineage record for the child's meta.

    Preserves any earlier lineage entries (e.g. Wave D migration data)
    under their original keys so the chain is still traversable.
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
    """Shallow-copy the parent's ``<stem>.artifacts/`` tree to the child.

    We choose a shallow copy (not a hardlink / reflink) because:

    * events inside the copy range may reference artifacts by relative
      path — the child must be able to resolve them after a detach.
    * forks are low-frequency (a handful per session lifetime), so the
      O(size) cost is acceptable.

    Subdirectories are copied recursively with ``copytree``; missing
    source dir is a no-op.
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
    source: "SessionStore",
    at_event_id: int,
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any] | None]:
    """Return (events-in-range, fork-point event). Events in storage order.

    ``fork_point_event`` is the event whose ``event_id == at_event_id``
    (if any). Returned separately so the caller can pass it to
    ``mutate`` before re-inserting.
    """
    source.events.flush_cache()
    in_range: list[tuple[str, dict[str, Any]]] = []
    fork_point: dict[str, Any] | None = None
    for key_bytes in source.events.keys():
        key = _decode_key(key_bytes)
        try:
            evt = source.events[key_bytes]
        except Exception as e:
            logger.debug(
                "Fork failed to read event during scan",
                key=key,
                error=str(e),
                exc_info=True,
            )
            continue
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
    # Stable ordering: by event_id first (same global ordering the
    # replay uses), then by key as a tiebreaker.
    in_range.sort(key=lambda pair: (pair[1].get("event_id", 0), pair[0]))
    return in_range, fork_point


def _child_session_id(parent_session_id: str) -> str:
    """Build a child session id from the parent's id + a short uuid."""
    short = uuid.uuid4().hex[:8]
    return f"{parent_session_id}-fork-{short}"


def perform_fork(
    source: "SessionStore",
    target_path: str,
    *,
    at_event_id: int,
    mutate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    name: str | None = None,
    pending_job_ids: set[str] | None = None,
) -> "SessionStore":
    """Implementation behind :meth:`SessionStore.fork`.

    Kept as a module-level function so the store class stays lean.
    """
    # Lazy import to avoid a module cycle with ``session.store``.
    from kohakuterrarium.session.store import SessionStore

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
    parent_meta: dict[str, Any] = {}
    for key_bytes in source.meta.keys():
        key = _decode_key(key_bytes)
        try:
            parent_meta[key] = source.meta[key_bytes]
        except Exception as e:
            logger.debug(
                "Fork failed to read parent meta key",
                key=key,
                error=str(e),
                exc_info=True,
            )
    parent_session_id = str(parent_meta.get("session_id") or Path(source.path).stem)
    child_session_id = _child_session_id(parent_session_id)
    child_name = name or child_session_id

    dest = SessionStore(str(target))
    try:
        # --- meta: copy parent meta verbatim, then overwrite the
        # session identity + stamp lineage.
        for key, value in parent_meta.items():
            if key in ("forked_children",):
                # Parent-local bookkeeping doesn't cross into the child.
                continue
            try:
                dest.meta[key] = value
            except Exception as e:
                logger.debug(
                    "Fork failed to write child meta key",
                    key=key,
                    error=str(e),
                    exc_info=True,
                )

        dest.meta["session_id"] = child_session_id
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

        # --- events: restrict to the copy range and optionally mutate
        # the fork-point event.
        mutated_fork_event: dict[str, Any] | None = None
        if mutate is not None and fork_point_event is not None:
            try:
                result = mutate(dict(fork_point_event))
            except Exception as exc:
                raise RuntimeError(f"Fork mutate callable raised: {exc}") from exc
            if result is None:
                mutated_fork_event = None  # drop the event
            elif isinstance(result, dict):
                mutated_fork_event = result
            else:
                raise TypeError(
                    "mutate callable must return a dict or None; "
                    f"got {type(result).__name__}"
                )

        event_keys = [key for key, _ in in_range]
        fork_point_key: str | None = None
        if fork_point_event is not None:
            for key, evt in in_range:
                if evt.get("event_id") == at_event_id:
                    fork_point_key = key
                    break

        if mutate is None or fork_point_event is None:
            _copy_table(source.events, dest.events, keys=event_keys)
        else:
            # Copy every event except the fork-point key; re-insert the
            # fork-point with the mutated payload (or drop it entirely
            # if mutate returned None).
            for key in event_keys:
                if key == fork_point_key:
                    continue
                try:
                    dest.events[key] = source.events[key]
                except Exception as e:
                    logger.debug(
                        "Fork failed to copy non-fork event",
                        key=key,
                        error=str(e),
                        exc_info=True,
                    )
            if mutated_fork_event is not None and fork_point_key is not None:
                # Preserve the original event_id / type if the mutator
                # stripped them (defensive — spec allows a full rewrite
                # but we still want a playable event).
                payload = dict(mutated_fork_event)
                payload.setdefault("event_id", at_event_id)
                payload.setdefault("type", fork_point_event.get("type", "user_message"))
                dest.events[fork_point_key] = payload

        # --- other tables: copy wholesale. They are snapshots that
        # make sense at the fork point (state, scratchpad, jobs, etc.).
        _copy_table(source.state, dest.state)
        _copy_table(source.channels, dest.channels)
        _copy_table(source.subagents, dest.subagents)
        _copy_table(source.jobs, dest.jobs)
        _copy_table(source.conversation, dest.conversation)
        _copy_table(source.turn_rollup, dest.turn_rollup)

        dest.flush()

        # Re-derive the per-agent / global counters from whatever
        # landed on disk so the returned store is ready for further
        # appends.
        dest._event_seq.clear()
        dest._channel_seq.clear()
        dest._subagent_runs.clear()
        dest._global_event_id = 0
        dest._restore_counters()
    except Exception:
        try:
            dest.close(update_status=False)
        finally:
            if target.exists():
                try:
                    target.unlink()
                except OSError as cleanup_exc:
                    logger.warning(
                        "Failed to remove partial fork output",
                        path=str(target),
                        error=str(cleanup_exc),
                    )
        raise

    _copy_artifacts(Path(source.path), target)

    # Record the child in the parent's meta so tree walks can find it.
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
