"""Worker-side ``drive_*`` verb handlers for the ``terrarium.runtime`` adapter.

:class:`TerrariumRuntimeAdapter` delegates every ``drive_*`` APP type here so the
main adapter stays under the file-size cap. Each verb runs against a
:class:`~kohakuterrarium.terrarium.service.LocalTerrariumService` bound to the
worker's engine (the same in-process Drive surface Studio uses on a single host)
and packs results via the versioned Drive wire DTOs.

The actor on each request was derived by the *authenticated host* from its own
authenticated context and travels the trusted host→worker Lab link; the worker
re-parses it for the op but never invents authority from it. Creature/tool Drive
calls on this worker use the in-process service directly (actor from
``ToolContext``) and never reach this wire path (design §13).

Typed Drive errors are caught here and packed with
:func:`terrarium.wire.pack_drive_error` so the controller reconstructs the exact
subtype; only an unexpected non-Drive error escapes to the generic ``engine``
envelope.
"""

from typing import Any

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory._internal.protocol import HOST_NODE_ID
from kohakuterrarium.terrarium.drive.errors import DriveDeliveryError, DriveError
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.wire import (
    pack_drive_audit,
    pack_drive_delivery,
    pack_drive_progress,
    unpack_create_request,
    unpack_drive_patch,
)
from kohakuterrarium.terrarium.drive.wire_service import (
    pack_drive_view,
    pack_runtime_status,
)
from kohakuterrarium.terrarium.wire import pack_drive_error

_DRIVE_VERB_PREFIX = "drive_"


def is_drive_verb(msg_type: str) -> bool:
    return msg_type.startswith(_DRIVE_VERB_PREFIX)


def _actor(body: dict[str, Any]) -> ActorRef:
    return ActorRef.parse(body["actor"])


def _opt_actor(value: Any) -> ActorRef | None:
    return None if value is None else ActorRef.parse(value)


def _statuses(value: Any) -> "frozenset[DriveStatus] | None":
    return None if value is None else frozenset(DriveStatus(v) for v in value)


def _runtime_enabled(service: Any) -> bool:
    return getattr(service.engine, "drives", None) is not None


async def handle_drive_request(service: Any, msg: AppMessage) -> dict[str, Any]:
    """Run one ``drive_*`` verb against ``service`` (a LocalTerrariumService).

    Always returns a response dict; a Drive failure becomes a typed error
    envelope rather than propagating (so the generic KeyError/ValueError mapping
    in the outer dispatcher never mislabels a Drive error).

    Drive verbs carry ``actor`` / ``is_privileged`` / ``operator`` authority that
    only the authenticated host may assert. A worker never trusts a peer for
    them (R1-04): a non-host origin is refused before any dispatch, so a forged
    ``from_node`` cannot drive a mutation. The host ACL already blocks such
    client-to-client forwards; this is the fail-closed floor.
    """
    if msg.sender_node != HOST_NODE_ID:
        return {
            "error": {
                "kind": "forbidden",
                "message": (
                    f"drive verb {msg.type!r} refused from non-host origin "
                    f"{msg.sender_node!r}"
                ),
            }
        }
    try:
        return await _dispatch(service, msg)
    except DriveError as exc:
        return {"error": pack_drive_error(exc)}


