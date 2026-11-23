"""Small helpers for the background futures of the browser runtime."""

import asyncio


def _retrieve(future: asyncio.Future) -> None:
    if not future.cancelled():
        future.exception()


def discard(future: asyncio.Future) -> None:
    """Cancel ``future`` and mark whatever it ends with as retrieved."""
    future.cancel()
    future.add_done_callback(_retrieve)
