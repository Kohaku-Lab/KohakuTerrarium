"""Drive record HTTP surface (design §9, §12; impl-plan Phase J).

Mounted under ``/api/sessions`` → ``/api/sessions/{sid}/drives*``. A *session* is
a Terrarium graph, so ``session_id`` is the ``graph_id`` the operations pass
through. Every handler is a thin adapter over
:mod:`kohakuterrarium.studio.sessions.drives`, which itself is thin over
:class:`~kohakuterrarium.terrarium.service.TerrariumService`.

The actor is derived from the authenticated request context, never from the body
(design §9.1, §13): a local/single-tenant caller is the operator console; an L4
user acts as itself with admin → operator elevation. List rows redact
spec/evidence; detail is returned only to an authorized caller (§12.1). Typed
Drive errors propagate to the ``DriveError`` HTTP mapper in ``api/app.py``
(409 conflict / 403 permission / 422 registration+transition / 404 / 400 / 429).
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
    """(actor, is_privileged, operator) from the authenticated request.

    ``user is None`` has two distinct meanings and must not collapse to one
    (R1-03):

    - **L4 disabled** → the single-tenant local operator console
      (privileged + operator);
    - **L4 enabled + anonymous** (only reachable in ``optional`` mode; a
      ``required``-mode anonymous is already rejected by ``get_service``) →
      an unprivileged anonymous principal, never an operator.

    An authenticated L4 user acts as ``user:<id>``; only an ``admin`` role
    gets graph-authority / operator elevation. Never trust an actor field
    from the request body.
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
    """Request-scoped ``(actor, is_privileged, operator)`` for Drive routes.

    Derives authority from BOTH authentication state and the active auth
    configuration so an anonymous optional-L4 caller is not mistaken for the
    trusted local console (R1-03).
    """
    cfg = get_auth_config(conn_info)
    return _actor_privilege(user, l4_enabled=cfg.multi_user_enabled)


# ---------------------------------------------------------------------------
# request bodies (responses are the façade's JSON-safe dicts)
# ---------------------------------------------------------------------------


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
    # Only keys present in ``model_fields_set`` become a patch (UNSET otherwise).
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


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------


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
    """Redacted Drive rows for a session/graph, filtered by the query."""
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
    """Full detail (or a redacted row for an unauthorized caller)."""
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
    """Delivery history (retries / recovery / dead-letter) — record-ACL gated."""
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
    """Append-only progress observations for a Drive — record-ACL gated."""
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
    """Canonical-mutation audit trail for a Drive — record-ACL gated (§7.3)."""
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


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------


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
    """CAS-checked non-identity patch (title/spec/priority/…)."""
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
    """Assign/reassign the Drive to a graph member (graph authority)."""
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
    """Unassign the Drive (graph authority)."""
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
    """Transfer the ownership boundary (explicit, audited)."""
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
    """Pause/resume/wait/block/cancel a Drive (generic transition)."""
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
    """Manually wake a Drive (re-arm pursuit); graph-bound like /transition."""
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
    """Approve a pending terminal proposal (verifier/operator authority)."""
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
    """Append a progress observation (append-only; no revision required)."""
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
    """Replay a dead-letter delivery (admin capability); mints a new delivery."""
    actor, is_privileged, _ = principal
    return await _drives.replay_delivery_record(
        service,
        delivery_id,
        actor=actor,
        is_privileged=is_privileged,
        graph_id=session_id,
    )
