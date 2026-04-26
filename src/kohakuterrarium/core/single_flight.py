"""Single-flight dispatch helpers for background jobs.

A single-flight dispatcher guarantees that only one job of a given kind can be
started at once. Later attempts are ignored immediately: they are not queued,
coalesced, or retried automatically.
"""

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class SingleFlightLease:
    """Opaque lease identifying the currently-running single-flight job."""

    token: int


class SingleFlightDispatch:
    """Small thread-safe gate for fire-and-forget single-flight jobs."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._running = False
        self._token = 0

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def try_acquire(self) -> SingleFlightLease | None:
        """Acquire the flight if idle, else return ``None`` immediately."""
        with self._lock:
            if self._running:
                return None
            self._token += 1
            self._running = True
            return SingleFlightLease(self._token)

    def release(self, lease: SingleFlightLease | None) -> bool:
        """Release the active flight if the lease still matches it."""
        with self._lock:
            if not self._running:
                return False
            if lease is not None and lease.token != self._token:
                return False
            self._running = False
            return True

    def force_release(self) -> bool:
        """Release the active flight unconditionally."""
        with self._lock:
            if not self._running:
                return False
            self._running = False
            return True
