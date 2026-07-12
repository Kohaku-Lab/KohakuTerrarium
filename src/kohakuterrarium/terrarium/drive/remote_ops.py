"""Worker-routed Drive methods for :class:`RemoteTerrariumService` (design §9.2).

Each method translates a Drive service call into an APP request against the
worker's ``terrarium.runtime`` namespace (``drive_*`` verbs, handled by
:mod:`laboratory.adapters.terrarium_runtime_drive`) and translates the reply
back. Requests carry serializable DTOs only (packed via :mod:`drive.wire` /
:mod:`drive.wire_service`); no Python registration/record instance crosses the
wire. Typed Drive errors round-trip via :func:`terrarium.wire.drive_error_from_body`
so ``except DriveConflictError`` keeps working across the node hop.

``RemoteTerrariumService`` mixes this in ahead of
:class:`~kohakuterrarium.terrarium.drive.service_protocol.DriveServiceUnsupportedMixin`
so these real implementations win the MRO while the unsupported stubs remain a
defensive floor for any method not yet routed.
"""

from typing import Any

from kohakuterrarium.terrarium.drive.errors import (
    CrossNodeDriveNotSupportedError,
)
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveDelivery,
    DriveProgress,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest, DrivePatch
from kohakuterrarium.terrarium.drive.wire import (
    pack_create_request,
    pack_drive_patch,
    unpack_drive_audit,
    unpack_drive_delivery,
    unpack_drive_progress,
)
from kohakuterrarium.terrarium.drive.wire_service import (
    DriveRuntimeStatus,
    DriveView,
    unpack_drive_view,
    unpack_runtime_status,
)
from kohakuterrarium.terrarium.wire import drive_error_from_body


def _raise_for_drive_error(body: Any) -> Any:
    """Reconstruct a typed Drive error from a worker error envelope, else pass through."""
    err = drive_error_from_body(body)
    if err is not None:
        raise err
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        e = body["error"]
        raise RuntimeError(f"{e.get('kind', 'error')}: {e.get('message', '')}")
    return body


def _statuses_wire(statuses: "frozenset[DriveStatus] | None") -> list[str] | None:
    return None if statuses is None else sorted(s.value for s in statuses)


