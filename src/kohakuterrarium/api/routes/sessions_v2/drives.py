"""Expose graph-scoped Drive records through the session API.

Actors and authority come from authentication context, never request bodies. The
single-tenant console is an operator; multi-user callers act as themselves, with
administrators elevated to operator authority. List responses redact sensitive
fields, while detail access follows record ACLs. Typed Drive errors propagate to
the application-level HTTP mapper.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from starlette.requests import HTTPConnection

from kohakuterrarium.api.auth.dependencies import get_auth_config, get_optional_user
from kohakuterrarium.api.auth.models import User
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.studio.sessions import drives as _drives
from kohakuterrarium.terrarium.drive.errors import DriveError
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


def _actor_privilege(
    user: User | None, *, l4_enabled: bool
) -> tuple[ActorRef, bool, bool]:
    """Derive the actor and authority flags from authentication state.

    An absent user means a trusted operator only when multi-user auth is disabled.
    With multi-user auth enabled, an anonymous optional-auth caller remains
    unprivileged. Authenticated administrators receive graph and operator authority.
    """
    if user is None:
        if l4_enabled:
            return ActorRef("user", "anonymous"), False, False
        return ActorRef("user", "local"), True, True
    is_admin = user.role == "admin"
    return ActorRef("user", str(user.id)), is_admin, is_admin


def drive_principal(
    conn_info: HTTPConnection,
    user: User | None = Depends(get_optional_user),
) -> tuple[ActorRef, bool, bool]:
    """Return the request actor and Drive authority flags.

    Authentication configuration disambiguates the trusted local console from an
    anonymous optional-auth caller.
    """
    cfg = get_auth_config(conn_info)
    return _actor_privilege(user, l4_enabled=cfg.multi_user_enabled)


class CreateDriveBody(BaseModel):
    kind: str
    title: str
    scope_type: str = "graph"
    scope_id: str | None = None
    owner: str | None = None
    owner_scope: str | None = None
    spec: dict[str, Any] | None = None
    presentation: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    assignee_creature_id: str | None = None
    priority: int = 0
    not_before: str | None = None
    expires_at: str | None = None
    dependency_ids: list[str] | None = None
    policy_name: str | None = None
    policy_options: dict[str, Any] | None = None
    schema_version: int = 1
    idempotency_key: str | None = None


class UpdateDriveBody(BaseModel):
    expected_revision: int
    idempotency_key: str | None = None
    # Only explicitly supplied fields belong to the patch.
    title: str | None = None
    spec: dict[str, Any] | None = None
    presentation: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    priority: int | None = None
    status_reason: str | None = None
    not_before: str | None = None
    expires_at: str | None = None
    dependency_ids: list[str] | None = None
    policy_options: dict[str, Any] | None = None


class AssignDriveBody(BaseModel):
    assignee_creature_id: str
    assignee_graph_id: str | None = None
    expected_revision: int
    idempotency_key: str | None = None


class UnassignDriveBody(BaseModel):
    expected_revision: int
    idempotency_key: str | None = None


class OwnerDriveBody(BaseModel):
    new_owner: str
    expected_revision: int
    idempotency_key: str | None = None


class ProposeDriveBody(BaseModel):
    target_status: str
    evidence: dict[str, Any] | None = None
    reason: str | None = None
    expected_revision: int | None = None


class ApproveDriveBody(BaseModel):
    proposal_id: str


class TransitionDriveBody(BaseModel):
    target_status: str
    expected_revision: int
    status_reason: str | None = None
    idempotency_key: str | None = None


class WakeDriveBody(BaseModel):
    expected_revision: int | None = None
    idempotency_key: str | None = None


class ProgressDriveBody(BaseModel):
    summary: str
    evidence: dict[str, Any] | None = None
    idempotency_key: str | None = None


@router.get("/{session_id}/drives")
async def list_drives(
    session_id: str,
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    assignee: str | None = Query(default=None),
    mine: bool = Query(default=False),
    include_terminal: bool = Query(default=True),
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Return filtered, redacted Drive rows for a session graph."""
    actor, is_privileged, _ = principal
    try:
        rows = await _drives.list_records(
            service,
            graph_id=session_id,
            actor=actor,
            is_privileged=is_privileged,
            statuses=status,
            kinds=kind,
            owner=owner,
            assignee_creature_id=assignee,
            mine=mine,
            include_terminal=include_terminal,
        )
    except DriveError as exc:
        if "Drive runtime is not enabled" not in str(exc):
            raise
        rows = []
    return {"drives": rows}


