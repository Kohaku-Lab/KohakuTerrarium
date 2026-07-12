"""Drive service contract + the typed cross-node-unsupported stub mixin.

Split from :mod:`drive.service` (file-size cap):

- :class:`DriveServiceProtocol` — the DTO-based Drive method contract every
  :class:`~kohakuterrarium.terrarium.service.TerrariumService` implementation
  must satisfy (design §9.2);
- :class:`DriveServiceUnsupportedMixin` — typed stubs Remote / MultiNode inherit
  so an unrouted Drive call fails with :class:`CrossNodeDriveNotSupportedError`,
  never ``AttributeError`` (impl-plan rule §4.12). Real routing lands in Phase H.

The Local in-process implementation lives in :class:`drive.service.DriveServiceMixin`.
"""

from typing import Any, Protocol

from kohakuterrarium.terrarium.drive.errors import CrossNodeDriveNotSupportedError
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveDelivery,
    DriveProgress,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest, DrivePatch
from kohakuterrarium.terrarium.drive.wire_service import DriveRuntimeStatus, DriveView


class DriveServiceProtocol(Protocol):
    """DTO-based Drive operations on the terrarium service (design §9.2)."""

    # reads
    async def get_drive(
        self, drive_id: str, *, actor: ActorRef, is_privileged: bool = False
    ) -> DriveView | None: ...

    async def list_drives(
        self,
        *,
        actor: ActorRef,
        graph_id: str | None = None,
        statuses: frozenset[DriveStatus] | None = None,
        kinds: frozenset[str] | None = None,
        assignee_creature_id: str | None = None,
        owner: ActorRef | None = None,
        include_terminal: bool = True,
        is_privileged: bool = False,
    ) -> tuple[DriveView, ...]: ...

    async def assert_drive_in_graph(
        self,
        drive_id: str,
        graph_id: str,
        *,
        actor: ActorRef,
        is_privileged: bool = False,
    ) -> None: ...

    async def list_drive_progress(
        self,
        drive_id: str,
        *,
        actor: ActorRef | None = None,
        is_privileged: bool = False,
    ) -> tuple[DriveProgress, ...]: ...

    async def list_drive_deliveries(
        self,
        drive_id: str,
        *,
        actor: ActorRef | None = None,
        is_privileged: bool = False,
    ) -> tuple[DriveDelivery, ...]: ...

    async def list_drive_audit(
        self,
        drive_id: str,
        *,
        actor: ActorRef | None = None,
        is_privileged: bool = False,
    ) -> tuple[Any, ...]: ...

    async def drive_runtime_status(self) -> DriveRuntimeStatus: ...

    async def list_drive_registrations(self) -> tuple[dict[str, Any], ...]: ...

    # writes
    async def create_drive(
        self,
        request: CreateDriveRequest,
        *,
        graph_id: str,
        actor: ActorRef,
        is_privileged: bool = False,
        operator: bool = False,
    ) -> DriveView: ...

    async def update_drive(
        self,
        drive_id: str,
        patch: DrivePatch,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView: ...

    async def assign_drive(
        self,
        drive_id: str,
        assignee_creature_id: str,
        assignee_graph_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
        operator: bool = False,
    ) -> DriveView: ...

    async def unassign_drive(
        self,
        drive_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView: ...

    async def transfer_drive_owner(
        self,
        drive_id: str,
        new_owner: ActorRef,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView: ...

    async def transition_drive(
        self,
        drive_id: str,
        target_status: DriveStatus,
        *,
        expected_revision: int,
        actor: ActorRef,
        status_reason: str | None = None,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView: ...

    async def wake_drive(
        self,
        drive_id: str,
        *,
        actor: ActorRef,
        expected_revision: int | None = None,
        is_privileged: bool = False,
    ) -> DriveView: ...

    async def report_drive_progress(
        self,
        drive_id: str,
        *,
        summary: str,
        evidence: dict[str, Any] | None,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveProgress: ...

    async def propose_drive_transition(
        self,
        drive_id: str,
        target_status: DriveStatus,
        *,
        actor: ActorRef,
        evidence: dict[str, Any] | None = None,
        reason: str | None = None,
        expected_revision: int | None = None,
        is_privileged: bool = False,
    ) -> DriveView | dict[str, Any]: ...

    async def approve_drive_proposal(
        self,
        proposal_id: str,
        *,
        actor: ActorRef,
        is_privileged: bool = False,
        operator: bool = False,
        drive_id: str | None = None,
        graph_id: str | None = None,
    ) -> DriveView: ...

    async def retire_drive(
        self,
        drive_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView: ...

    async def replay_drive_delivery(
        self,
        delivery_id: str,
        *,
        actor: ActorRef,
        is_privileged: bool = False,
        graph_id: str | None = None,
    ) -> DriveDelivery: ...

    async def reconfigure_drive_runtime(
        self, registrations: Any, *, actor: ActorRef
    ) -> str: ...


def _unsupported(op: str) -> None:
    raise CrossNodeDriveNotSupportedError(
        f"drive service op {op!r} is not routed on this service yet (Phase H)"
    )


class DriveServiceUnsupportedMixin:
    """Typed Drive stubs for Remote / MultiNode services (Phase H does routing).

    Every method raises :class:`CrossNodeDriveNotSupportedError` so an unrouted
    call fails with a typed error instead of ``AttributeError``.
    """

    async def get_drive(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("get_drive")

    async def assert_drive_in_graph(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("assert_drive_in_graph")

    async def list_drives(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("list_drives")

    async def list_drive_progress(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("list_drive_progress")

    async def list_drive_deliveries(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("list_drive_deliveries")

    async def list_drive_audit(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("list_drive_audit")

    async def drive_runtime_status(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("drive_runtime_status")

    async def list_drive_registrations(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("list_drive_registrations")

    async def create_drive(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("create_drive")

    async def update_drive(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("update_drive")

    async def assign_drive(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("assign_drive")

    async def unassign_drive(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("unassign_drive")

    async def transfer_drive_owner(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("transfer_drive_owner")

    async def transition_drive(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("transition_drive")

    async def wake_drive(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("wake_drive")

    async def report_drive_progress(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("report_drive_progress")

    async def propose_drive_transition(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("propose_drive_transition")

    async def approve_drive_proposal(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("approve_drive_proposal")

    async def retire_drive(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("retire_drive")

    async def replay_drive_delivery(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("replay_drive_delivery")

    async def reconfigure_drive_runtime(self, *args: Any, **kwargs: Any) -> Any:
        _unsupported("reconfigure_drive_runtime")


__all__ = ["DriveServiceProtocol", "DriveServiceUnsupportedMixin"]
