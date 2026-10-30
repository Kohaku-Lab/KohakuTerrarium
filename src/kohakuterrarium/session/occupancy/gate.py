"""Process-local admission gate for session-file occupancy.

Legacy enrollments may nest. NEW fast reads are isolated from enrolled
legacy occupancy and stay disabled unless a caller passes ``eligible=True``.
At most one NEW occupant is admitted per path. Callers prove source disposal
through :meth:`ResourceLease.finish`; this module never opens session files.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from kohakuterrarium.utils.fs_path import coerce_fs_path
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

KIND_FAST = "fast"
KIND_LEGACY = "legacy"
REASON_OK = "ok"
REASON_NOT_ELIGIBLE = "not_eligible"
REASON_FAST_ACTIVE = "fast_active"
REASON_LEGACY_PENDING = "legacy_pending"
REASON_LEGACY_ENROLLED = "legacy_enrolled"
REASON_LEGACY_RELEASE_UNCERTAIN = "legacy_release_uncertain"

__all__ = [
    "Admission",
    "GATE",
    "KIND_FAST",
    "KIND_LEGACY",
    "OccupancySnapshot",
    "REASON_FAST_ACTIVE",
    "REASON_LEGACY_ENROLLED",
    "REASON_LEGACY_PENDING",
    "REASON_LEGACY_RELEASE_UNCERTAIN",
    "REASON_NOT_ELIGIBLE",
    "REASON_OK",
    "ResourceGate",
    "ResourceLease",
    "ResourceReleaseError",
    "acquire_legacy",
    "resource_gate",
    "try_fast",
]


class ResourceReleaseError(RuntimeError):
    """Raised when NEW occupancy cannot be proven released."""


class ResourceLease:
    """Admission token released only after explicit source disposal."""

    __slots__ = ("kind", "token", "_gate", "_key")

    def __init__(self, gate: ResourceGate, token: int, kind: str, key: str) -> None:
        self._gate = gate
        self.token = token
        self.kind = kind
        self._key = key

    def finish(self, *, source_released: bool) -> None:
        """Record disposal. ``True`` releases occupancy; ``False`` is terminal."""
        self._gate._finish(self, source_released=source_released)


@dataclass(frozen=True, slots=True)
class Admission:
    """Result of a non-blocking NEW fast-read admission attempt."""

    lease: ResourceLease | None
    reason: str


@dataclass(frozen=True, slots=True)
class OccupancySnapshot:
    """Test-facing occupancy view for one normalized path."""

    legacy_active: int = 0
    legacy_pending: int = 0
    new_active: int = 0
    legacy_uncertain: bool = False
    new_uncertain: bool = False
    registered: bool = False


class _PathRecord:
    __slots__ = (
        "leases",
        "legacy_active",
        "legacy_pending",
        "legacy_uncertain",
        "new_active",
        "new_uncertain",
    )

    def __init__(self) -> None:
        self.legacy_active = 0
        self.legacy_pending = 0
        self.new_active = 0
        self.legacy_uncertain = False
        self.new_uncertain = False
        self.leases: dict[int, str] = {}

    def is_clean(self) -> bool:
        return (
            self.legacy_active == 0
            and self.legacy_pending == 0
            and self.new_active == 0
            and not self.legacy_uncertain
            and not self.new_uncertain
        )


def _normalize_path(path: str | Path) -> str:
    return os.path.normcase(str(coerce_fs_path(path).expanduser().resolve()))


class ResourceGate:
    """In-process occupancy registry for one session file identity."""

    def __init__(self, *, wait_log_interval: float = 1.0) -> None:
        self._cond = threading.Condition()
        self._records: dict[str, _PathRecord] = {}
        self._next_token = 0
        self.wait_log_interval = wait_log_interval

    def acquire_legacy(self, path: str | Path, operation: str) -> ResourceLease:
        """Enroll a nested legacy occupant, waiting only for NEW occupancy."""
        key = _normalize_path(path)
        interval = self.wait_log_interval
        wait_timeout = interval if interval > 0 else None
        started = time.monotonic()
        last_log = 0.0
        pending_enrolled = False
        try:
            while True:
                log_fields: dict[str, object] | None = None
                with self._cond:
                    rec = self._records.get(key)
                    if rec is None:
                        rec = _PathRecord()
                        self._records[key] = rec
                    if rec.new_uncertain:
                        raise ResourceReleaseError(
                            f"NEW occupancy release is uncertain: {key}"
                        )
                    if not pending_enrolled:
                        rec.legacy_pending += 1
                        pending_enrolled = True
                    if rec.new_active == 0:
                        rec.legacy_pending -= 1
                        pending_enrolled = False
                        rec.legacy_active += 1
                        return self._issue(rec, key, KIND_LEGACY)
                    now = time.monotonic()
                    if interval > 0 and now - last_log >= interval:
                        last_log = now
                        log_fields = {
                            "path": key,
                            "operation": operation,
                            "elapsed": now - started,
                        }
                    else:
                        self._cond.wait(timeout=wait_timeout)
                        continue
                # Logging can block; keep pending occupancy and recheck after.
                if log_fields is not None:
                    logger.debug(
                        "Waiting for NEW occupancy to drain",
                        **log_fields,
                    )
        except BaseException:
            if pending_enrolled:
                with self._cond:
                    rec = self._records.get(key)
                    if rec is not None:
                        rec.legacy_pending -= 1
                        self._retire_if_clean(key, rec)
            raise

    def try_fast(self, path: str | Path, *, eligible: bool = False) -> Admission:
        """Admit one NEW occupant immediately, or decline without waiting."""
        if not eligible:
            return Admission(None, REASON_NOT_ELIGIBLE)
        key = _normalize_path(path)
        with self._cond:
            rec = self._records.get(key)
            if rec is not None and rec.new_uncertain:
                raise ResourceReleaseError(f"NEW occupancy release is uncertain: {key}")
            if rec is None:
                rec = _PathRecord()
                self._records[key] = rec
            if rec.legacy_uncertain:
                return Admission(None, REASON_LEGACY_RELEASE_UNCERTAIN)
            if rec.legacy_pending > 0:
                return Admission(None, REASON_LEGACY_PENDING)
            if rec.legacy_active > 0:
                return Admission(None, REASON_LEGACY_ENROLLED)
            if rec.new_active > 0:
                return Admission(None, REASON_FAST_ACTIVE)
            rec.new_active += 1
            return Admission(self._issue(rec, key, KIND_FAST), REASON_OK)

    def snapshot(self, path: str | Path) -> OccupancySnapshot:
        """Return occupancy counts for ``path`` without creating a record."""
        key = _normalize_path(path)
        with self._cond:
            rec = self._records.get(key)
            if rec is None:
                return OccupancySnapshot()
            return OccupancySnapshot(
                legacy_active=rec.legacy_active,
                legacy_pending=rec.legacy_pending,
                new_active=rec.new_active,
                legacy_uncertain=rec.legacy_uncertain,
                new_uncertain=rec.new_uncertain,
                registered=True,
            )

    def _issue(self, rec: _PathRecord, key: str, kind: str) -> ResourceLease:
        self._next_token += 1
        token = self._next_token
        rec.leases[token] = kind
        return ResourceLease(self, token, kind, key)

    def _finish(self, lease: ResourceLease, *, source_released: bool) -> None:
        with self._cond:
            rec = self._records.get(lease._key)
            if rec is None:
                return
            kind = rec.leases.pop(lease.token, None)
            if kind is None:
                return
            if kind == KIND_LEGACY:
                rec.legacy_active = max(0, rec.legacy_active - 1)
                if not source_released:
                    rec.legacy_uncertain = True
            else:
                rec.new_active = max(0, rec.new_active - 1)
                if not source_released:
                    rec.new_uncertain = True
            self._cond.notify_all()
            self._retire_if_clean(lease._key, rec)

    def _retire_if_clean(self, key: str, rec: _PathRecord) -> None:
        if rec.is_clean():
            self._records.pop(key, None)


GATE = ResourceGate()
resource_gate = GATE


def acquire_legacy(path: str | Path, operation: str) -> ResourceLease:
    """Enroll ``path`` on the process-wide gate."""
    return GATE.acquire_legacy(path, operation)


def try_fast(path: str | Path, *, eligible: bool = False) -> Admission:
    """Attempt NEW admission on the process-wide gate."""
    return GATE.try_fast(path, eligible=eligible)