@router.get("/{session_id}/drives/{drive_id}")
async def get_drive(
    session_id: str,
    drive_id: str,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Return full Drive detail or an ACL-redacted row."""
    actor, is_privileged, _ = principal
    record = await _drives.get_record(
        service,
        drive_id,
        actor=actor,
        is_privileged=is_privileged,
        graph_id=session_id,
    )
    if record is None:
        raise HTTPException(404, f"drive {drive_id!r} not found")
    return record


@router.get("/{session_id}/drives/{drive_id}/deliveries")
async def list_deliveries(
    session_id: str,
    drive_id: str,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Return ACL-gated retry, recovery, and dead-letter history."""
    actor, is_privileged, _ = principal
    return {
        "deliveries": await _drives.list_deliveries_records(
            service,
            drive_id,
            actor=actor,
            is_privileged=is_privileged,
            graph_id=session_id,
        )
    }


@router.get("/{session_id}/drives/{drive_id}/progress")
async def list_progress(
    session_id: str,
    drive_id: str,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Return ACL-gated, append-only progress observations."""
    actor, is_privileged, _ = principal
    return {
        "progress": await _drives.list_progress_records(
            service,
            drive_id,
            actor=actor,
            is_privileged=is_privileged,
            graph_id=session_id,
        )
    }


@router.get("/{session_id}/drives/{drive_id}/audit")
async def list_audit(
    session_id: str,
    drive_id: str,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Return the ACL-gated canonical mutation history."""
    actor, is_privileged, _ = principal
    return {
        "audit": await _drives.list_audit_records(
            service,
            drive_id,
            actor=actor,
            is_privileged=is_privileged,
            graph_id=session_id,
        )
    }


@router.post("/{session_id}/drives")
async def create_drive(
    session_id: str,
    body: CreateDriveBody,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Create a Drive in the session's graph, owned by the caller by default."""
    actor, is_privileged, operator = principal
    return await _drives.create_record(
        service,
        graph_id=session_id,
        actor=actor,
        body=body.model_dump(exclude_unset=True),
        is_privileged=is_privileged,
        operator=operator,
    )


@router.patch("/{session_id}/drives/{drive_id}")
async def update_drive(
    session_id: str,
    drive_id: str,
    body: UpdateDriveBody,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Apply a revision-checked patch without changing Drive identity."""
    actor, is_privileged, _ = principal
    changes = body.model_dump(exclude_unset=True)
    changes.pop("expected_revision", None)
    changes.pop("idempotency_key", None)
    return await _drives.update_record(
        service,
        drive_id,
        expected_revision=body.expected_revision,
        actor=actor,
        body=changes,
        idempotency_key=body.idempotency_key,
        is_privileged=is_privileged,
        graph_id=session_id,
    )


@router.post("/{session_id}/drives/{drive_id}/assign")
async def assign_drive(
    session_id: str,
    drive_id: str,
    body: AssignDriveBody,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Assign or reassign a Drive to a graph member."""
    actor, is_privileged, operator = principal
    return await _drives.assign_record(
        service,
        drive_id,
        assignee_creature_id=body.assignee_creature_id,
        assignee_graph_id=body.assignee_graph_id or session_id,
        expected_revision=body.expected_revision,
        actor=actor,
        idempotency_key=body.idempotency_key,
        is_privileged=is_privileged,
        operator=operator,
        graph_id=session_id,
    )


@router.post("/{session_id}/drives/{drive_id}/unassign")
async def unassign_drive(
    session_id: str,
    drive_id: str,
    body: UnassignDriveBody,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Remove a Drive's assignee with graph authority."""
    actor, is_privileged, _ = principal
    return await _drives.unassign_record(
        service,
        drive_id,
        expected_revision=body.expected_revision,
        actor=actor,
        idempotency_key=body.idempotency_key,
        is_privileged=is_privileged,
        graph_id=session_id,
    )


@router.post("/{session_id}/drives/{drive_id}/owner")
async def transfer_owner(
    session_id: str,
    drive_id: str,
    body: OwnerDriveBody,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Transfer Drive ownership through an explicit audited mutation."""
    actor, is_privileged, _ = principal
    return await _drives.transfer_owner_record(
        service,
        drive_id,
        new_owner=body.new_owner,
        expected_revision=body.expected_revision,
        actor=actor,
        idempotency_key=body.idempotency_key,
        is_privileged=is_privileged,
        graph_id=session_id,
    )


@router.post("/{session_id}/drives/{drive_id}/transition")
async def transition_drive(
    session_id: str,
    drive_id: str,
    body: TransitionDriveBody,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Apply a generic pause, resume, wait, block, or cancel transition."""
    actor, is_privileged, _ = principal
    return await _drives.transition_record(
        service,
        drive_id,
        target_status=body.target_status,
        expected_revision=body.expected_revision,
        actor=actor,
        status_reason=body.status_reason,
        idempotency_key=body.idempotency_key,
        is_privileged=is_privileged,
        graph_id=session_id,
    )


@router.post("/{session_id}/drives/{drive_id}/wake")
async def wake_drive(
    session_id: str,
    drive_id: str,
    body: WakeDriveBody,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Re-arm pursuit for a Drive within the session graph."""
    actor, is_privileged, _ = principal
    return await _drives.wake_record(
        service,
        drive_id,
        actor=actor,
        expected_revision=body.expected_revision,
        is_privileged=is_privileged,
        graph_id=session_id,
    )


@router.post("/{session_id}/drives/{drive_id}/propose")
async def propose_terminal(
    session_id: str,
    drive_id: str,
    body: ProposeDriveBody,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Propose a terminal transition (complete/fail).

    Returns the updated detail when policy accepts immediately, or a
    ``{proposal_id, ..., pending: true}`` envelope when a verifier must approve.
    """
    actor, is_privileged, _ = principal
    return await _drives.propose_terminal_record(
        service,
        drive_id,
        target_status=body.target_status,
        actor=actor,
        evidence=body.evidence,
        reason=body.reason,
        expected_revision=body.expected_revision,
        is_privileged=is_privileged,
        graph_id=session_id,
    )


@router.post("/{session_id}/drives/{drive_id}/approve")
async def approve_proposal(
    session_id: str,
    drive_id: str,
    body: ApproveDriveBody,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Approve a pending terminal proposal with verifier or operator authority."""
    actor, is_privileged, operator = principal
    return await _drives.approve_proposal_record(
        service,
        body.proposal_id,
        actor=actor,
        is_privileged=is_privileged,
        operator=operator,
        drive_id=drive_id,
        graph_id=session_id,
    )


@router.post("/{session_id}/drives/{drive_id}/progress")
async def report_progress(
    session_id: str,
    drive_id: str,
    body: ProgressDriveBody,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Append a progress observation without requiring a revision."""
    actor, is_privileged, _ = principal
    return await _drives.report_progress_record(
        service,
        drive_id,
        summary=body.summary,
        evidence=body.evidence,
        actor=actor,
        idempotency_key=body.idempotency_key,
        is_privileged=is_privileged,
        graph_id=session_id,
    )


@router.post("/{session_id}/drives/deliveries/{delivery_id}/replay")
async def replay_delivery(
    session_id: str,
    delivery_id: str,
    service: TerrariumService = Depends(get_service),
    principal: tuple[ActorRef, bool, bool] = Depends(drive_principal),
):
    """Replay a dead-letter delivery by minting a new delivery record."""
    actor, is_privileged, _ = principal
    return await _drives.replay_delivery_record(
        service,
        delivery_id,
        actor=actor,
        is_privileged=is_privileged,
        graph_id=session_id,
    )
