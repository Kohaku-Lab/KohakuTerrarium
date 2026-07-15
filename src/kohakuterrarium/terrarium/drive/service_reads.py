"""Authorize Drive history reads for local Terrarium services.

Progress, delivery, and audit reads share one authorization gate for owners,
assignees, and administrators. The mixin relies on its host service to resolve
the Drive runtime and owning graph manager.
"""

from typing import Any

from kohakuterrarium.terrarium.drive.acl import capabilities_for
from kohakuterrarium.terrarium.drive.errors import (
    DriveNotFoundError,
    DrivePermissionError,
)
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveDelivery,
    DriveProgress,
    DriveRecord,
)
from kohakuterrarium.terrarium.drive.policy import DriveCapability

# Evidence and history require owner, assignee, or administrative capability;
# basic graph read access is insufficient.
_HISTORY_READ_CAPS: frozenset[DriveCapability] = frozenset(
    {
        DriveCapability.UPDATE_OWNED,
        DriveCapability.MANAGE_ASSIGNED,
        DriveCapability.ADMIN,
    }
)


class DriveHistoryReadMixin:
    """Gate Drive progress, delivery, and audit history behind one ACL check."""

    _drive_runtime: Any
    _find_manager: Any

    async def _authorize_read(
        self, manager: Any, record: DriveRecord, actor: ActorRef, is_privileged: bool
    ) -> None:
        """Require owner, current assignee, or administrator history access."""
        assignment = await manager.get_assignment(record.drive_id)
        caps = capabilities_for(actor, record, assignment, is_privileged=is_privileged)
        if not (caps & _HISTORY_READ_CAPS):
            raise DrivePermissionError(
                f"actor {actor.format()!r} may not read this Drive's history"
            )

    async def _authorized_history(
        self, drive_id: str, actor: ActorRef | None, is_privileged: bool, fetch: Any
    ) -> Any:
        """Resolve and authorize a history read, allowing trusted local callers."""
        runtime = self._drive_runtime()
        found = await self._find_manager(runtime, drive_id)
        if found is None:
            raise DriveNotFoundError(f"no Drive {drive_id!r}")
        manager, record = found
        if actor is not None:
            await self._authorize_read(manager, record, actor, is_privileged)
        return await fetch(manager)

    async def list_drive_progress(
        self,
        drive_id: str,
        *,
        actor: ActorRef | None = None,
        is_privileged: bool = False,
    ) -> tuple[DriveProgress, ...]:
        return await self._authorized_history(
            drive_id, actor, is_privileged, lambda m: m.list_progress(drive_id)
        )

    async def list_drive_deliveries(
        self,
        drive_id: str,
        *,
        actor: ActorRef | None = None,
        is_privileged: bool = False,
    ) -> tuple[DriveDelivery, ...]:
        return await self._authorized_history(
            drive_id, actor, is_privileged, lambda m: m.list_deliveries(drive_id)
        )

    async def list_drive_audit(
        self,
        drive_id: str,
        *,
        actor: ActorRef | None = None,
        is_privileged: bool = False,
    ) -> tuple[Any, ...]:
        return await self._authorized_history(
            drive_id, actor, is_privileged, lambda m: m.repository.list_audit(drive_id)
        )


__all__ = ["DriveHistoryReadMixin"]
