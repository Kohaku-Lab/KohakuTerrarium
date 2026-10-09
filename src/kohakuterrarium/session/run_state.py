"""Crash-tolerant run records: what each creature was doing, per process boot.

``state["run:<agent>"]`` = {state, turn_id, started_at, last_beat, ended_at,
boot_id}; ``state`` is ``started`` / ``active`` (beat every
:data:`BEAT_INTERVAL` s) / ``idle`` / ``stopped``. The ``state`` table writes
straight to SQLite (WAL, no cache), so the last record survives a killed
process. ``meta["lifecycle"]`` = {live, stop_reason, boot_id, updated_at,
hosted_at} is the session's own record. A graceful shutdown :func:`freeze`\\ s the store
before its creatures stop, so records keep what was happening at shutdown
rather than the teardown's ``idle`` / ``stopped``.
"""

import time
import uuid
from pathlib import Path
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

BOOT_ID = uuid.uuid4().hex[:16]
BEAT_INTERVAL = 5.0
RUN_PREFIX = "run:"
LIFECYCLE_KEY = "lifecycle"

STARTED = "started"
ACTIVE = "active"
IDLE = "idle"
STOPPED = "stopped"

STOP_USER = "user"
STOP_SHUTDOWN = "shutdown"
STOP_CRASH = "crash"

_frozen: set[str] = set()


def _store_key(store: Any) -> str:
    path = getattr(store, "path", None) or getattr(store, "_path", None)
    if path is None:
        return f"id:{id(store)}"
    try:
        return str(Path(path).resolve(strict=False)).lower()
    except (OSError, RuntimeError, ValueError):
        return str(path).lower()


def freeze(store: Any) -> None:
    """Stop this process from changing ``store``'s run records."""
    _frozen.add(_store_key(store))


def thaw(store: Any) -> None:
    """Let this process write ``store``'s run records again (a resume reopens it)."""
    _frozen.discard(_store_key(store))


def is_frozen(store: Any) -> bool:
    return _store_key(store) in _frozen


def read_run(store: Any, agent: str) -> dict | None:
    try:
        value = store.state.get(f"{RUN_PREFIX}{agent}")
    except Exception:  # noqa: BLE001 - a missing or unreadable record is "none"
        return None
    return value if isinstance(value, dict) else None


def read_runs(store: Any, agents: list[str]) -> dict[str, dict | None]:
    return {name: read_run(store, name) for name in agents}


def write_run(
    store: Any,
    agent: str,
    state: str,
    *,
    turn_id: Any = None,
    now: float | None = None,
) -> dict | None:
    """Write ``agent``'s run record; returns it, or None while frozen."""
    if is_frozen(store):
        return None
    now = time.time() if now is None else now
    prev = read_run(store, agent) or {}
    turn_started = state == STARTED
    record = {
        "state": state,
        "turn_id": turn_id if turn_started else prev.get("turn_id"),
        "started_at": now if turn_started else prev.get("started_at"),
        "last_beat": now,
        "ended_at": now if state in (IDLE, STOPPED) else None,
        "boot_id": BOOT_ID,
    }
    store.state[f"{RUN_PREFIX}{agent}"] = record
    return record


def classify(record: dict | None, boot_id: str | None = None) -> str:
    """``idle`` | ``stopped`` | ``interrupted`` for a record left by an earlier boot.

    A turn still ``started`` / ``active`` under another boot never ended:
    the process went down mid-turn. No record means the creature never ran
    a turn, which resumes like ``idle``.
    """
    if not record:
        return IDLE
    state = record.get("state")
    if state == STOPPED:
        return STOPPED
    if state in (STARTED, ACTIVE) and record.get("boot_id") != (boot_id or BOOT_ID):
        return "interrupted"
    return IDLE


def read_lifecycle(store_or_meta: Any) -> dict:
    meta = getattr(store_or_meta, "meta", store_or_meta)
    try:
        value = meta.get(LIFECYCLE_KEY)
    except Exception:  # noqa: BLE001
        return {}
    return value if isinstance(value, dict) else {}


def set_lifecycle(store: Any, *, live: bool, stop_reason: str | None = None) -> dict:
    """Write the lifecycle record; ``hosted_at`` is when this boot began hosting."""
    now = time.time()
    prev = read_lifecycle(store)
    same_boot = prev.get("boot_id") == BOOT_ID and prev.get("hosted_at")
    record = {
        "live": bool(live),
        "stop_reason": stop_reason,
        "boot_id": BOOT_ID,
        "updated_at": now,
        "hosted_at": prev["hosted_at"] if same_boot else now,
    }
    store.meta[LIFECYCLE_KEY] = record
    return record


def stop_reason(lifecycle: dict, boot_id: str | None = None) -> str | None:
    """Why a session is not running now: ``user`` / ``shutdown`` / ``crash`` / None (live)."""
    if not lifecycle:
        return None
    if not lifecycle.get("live"):
        return lifecycle.get("stop_reason") or STOP_USER
    if lifecycle.get("stop_reason") == STOP_SHUTDOWN:
        return STOP_SHUTDOWN
    if lifecycle.get("boot_id") != (boot_id or BOOT_ID):
        return STOP_CRASH
    return None


def mark_shutdown(store: Any) -> None:
    """Record a graceful server shutdown and freeze the run records (requires blocking)."""
    try:
        if read_lifecycle(store).get("live"):
            set_lifecycle(store, live=True, stop_reason=STOP_SHUTDOWN)
    except Exception as exc:  # noqa: BLE001 - shutdown must continue
        logger.warning("lifecycle shutdown mark failed", error=str(exc))
    freeze(store)


class RunTracker:
    """Per-agent writer used by the session output sink.

    ``turn_started`` / ``turn_ended`` bracket a turn, ``touch`` is called on
    every recorded event and writes an ``active`` beat at most every
    :data:`BEAT_INTERVAL` seconds while a turn is open, ``stopped`` marks a
    stopped creature. ``submit`` runs the write (the store's affinity thread).
    """

    def __init__(self, store: Any, agent: str, submit) -> None:
        self._store = store
        self._agent = agent
        self._submit = submit
        self._in_turn = False
        self._last_beat = 0.0

    def _write(self, state: str, **kwargs: Any) -> None:
        self._submit(write_run, self._store, self._agent, state, **kwargs)

    def turn_started(self, turn_id: Any = None) -> None:
        self._in_turn = True
        self._last_beat = time.monotonic()
        self._write(STARTED, turn_id=turn_id)

    def touch(self) -> None:
        if not self._in_turn:
            return
        now = time.monotonic()
        if now - self._last_beat < BEAT_INTERVAL:
            return
        self._last_beat = now
        self._write(ACTIVE)

    def turn_ended(self) -> None:
        self._in_turn = False
        self._write(IDLE)

    def stopped(self) -> None:
        self._in_turn = False
        self._write(STOPPED)
