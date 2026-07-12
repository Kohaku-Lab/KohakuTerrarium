"""ACL-gated Drive history reads for ``LocalTerrariumService`` (design §12.1).

Split out of :mod:`drive.service` (file-size cap): the progress / delivery /
audit reads share one authorization gate (owner / assignee / admin), so they
live together in :class:`DriveHistoryReadMixin`. It relies on the host service
(``DriveServiceMixin``) for ``_drive_runtime`` / ``_find_manager``; both mixins
are combined on ``LocalTerrariumService``.
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

# Capabilities that may read a Drive's evidence/history (owner / assignee /
# admin). A bare graph member holds only ``READ`` (list rows), never these.
_HISTORY_READ_CAPS: frozenset[DriveCapability] = frozenset(
    {
        DriveCapability.UPDATE_OWNED,
        DriveCapability.MANAGE_ASSIGNED,
        DriveCapability.ADMIN,
    }
)


class DriveHistoryReadMixin:
    """Progress / delivery / audit reads behind a single record-ACL gate."""

    _drive_runtime: Any
    _find_manager: Any

    async def _authorize_read(
        self, manager: Any, record: DriveRecord, actor: ActorRef, is_privileged: bool
    ) -> None:
        """Fail closed unless ``actor`` may read this Drive's evidence/history.

        Owner / current assignee / admin only; a bare graph member is denied
        (evidence is as sensitive as the detail spec, §12.1).
        """
        assignment = await manager.get_assignment(record.drive_id)
        caps = capabilities_for(actor, record, assignment, is_privileged=is_privileged)
        if not (caps & _HISTORY_READ_CAPS):
            raise DrivePermissionError(
                f"actor {actor.format()!r} may not read this Drive's history"
            )

    async def _authorized_history(
        self, drive_id: str, actor: ActorRef | None, is_privileged: bool, fetch: Any
    ) -> Any:
        """Resolve + ACL-gate a history read; ``actor is None`` is trusted local."""
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