async def _dispatch(service: Any, msg: AppMessage) -> dict[str, Any]:
    body = msg.body
    match msg.type:
        # -- reads ----------------------------------------------------------
        case "drive_get":
            if not _runtime_enabled(service):
                return {"view": None}
            view = await service.get_drive(
                body["drive_id"],
                actor=_actor(body),
                is_privileged=body.get("is_privileged", False),
            )
            return {"view": None if view is None else pack_drive_view(view)}

        case "drive_list":
            if not _runtime_enabled(service):
                return {"views": []}
            views = await service.list_drives(
                actor=_actor(body),
                graph_id=body.get("graph_id"),
                statuses=_statuses(body.get("statuses")),
                kinds=(None if body.get("kinds") is None else frozenset(body["kinds"])),
                assignee_creature_id=body.get("assignee_creature_id"),
                owner=_opt_actor(body.get("owner")),
                include_terminal=body.get("include_terminal", True),
                is_privileged=body.get("is_privileged", False),
            )
            return {"views": [pack_drive_view(v) for v in views]}

        case "drive_assert_in_graph":
            await service.assert_drive_in_graph(
                body["drive_id"],
                body["graph_id"],
                actor=_actor(body),
                is_privileged=body.get("is_privileged", False),
            )
            return {"ok": True}

        case "drive_list_progress":
            progress = await service.list_drive_progress(
                body["drive_id"],
                actor=_opt_actor(body.get("actor")),
                is_privileged=body.get("is_privileged", False),
            )
            return {"progress": [pack_drive_progress(p) for p in progress]}

        case "drive_list_deliveries":
            deliveries = await service.list_drive_deliveries(
                body["drive_id"],
                actor=_opt_actor(body.get("actor")),
                is_privileged=body.get("is_privileged", False),
            )
            return {"deliveries": [pack_drive_delivery(d) for d in deliveries]}

        case "drive_list_audit":
            audit = await service.list_drive_audit(
                body["drive_id"],
                actor=_opt_actor(body.get("actor")),
                is_privileged=body.get("is_privileged", False),
            )
            return {"audit": [pack_drive_audit(a) for a in audit]}

        case "drive_runtime_status":
            status = await service.drive_runtime_status()
            return {"status": pack_runtime_status(status)}

        case "drive_list_registrations":
            regs = await service.list_drive_registrations()
            return {"registrations": [dict(r) for r in regs]}

        case "drive_locate_proposal":
            if not _runtime_enabled(service):
                return {"hosted": False}
            try:
                await service._proposal_for(service.engine.drives, body["proposal_id"])
                return {"hosted": True}
            except DriveError:
                return {"hosted": False}

        case "drive_locate_delivery":
            if not _runtime_enabled(service):
                return {"hosted": False}
            try:
                await service._manager_for_delivery(
                    service.engine.drives, body["delivery_id"]
                )
                return {"hosted": True}
            except DriveDeliveryError:
                return {"hosted": False}

        # -- writes ---------------------------------------------------------
        case "drive_create":
            view = await service.create_drive(
                unpack_create_request(body["request"]),
                graph_id=body["graph_id"],
                actor=_actor(body),
                is_privileged=body.get("is_privileged", False),
                operator=body.get("operator", False),
            )
            return {"view": pack_drive_view(view)}

        case "drive_update":
            view = await service.update_drive(
                body["drive_id"],
                unpack_drive_patch(body["patch"]),
                expected_revision=body["expected_revision"],
                actor=_actor(body),
                idempotency_key=body.get("idempotency_key"),
                is_privileged=body.get("is_privileged", False),
            )
            return {"view": pack_drive_view(view)}

        case "drive_assign":
            view = await service.assign_drive(
                body["drive_id"],
                body["assignee_creature_id"],
                body["assignee_graph_id"],
                expected_revision=body["expected_revision"],
                actor=_actor(body),
                idempotency_key=body.get("idempotency_key"),
                is_privileged=body.get("is_privileged", False),
                operator=body.get("operator", False),
            )
            return {"view": pack_drive_view(view)}

        case "drive_unassign":
            view = await service.unassign_drive(
                body["drive_id"],
                expected_revision=body["expected_revision"],
                actor=_actor(body),
                idempotency_key=body.get("idempotency_key"),
                is_privileged=body.get("is_privileged", False),
            )
            return {"view": pack_drive_view(view)}

        case "drive_transfer_owner":
            view = await service.transfer_drive_owner(
                body["drive_id"],
                ActorRef.parse(body["new_owner"]),
                expected_revision=body["expected_revision"],
                actor=_actor(body),
                idempotency_key=body.get("idempotency_key"),
                is_privileged=body.get("is_privileged", False),
            )
            return {"view": pack_drive_view(view)}

        case "drive_transition":
            view = await service.transition_drive(
                body["drive_id"],
                DriveStatus(body["target_status"]),
                expected_revision=body["expected_revision"],
                actor=_actor(body),
                status_reason=body.get("status_reason"),
                idempotency_key=body.get("idempotency_key"),
                is_privileged=body.get("is_privileged", False),
            )
            return {"view": pack_drive_view(view)}

        case "drive_wake":
            view = await service.wake_drive(
                body["drive_id"],
                actor=_actor(body),
                expected_revision=body.get("expected_revision"),
                is_privileged=body.get("is_privileged", False),
            )
            return {"view": pack_drive_view(view)}

        case "drive_report_progress":
            progress = await service.report_drive_progress(
                body["drive_id"],
                summary=body["summary"],
                evidence=body.get("evidence"),
                actor=_actor(body),
                idempotency_key=body.get("idempotency_key"),
                is_privileged=body.get("is_privileged", False),
            )
            return {"progress": pack_drive_progress(progress)}

        case "drive_propose_transition":
            result = await service.propose_drive_transition(
                body["drive_id"],
                DriveStatus(body["target_status"]),
                actor=_actor(body),
                evidence=body.get("evidence"),
                reason=body.get("reason"),
                expected_revision=body.get("expected_revision"),
                is_privileged=body.get("is_privileged", False),
            )
            if isinstance(result, dict):
                return {"kind": "proposal", "proposal": result}
            return {"kind": "view", "view": pack_drive_view(result)}

        case "drive_approve_proposal":
            view = await service.approve_drive_proposal(
                body["proposal_id"],
                actor=_actor(body),
                is_privileged=body.get("is_privileged", False),
                operator=body.get("operator", False),
                drive_id=body.get("drive_id"),
                graph_id=body.get("graph_id"),
            )
            return {"view": pack_drive_view(view)}

        case "drive_retire":
            view = await service.retire_drive(
                body["drive_id"],
                expected_revision=body["expected_revision"],
                actor=_actor(body),
                idempotency_key=body.get("idempotency_key"),
                is_privileged=body.get("is_privileged", False),
            )
            return {"view": pack_drive_view(view)}

        case "drive_replay_delivery":
            delivery = await service.replay_drive_delivery(
                body["delivery_id"],
                actor=_actor(body),
                is_privileged=body.get("is_privileged", False),
                graph_id=body.get("graph_id"),
            )
            return {"delivery": pack_drive_delivery(delivery)}

        case _:
            return {
                "error": {
                    "kind": "unknown_type",
                    "message": f"unsupported drive verb: {msg.type!r}",
                }
            }


__all__ = ["handle_drive_request", "is_drive_verb"]
