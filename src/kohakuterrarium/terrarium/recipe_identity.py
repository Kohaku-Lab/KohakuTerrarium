"""Atomic runtime identity reservation for recipe applications."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium


class RecipeIdentityReservations:
    """Reserve a recipe's runtime creature IDs in one short critical section."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._reserved: set[str] = set()

    @asynccontextmanager
    async def reserve_exact(
        self, engine: "Terrarium", runtime_ids: Iterable[str]
    ) -> AsyncIterator[tuple[str, ...]]:
        """Reserve exact persisted IDs before manifest adoption performs I/O."""
        requested = tuple(runtime_ids)
        if len(set(requested)) != len(requested):
            raise ValueError("duplicate runtime identity in one reservation")
        async with self._lock:
            unavailable = set(engine._creatures) | self._reserved
            collision = next((item for item in requested if item in unavailable), None)
            if collision is not None:
                raise ValueError(f"creature_id {collision!r} already exists")
            self._reserved.update(requested)
        try:
            yield requested
        finally:
            release = asyncio.create_task(self._release(requested))
            try:
                await asyncio.shield(release)
            except asyncio.CancelledError:
                await release
                raise

    @asynccontextmanager
    async def reserve(
        self,
        engine: "Terrarium",
        logical_names: Iterable[str],
    ) -> AsyncIterator[dict[str, str]]:
        """Yield logical-name to runtime-ID mappings and always release them.

        Only ID calculation and publication happen while holding ``_lock``.
        Recipe building, engine mutation, startup, and cleanup all run outside
        that critical section.
        """
        names = tuple(logical_names)
        if len(names) != len(set(names)):
            raise ValueError("logical creature names must be unique")

        async with self._lock:
            unavailable = set(engine._creatures) | self._reserved
            reserved = {name: _next_available_id(name, unavailable) for name in names}
            self._reserved.update(reserved.values())
        try:
            yield reserved
        finally:
            # Cancellation can arrive before or during lock acquisition. Keep
            # retrying in a separate task so no reservation can be stranded.
            release = asyncio.create_task(self._release(reserved.values()))
            try:
                await asyncio.shield(release)
            except asyncio.CancelledError:
                await release
                raise

    async def claim_exact(self, engine: "Terrarium", runtime_id: str) -> bool:
        """Claim an unreserved direct-add ID without holding startup I/O."""
        async with self._lock:
            if runtime_id in engine._creatures or runtime_id in self._reserved:
                return False
            self._reserved.add(runtime_id)
            return True

    async def release_exact(self, runtime_id: str) -> None:
        """Release one direct-add claim after insertion succeeds or fails."""
        await self._release([runtime_id])

    async def _release(self, runtime_ids: Iterable[str]) -> None:
        async with self._lock:
            self._reserved.difference_update(runtime_ids)


def _next_available_id(name: str, unavailable: set[str]) -> str:
    candidate = name
    suffix = 2
    while candidate in unavailable:
        candidate = f"{name}_{suffix}"
        suffix += 1
    unavailable.add(candidate)
    return candidate
