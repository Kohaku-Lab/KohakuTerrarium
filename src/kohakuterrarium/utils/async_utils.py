"""
Async utilities for KohakuTerrarium.

Provides common async patterns and helpers.
"""

import asyncio
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


async def run_with_timeout(
    coro: Awaitable[T],
    timeout: float,
    default: T | None = None,
) -> T | None:
    """
    Run a coroutine with a timeout.

    Args:
        coro: Coroutine to run
        timeout: Timeout in seconds
        default: Value to return on timeout (default: None)

    Returns:
        Coroutine result or default on timeout
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Coroutine timed out", timeout=timeout)
        return default


async def gather_with_concurrency(
    limit: int,
    *coros: Awaitable[T],
) -> list[T | BaseException]:
    """
    Run coroutines with limited concurrency.

    Args:
        limit: Maximum number of concurrent coroutines
        *coros: Coroutines to run

    Returns:
        List of results (or exceptions)
    """
    semaphore = asyncio.Semaphore(limit)

    async def limited_coro(coro: Awaitable[T]) -> T:
        async with semaphore:
            return await coro

    return await asyncio.gather(
        *[limited_coro(c) for c in coros],
        return_exceptions=True,
    )


async def retry_async(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential: bool = True,
    **kwargs: Any,
) -> T:
    """
    Retry an async function with exponential backoff.

    Args:
        func: Async function to retry
        *args: Positional arguments for func
        max_attempts: Maximum number of attempts
        base_delay: Initial delay between retries
        max_delay: Maximum delay between retries
        exponential: Use exponential backoff (default: True)
        **kwargs: Keyword arguments for func

    Returns:
        Result from successful call

    Raises:
        Last exception if all attempts fail
    """
    last_exception: BaseException | None = None
    delay = base_delay

    for attempt in range(1, max_attempts + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt == max_attempts:
                logger.error(
                    "All retry attempts failed",
                    func=func.__name__,
                    attempts=max_attempts,
                    error=str(e),
                )
                raise

            logger.warning(
                "Retry attempt failed",
                func=func.__name__,
                attempt=attempt,
                error=str(e),
                next_delay=delay,
            )

            await asyncio.sleep(delay)

            if exponential:
                delay = min(delay * 2, max_delay)

    # Should never reach here, but for type checker
    raise last_exception or RuntimeError("Unexpected retry state")


async def collect_async_iterator(
    iterator: AsyncIterator[T],
    max_items: int | None = None,
) -> list[T]:
    """
    Collect all items from an async iterator into a list.

    Args:
        iterator: Async iterator to collect from
        max_items: Maximum items to collect (default: unlimited)

    Returns:
        List of collected items
    """
    items: list[T] = []
    count = 0
    async for item in iterator:
        items.append(item)
        count += 1
        if max_items is not None and count >= max_items:
            break
    return items


async def first_result(
    *coros: Awaitable[T],
    timeout: float | None = None,
) -> T:
    """
    Return the result of the first coroutine to complete.

    Cancels remaining coroutines after first completes.

    Args:
        *coros: Coroutines to race
        timeout: Optional timeout

    Returns:
        Result from first completed coroutine

    Raises:
        asyncio.TimeoutError if timeout reached
        Exception from first coroutine if it raises
    """
    tasks = [asyncio.create_task(c) for c in coros]

    try:
        done, pending = await asyncio.wait(
            tasks,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel pending tasks
        for task in pending:
            task.cancel()

        if not done:
            raise asyncio.TimeoutError("No coroutine completed in time")

        # Return first result (or raise its exception)
        return done.pop().result()
    except Exception as e:
        # Ensure all tasks are cancelled on error; re-raised below
        logger.debug("race() failed, cancelling remaining tasks", error=str(e))
        for task in tasks:
            if not task.done():
                task.cancel()
        raise


class AsyncQueue:
    """
    Simple async queue wrapper with additional utilities.

    Provides:
    - put/get with optional timeout
    - peek without removing
    - batch get operations
    """

    def __init__(self, maxsize: int = 0):
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)

    async def put(self, item: T, timeout: float | None = None) -> None:
        """Put an item into the queue."""
        if timeout is not None:
            await asyncio.wait_for(self._queue.put(item), timeout=timeout)
        else:
            await self._queue.put(item)

    async def get(self, timeout: float | None = None) -> T:
        """Get an item from the queue."""
        if timeout is not None:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        return await self._queue.get()

    def put_nowait(self, item: T) -> None:
        """Put an item without waiting."""
        self._queue.put_nowait(item)

    def get_nowait(self) -> T:
        """Get an item without waiting."""
        return self._queue.get_nowait()

    async def get_batch(
        self,
        max_items: int,
        timeout: float | None = None,
    ) -> list[T]:
        """
        Get up to max_items from queue.

        Waits for at least one item, then collects any immediately available.
        """
        items: list[T] = []

        # Wait for first item
        first = await self.get(timeout=timeout)
        items.append(first)

        # Collect any immediately available
        while len(items) < max_items:
            try:
                item = self.get_nowait()
                items.append(item)
            except asyncio.QueueEmpty:
                break

        return items

    def empty(self) -> bool:
        """Check if queue is empty."""
        return self._queue.empty()

    def qsize(self) -> int:
        """Get current queue size."""
        return self._queue.qsize()

    def task_done(self) -> None:
        """Mark a task as done."""
        self._queue.task_done()

    async def join(self) -> None:
        """Wait for all tasks to complete."""
        await self._queue.join()


async def to_thread(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """
    Run a blocking function in a thread pool.

    Wrapper around asyncio.to_thread for sync tool execution.

    Args:
        func: Blocking function to run
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        Function result
    """
    return await asyncio.to_thread(func, *args, **kwargs)
