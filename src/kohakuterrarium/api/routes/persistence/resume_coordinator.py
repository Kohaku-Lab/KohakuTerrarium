"""Per-session coordination for idempotent resume requests."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class ResumeCoordinator:
    """Share one in-flight resume operation for each session key.

    Waiters are shielded from the shared task so cancelling one request does
    not cancel the underlying resume or affect other waiters.
    """

    def __init__(self) -> None:
        self._in_flight: dict[
            tuple[asyncio.AbstractEventLoop, str], tuple[str, asyncio.Task[object]]
        ] = {}

    async def run(
        self,
        session_key: str,
        resume: Callable[[], Awaitable[T]],
        *,
        intent: str = "",
    ) -> T:
        """Return the shared result, rejecting a conflicting in-flight intent."""
        loop = asyncio.get_running_loop()
        key = (loop, session_key)
        current = self._in_flight.get(key)
        if current is not None and current[0] != intent:
            raise RuntimeError("conflicting resume request is already in progress")
        task = current[1] if current is not None else None
        if task is None:
            task = asyncio.create_task(resume())
            self._in_flight[key] = (intent, task)
            task.add_done_callback(
                lambda completed, flight_key=key: self._discard(flight_key, completed)
            )

        return await asyncio.shield(task)  # type: ignore[return-value]

    def _discard(
        self,
        key: tuple[asyncio.AbstractEventLoop, str],
        completed: asyncio.Task[object],
    ) -> None:
        """Remove ``completed`` without deleting a newer task for the key."""
        current = self._in_flight.get(key)
        if current is not None and current[1] is completed:
            self._in_flight.pop(key, None)


resume_coordinator = ResumeCoordinator()
