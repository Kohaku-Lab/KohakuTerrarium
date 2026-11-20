"""Shutdown deadline, outcome report, and bounded joins for ``Agent.stop``."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

FORCE_SETTLE_SECONDS = 1.0
JOIN_SLACK_SECONDS = 0.5


@dataclass(frozen=True)
class ShutdownReport:
    """Caller-visible outcome of one agent shutdown.

    ``settled`` means every tracked task exited through ordinary cooperative
    cancellation. ``terminated`` means every tracked task has exited, however
    it got there. A forced or abandoned shutdown is never ``settled``.
    """

    settled: bool = True
    terminated: bool = True
    timed_out: bool = False
    forced: bool = False
    expired_stage: str | None = None
    abandoned: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    elapsed: float = 0.0


@dataclass
class AbandonedTask:
    """One task the shutdown stopped waiting for, with its report label."""

    label: str
    task: asyncio.Future


@dataclass
class ShutdownState:
    """Mutable bookkeeping shared by every caller of one shutdown."""

    deadline: "ShutdownDeadline"
    started_at: float = field(default_factory=time.monotonic)
    force_requested: bool = False
    forced: bool = False
    expired_stage: str | None = None
    abandoned: list[AbandonedTask] = field(default_factory=list)
    errors: list[BaseException] = field(default_factory=list)
    done: asyncio.Future | None = None
    report: ShutdownReport | None = None

    def live_abandoned(self) -> list[AbandonedTask]:
        """Abandoned tasks that have not exited yet."""
        return [item for item in self.abandoned if not item.task.done()]

    def build_report(self) -> ShutdownReport:
        """Snapshot the current outcome, re-checking abandoned tasks."""
        live = self.live_abandoned()
        incomplete = (
            bool(self.abandoned) or self.forced or self.expired_stage is not None
        )
        return ShutdownReport(
            settled=not incomplete,
            terminated=not live,
            timed_out=self.expired_stage is not None,
            forced=self.forced,
            expired_stage=self.expired_stage,
            abandoned=tuple(item.label for item in self.abandoned),
            errors=tuple(repr(err) for err in self.errors),
            elapsed=time.monotonic() - self.started_at,
        )


class ShutdownDeadline:
    """One monotonic grace deadline shared by every shutdown stage.

    A later request may only move the deadline earlier; it never resets or
    extends it. Joins wake when the deadline tightens.
    """

    def __init__(self, grace: float | None) -> None:
        self._at: float | None = None
        self._force_at: float | None = None
        self._changed = asyncio.Event()
        if grace is not None:
            self.tighten(grace)

    @property
    def bounded(self) -> bool:
        """Whether a finite deadline is in force."""
        return self._at is not None

    def tighten(self, grace: float) -> None:
        """Move the deadline to ``now + grace`` when that is earlier."""
        candidate = time.monotonic() + max(0.0, grace)
        if self._at is None or candidate < self._at:
            self._at = candidate
            self._changed.set()

    def wake(self) -> None:
        """Wake every join so it re-reads the deadline and force request."""
        self._changed.set()

    def open_force_window(self) -> None:
        """Start the single post-deadline window granted to forced cancellation."""
        if self._force_at is None:
            self._force_at = time.monotonic() + FORCE_SETTLE_SECONDS
            self._changed.set()

    def remaining(self, *, force: bool = False) -> float | None:
        """Seconds left before the deadline (or force window), ``None`` if unbounded."""
        target = self._force_at if force else self._at
        if target is None:
            return None
        return max(0.0, target - time.monotonic())

    def expired(self) -> bool:
        """Whether the grace deadline has passed."""
        remaining = self.remaining()
        return remaining is not None and remaining <= 0

    async def join(
        self, tasks: list[asyncio.Task] | set[asyncio.Task], *, force: bool = False
    ) -> set[asyncio.Task]:
        """Wait for ``tasks`` until the deadline and return those still pending.

        Never cancels anything. ``asyncio.wait`` returns at its timeout even
        when a task suppresses cancellation, so the wait is bounded as long as
        the event loop itself keeps running.
        """
        pending = {task for task in tasks if not task.done()}
        while pending:
            self._changed.clear()
            remaining = self.remaining(force=force)
            if remaining is not None and remaining <= 0:
                break
            changed = asyncio.ensure_future(self._changed.wait())
            try:
                done, _ = await asyncio.wait(
                    pending | {changed},
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                changed.cancel()
            pending -= done
        for task in tasks:
            if task.done() and not task.cancelled():
                task.exception()
        return pending

    async def wait_change(self, future: asyncio.Future, timeout: float | None) -> bool:
        """Wait for ``future`` or a deadline change; ``False`` means timed out."""
        self._changed.clear()
        changed = asyncio.ensure_future(self._changed.wait())
        try:
            done, _ = await asyncio.wait(
                {future, changed},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            changed.cancel()
        return bool(done)


def log_late_failure(task: asyncio.Task) -> None:
    """Log the eventual failure of a task the shutdown stopped waiting for."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(
            "Abandoned shutdown work failed after stop returned",
            task_name=task.get_name(),
            error=repr(exc),
        )


def spawn_stage(name: str, awaitable: Awaitable[Any]) -> asyncio.Future:
    """Run one shutdown stage as a task so its join can be bounded."""
    task = asyncio.ensure_future(awaitable)
    if isinstance(task, asyncio.Task):
        task.set_name(f"shutdown-stage:{name}")
    return task
