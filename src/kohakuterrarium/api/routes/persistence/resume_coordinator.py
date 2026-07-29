"""Per-session coordination for idempotent resume requests."""

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from kohakuterrarium.session.store import SessionStore

T = TypeVar("T")


def conversation_coordination_key(
    conversation_id: str,
    session_dir: str | Path,
) -> str:
    """Return a request-scope key for a known stable conversation identity."""
    namespace = os.path.normcase(
        str(Path(session_dir).expanduser().resolve(strict=False))
    )
    return f"{namespace}:conversation:{conversation_id}"


def session_coordination_key(path: str | Path, session_dir: str | Path) -> str:
    """Return one request-scope key for every file in a persisted conversation."""
    namespace = os.path.normcase(
        str(Path(session_dir).expanduser().resolve(strict=False))
    )
    resolved = os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))
    candidate = Path(path)
    try:
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        store = SessionStore.open_readonly(candidate)
        try:
            conversation_id = str(store.meta.get("conversation_id") or "")
        finally:
            store.close(update_status=False)
    except Exception:  # noqa: BLE001 - corrupt stores fall back to path identity
        conversation_id = ""
    if conversation_id:
        return conversation_coordination_key(conversation_id, session_dir)
    return f"{namespace}:path:{resolved}"


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