class RemoteDriveServiceMixin:
    """Drive service surface for a single worker node (over ``terrarium.runtime``)."""

    # Provided by RemoteTerrariumService.
    async def _req(self, type_: str, body: dict[str, Any]) -> Any: ...

    async def _drive_req(self, type_: str, body: dict[str, Any]) -> Any:
        return _raise_for_drive_error(await self._req(type_, body))

    # -- reads ---------------------------------------------------------------

    async def get_drive(
        self, drive_id: str, *, actor: ActorRef, is_privileged: bool = False
    ) -> DriveView | None:
        body = await self._drive_req(
            "drive_get",
            {
                "drive_id": drive_id,
                "actor": actor.format(),
                "is_privileged": is_privileged,
            },
        )
        view = body.get("view")
        return None if view is None else unpack_drive_view(view)

    async def list_drives(
        self,
        *,
        actor: ActorRef,
        graph_id: str | None = None,
        statuses: "frozenset[DriveStatus] | None" = None,
        kinds: frozenset[str] | None = None,
        assignee_creature_id: str | None = None,
        owner: ActorRef | None = None,
        include_terminal: bool = True,
        is_privileged: bool = False,
    ) -> tuple[DriveView, ...]:
        body = await self._drive_req(
            "drive_list",
            {
                "actor": actor.format(),
                "graph_id": graph_id,
                "statuses": _statuses_wire(statuses),
                "kinds": None if kinds is None else sorted(kinds),
                "assignee_creature_id": assignee_creature_id,
                "owner": None if owner is None else owner.format(),
                "include_terminal": include_terminal,
                "is_privileged": is_privileged,
            },
        )
        return tuple(unpack_drive_view(v) for v in body.get("views", []))

    async def assert_drive_in_graph(
        self,
        drive_id: str,
        graph_id: str,
        *,
        actor: ActorRef,
        is_privileged: bool = False,
    ) -> None:
        await self._drive_req(
            "drive_assert_in_graph",
            {
                "drive_id": drive_id,
                "graph_id": graph_id,
                "actor": actor.format(),
                "is_privileged": is_privileged,
            },
        )

    async def list_drive_progress(
        self,
        drive_id: str,
        *,
        actor: ActorRef | None = None,
        is_privileged: bool = False,
    ) -> tuple[DriveProgress, ...]:
        body = await self._drive_req(
            "drive_list_progress",
            {
                "drive_id": drive_id,
                "actor": None if actor is None else actor.format(),
                "is_privileged": is_privileged,
            },
        )
        return tuple(unpack_drive_progress(p) for p in body.get("progress", []))

    async def list_drive_deliveries(
        self,
        drive_id: str,
        *,
        actor: ActorRef | None = None,
        is_privileged: bool = False,
    ) -> tuple[DriveDelivery, ...]:
        body = await self._drive_req(
            "drive_list_deliveries",
            {
                "drive_id": drive_id,
                "actor": None if actor is None else actor.format(),
                "is_privileged": is_privileged,
            },
        )
        return tuple(unpack_drive_delivery(d) for d in body.get("deliveries", []))

    async def list_drive_audit(
        self,
        drive_id: str,
        *,
        actor: ActorRef | None = None,
        is_privileged: bool = False,
    ) -> tuple[Any, ...]:
        body = await self._drive_req(
            "drive_list_audit",
            {
                "drive_id": drive_id,
                "actor": None if actor is None else actor.format(),
                "is_privileged": is_privileged,
            },
        )
        return tuple(unpack_drive_audit(a) for a in body.get("audit", []))

    async def drive_runtime_status(self) -> DriveRuntimeStatus:
        body = await self._drive_req("drive_runtime_status", {})
        return unpack_runtime_status(body["status"])

    async def list_drive_registrations(self) -> tuple[dict[str, Any], ...]:
        body = await self._drive_req("drive_list_registrations", {})
        return tuple(dict(r) for r in body.get("registrations", []))

    # -- writes --------------------------------------------------------------

    async def create_drive(
        self,
        request: CreateDriveRequest,
        *,
        graph_id: str,
        actor: ActorRef,
        is_privileged: bool = False,
        operator: bool = False,
    ) -> DriveView:
        body = await self._drive_req(
            "drive_create",
            {
                "request": pack_create_request(request),
                "graph_id": graph_id,
                "actor": actor.format(),
                "is_privileged": is_privileged,
                "operator": operator,
            },
        )
        return unpack_drive_view(body["view"])

    async def update_drive(
        self,
        drive_id: str,
        patch: DrivePatch,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        body = await self._drive_req(
            "drive_update",
            {
                "drive_id": drive_id,
                "patch": pack_drive_patch(patch),
                "expected_revision": expected_revision,
                "actor": actor.format(),
                "idempotency_key": idempotency_key,
                "is_privileged": is_privileged,
            },
        )
        return unpack_drive_view(body["view"])

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
    ) -> DriveView:
        body = await self._drive_req(
            "drive_assign",
            {
                "drive_id": drive_id,
                "assignee_creature_id": assignee_creature_id,
                "assignee_graph_id": assignee_graph_id,
                "expected_revision": expected_revision,
                "actor": actor.format(),
                "idempotency_key": idempotency_key,
                "is_privileged": is_privileged,
                "operator": operator,
            },
        )
        return unpack_drive_view(body["view"])

    async def unassign_drive(
        self,
        drive_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        body = await self._drive_req(
            "drive_unassign",
            {
                "drive_id": drive_id,
                "expected_revision": expected_revision,
                "actor": actor.format(),
                "idempotency_key": idempotency_key,
                "is_privileged": is_privileged,
            },
        )
        return unpack_drive_view(body["view"])

    async def transfer_drive_owner(
        self,
        drive_id: str,
        new_owner: ActorRef,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        body = await self._drive_req(
            "drive_transfer_owner",
            {
                "drive_id": drive_id,
                "new_owner": new_owner.format(),
                "expected_revision": expected_revision,
                "actor": actor.format(),
                "idempotency_key": idempotency_key,
                "is_privileged": is_privileged,
            },
        )
        return unpack_drive_view(body["view"])

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
    ) -> DriveView:
        body = await self._drive_req(
            "drive_transition",
            {
                "drive_id": drive_id,
                "target_status": target_status.value,
                "expected_revision": expected_revision,
                "actor": actor.format(),
                "status_reason": status_reason,
                "idempotency_key": idempotency_key,
                "is_privileged": is_privileged,
            },
        )
        return unpack_drive_view(body["view"])

    async def wake_drive(
        self,
        drive_id: str,
        *,
        actor: ActorRef,
        expected_revision: int | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        body = await self._drive_req(
            "drive_wake",
            {
                "drive_id": drive_id,
                "actor": actor.format(),
                "expected_revision": expected_revision,
                "is_privileged": is_privileged,
            },
        )
        return unpack_drive_view(body["view"])

    async def report_drive_progress(
        self,
        drive_id: str,
        *,
        summary: str,
        evidence: dict[str, Any] | None,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveProgress:
        body = await self._drive_req(
            "drive_report_progress",
            {
                "drive_id": drive_id,
                "summary": summary,
                "evidence": evidence,
                "actor": actor.format(),
                "idempotency_key": idempotency_key,
                "is_privileged": is_privileged,
            },
        )
        return unpack_drive_progress(body["progress"])

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
    ) -> DriveView | dict[str, Any]:
        body = await self._drive_req(
            "drive_propose_transition",
            {
                "drive_id": drive_id,
                "target_status": target_status.value,
                "actor": actor.format(),
                "evidence": evidence,
                "reason": reason,
                "expected_revision": expected_revision,
                "is_privileged": is_privileged,
            },
        )
        if body.get("kind") == "view":
            return unpack_drive_view(body["view"])
        return dict(body.get("proposal", {}))

    async def approve_drive_proposal(
        self,
        proposal_id: str,
        *,
        actor: ActorRef,
        is_privileged: bool = False,
        operator: bool = False,
        drive_id: str | None = None,
        graph_id: str | None = None,
    ) -> DriveView:
        body = await self._drive_req(
            "drive_approve_proposal",
            {
                "proposal_id": proposal_id,
                "actor": actor.format(),
                "is_privileged": is_privileged,
                "operator": operator,
                "drive_id": drive_id,
                "graph_id": graph_id,
            },
        )
        return unpack_drive_view(body["view"])

    async def retire_drive(
        self,
        drive_id: str,
        *,
        expected_revision: int,
        actor: ActorRef,
        idempotency_key: str | None = None,
        is_privileged: bool = False,
    ) -> DriveView:
        body = await self._drive_req(
            "drive_retire",
            {
                "drive_id": drive_id,
                "expected_revision": expected_revision,
                "actor": actor.format(),
                "idempotency_key": idempotency_key,
                "is_privileged": is_privileged,
            },
        )
        return unpack_drive_view(body["view"])

    async def replay_drive_delivery(
        self,
        delivery_id: str,
        *,
        actor: ActorRef,
        is_privileged: bool = False,
        graph_id: str | None = None,
    ) -> DriveDelivery:
        body = await self._drive_req(
            "drive_replay_delivery",
            {
                "delivery_id": delivery_id,
                "actor": actor.format(),
                "is_privileged": is_privileged,
                "graph_id": graph_id,
            },
        )
        return unpack_drive_delivery(body["delivery"])

    async def locate_drive_proposal(self, proposal_id: str) -> bool:
        """Routing probe: does this worker hold a pending proposal? (read-only)."""
        body = await self._drive_req(
            "drive_locate_proposal", {"proposal_id": proposal_id}
        )
        return bool(body.get("hosted", False))

    async def locate_drive_delivery(self, delivery_id: str) -> bool:
        """Routing probe: does this worker host ``delivery_id``? (read-only)."""
        body = await self._drive_req(
            "drive_locate_delivery", {"delivery_id": delivery_id}
        )
        return bool(body.get("hosted", False))

    async def reconfigure_drive_runtime(
        self, registrations: Any, *, actor: ActorRef
    ) -> str:
        # Registration *instances* cannot cross the wire (design §8.6); a worker
        # is reconfigured through its node-targeted ``studio.settings`` apply,
        # which resolves names against that worker's installed packages.
        raise CrossNodeDriveNotSupportedError(
            "reconfigure_drive_runtime with registration instances is not routable "
            "cross-node; use the node-targeted Drive settings apply "
            "(Studio.nodes[node].settings.drives.apply)"
        )


__all__ = ["RemoteDriveServiceMixin"]
