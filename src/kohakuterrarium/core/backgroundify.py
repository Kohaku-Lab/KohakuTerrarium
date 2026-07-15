"""
Backgroundify — mid-flight task promotion from direct to background.

Wraps an ``asyncio.Task`` so it can be awaited normally (direct mode)
or promoted to background at any time.  When promoted, the awaiter
receives a ``PromotionResult`` placeholder and the task continues
running independently, firing a callback on completion.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PromotionResult:
    """Sentinel returned by ``BackgroundifyHandle.wait()`` when promoted."""

    job_id: str
    message: str = "Task promoted to background. Result arrives later."


class BackgroundifyHandle:
    """Wrap an asyncio task with mid-flight promotion to background."""

    __slots__ = (
        "_task",
        "_job_id",
        "_on_bg_complete",
        "_promoted",
        "_promotion_event",
        "_completed",
        "_result",
    )

    def __init__(
        self,
        task: asyncio.Task,
        job_id: str,
        on_bg_complete: Callable[[str, Any], Awaitable[None]] | None = None,
    ):
        self._task = task
        self._job_id = job_id
        self._on_bg_complete = on_bg_complete
        self._promoted = False
        self._promotion_event = asyncio.Event()
        self._completed = False
        self._result: Any = None

        self._task.add_done_callback(self._on_task_done)

    @property
    def job_id(self) -> str:
        return self._job_id

    @property
    def promoted(self) -> bool:
        return self._promoted

    @property
    def done(self) -> bool:
        return self._task.done()

    @property
    def task(self) -> asyncio.Task:
        """The underlying asyncio task (for cancellation)."""
        return self._task

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Called when the underlying task completes."""
        self._completed = True
        try:
            self._result = task.result()
        except (asyncio.CancelledError, Exception) as e:
            self._result = e

        if self._promoted and self._on_bg_complete:
            asyncio.create_task(self._on_bg_complete(self._job_id, self._result))

    def promote(self) -> bool:
        """Promote to background. Returns False if task already completed."""
        if self._completed:
            return False
        self._promoted = True
        self._promotion_event.set()
        logger.info("Task promoted to background", job_id=self._job_id)
        return True

    async def wait(self) -> Any:
        """Wait for completion OR promotion (whichever comes first).

        Returns the real task result if it completes first,
        or ``PromotionResult`` if ``promote()`` is called first.
        """
        # If already promoted (background_init), return immediately
        if self._promoted:
            return PromotionResult(job_id=self._job_id)

        # If already done, return result
        if self._completed:
            return self._result

        # Race between task completion and promotion signal
        done_future = asyncio.ensure_future(asyncio.shield(self._task))
        promote_future = asyncio.ensure_future(self._promotion_event.wait())

        done, pending = await asyncio.wait(
            {done_future, promote_future},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for p in pending:
            p.cancel()
            try:
                await p
            except asyncio.CancelledError:
                pass

        if self._promoted:
            return PromotionResult(job_id=self._job_id)

        return self._task.result()


def backgroundify(
    task: asyncio.Task,
    job_id: str,
    on_bg_complete: Callable[[str, Any], Awaitable[None]] | None = None,
    background_init: bool = False,
) -> BackgroundifyHandle:
    """Wrap a task with background promotion capability.

    Args:
        task: The asyncio.Task to wrap.
        job_id: Job identifier for tracking.
        on_bg_complete: Callback fired when a promoted task completes.
        background_init: If True, promote immediately (= current background mode).
    """
    handle = BackgroundifyHandle(task, job_id, on_bg_complete)
    if background_init:
        handle.promote()
    return handle
