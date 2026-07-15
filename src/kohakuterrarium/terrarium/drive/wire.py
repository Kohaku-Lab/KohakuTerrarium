"""Primitive-dict (de)serialization for Drive records and DTOs.

Every packed payload is a msgpack/JSON-safe dict wrapped in an envelope that
carries an explicit ``wire_schema`` version and ``wire_type`` tag. Unpacking
reads only known keys, so an unknown/extra field can never overwrite a
framework identity field, and every malformed payload (missing key, bad type,
unknown version) raises :class:`DriveValidationError` rather than crashing
mid-parse. ``datetime`` values travel as ISO-8601 strings (the session layer's
record-timestamp convention), :class:`ActorRef` as its ``kind:identity`` form.
"""

from datetime import datetime
from typing import Any

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveAssignment,
    DriveAuditRecord,
    DriveDelivery,
    DriveProgress,
    DriveRecord,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.requests import (
    CreateDriveRequest,
    DrivePatch,
    DriveQuery,
    DriveTransitionProposal,
)

WIRE_SCHEMA_VERSION = 1

_PATCH_FIELDS = (
    "title",
    "spec",
    "presentation",
    "metadata",
    "priority",
    "status_reason",
    "not_before",
    "expires_at",
    "dependency_ids",
    "policy_options",
)


def _envelope(wire_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"wire_schema": WIRE_SCHEMA_VERSION, "wire_type": wire_type, "data": data}


def _open(payload: object, expected_type: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DriveValidationError(f"wire payload must be a dict, got {payload!r}")
    version = payload.get("wire_schema")
    if version != WIRE_SCHEMA_VERSION:
        raise DriveValidationError(f"unsupported wire schema version {version!r}")
    wire_type = payload.get("wire_type")
    if wire_type != expected_type:
        raise DriveValidationError(
            f"expected wire_type {expected_type!r}, got {wire_type!r}"
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DriveValidationError("wire payload 'data' must be a dict")
    return data


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_dt(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DriveValidationError(f"{name} must be an ISO datetime string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise DriveValidationError(
            f"{name} is not a valid ISO datetime: {value!r}"
        ) from exc


def _actor(value: ActorRef | None) -> str | None:
    return value.format() if value is not None else None


def _parse_actor(
    value: object, name: str, *, allow_none: bool = False
) -> ActorRef | None:
    if value is None:
        if allow_none:
            return None
        raise DriveValidationError(f"{name} is required")
    if not isinstance(value, str):
        raise DriveValidationError(f"{name} must be a string ActorRef")
    return ActorRef.parse(value)


def _status(value: object, name: str) -> DriveStatus:
    try:
        return DriveStatus(value)
    except ValueError as exc:
        raise DriveValidationError(f"{name} is not a valid status: {value!r}") from exc


def _opt_status(value: object, name: str) -> DriveStatus | None:
    return None if value is None else _status(value, name)


def _dict(value: object, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DriveValidationError(f"{name} must be a dict")
    return value


def _seq(value: object, name: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise DriveValidationError(f"{name} must be a list")
    return tuple(value)


def pack_drive_record(record: DriveRecord) -> dict[str, Any]:
    return _envelope(
        "drive_record",
        {
            "drive_id": record.drive_id,
            "kind": record.kind,
            "schema_version": record.schema_version,
            "revision": record.revision,
            "title": record.title,
            "spec": record.spec,
            "presentation": record.presentation,
            "metadata": record.metadata,
            "scope_type": record.scope_type,
            "scope_id": record.scope_id,
            "origin_scope_id": record.origin_scope_id,
            "status": record.status.value,
            "status_reason": record.status_reason,
            "priority": record.priority,
            "policy_name": record.policy_name,
            "created_by": _actor(record.created_by),
            "owner": _actor(record.owner),
            "owner_scope": record.owner_scope,
            "created_at": _dt(record.created_at),
            "updated_by": _actor(record.updated_by),
            "updated_at": _dt(record.updated_at),
            "lifecycle_epoch": record.lifecycle_epoch,
            "terminal_evidence": record.terminal_evidence,
            "not_before": _dt(record.not_before),
            "expires_at": _dt(record.expires_at),
            "dependency_ids": list(record.dependency_ids),
            "policy_options": record.policy_options,
        },
    )


def unpack_drive_record(payload: object) -> DriveRecord:
    d = _open(payload, "drive_record")
    terminal = d.get("terminal_evidence")
    return DriveRecord(
        drive_id=d.get("drive_id"),
        kind=d.get("kind"),
        schema_version=d.get("schema_version"),
        revision=d.get("revision"),
        title=d.get("title"),
        spec=_dict(d.get("spec"), "spec"),
        presentation=_dict(d.get("presentation"), "presentation"),
        metadata=_dict(d.get("metadata"), "metadata"),
        scope_type=d.get("scope_type"),
        scope_id=d.get("scope_id"),
        origin_scope_id=d.get("origin_scope_id"),
        status=_status(d.get("status"), "status"),
        status_reason=d.get("status_reason"),
        priority=d.get("priority"),
        policy_name=d.get("policy_name"),
        created_by=_parse_actor(d.get("created_by"), "created_by"),
        owner=_parse_actor(d.get("owner"), "owner"),
        owner_scope=d.get("owner_scope"),
        created_at=_parse_dt(d.get("created_at"), "created_at"),
        updated_by=_parse_actor(d.get("updated_by"), "updated_by"),
        updated_at=_parse_dt(d.get("updated_at"), "updated_at"),
        lifecycle_epoch=d.get("lifecycle_epoch"),
        terminal_evidence=(
            None if terminal is None else _dict(terminal, "terminal_evidence")
        ),
        not_before=_parse_dt(d.get("not_before"), "not_before"),
        expires_at=_parse_dt(d.get("expires_at"), "expires_at"),
        dependency_ids=tuple(_seq(d.get("dependency_ids"), "dependency_ids")),
        policy_options=_dict(d.get("policy_options"), "policy_options"),
    )


def pack_drive_assignment(a: DriveAssignment) -> dict[str, Any]:
    return _envelope(
        "drive_assignment",
        {
            "drive_id": a.drive_id,
            "assignment_id": a.assignment_id,
            "revision": a.revision,
            "lifecycle_epoch": a.lifecycle_epoch,
            "assignee_graph_id": a.assignee_graph_id,
            "assignment_state": a.assignment_state,
            "updated_at": _dt(a.updated_at),
            "assignee_creature_id": a.assignee_creature_id,
            "lease_owner": a.lease_owner,
            "lease_expires_at": _dt(a.lease_expires_at),
            "assigned_by": _actor(a.assigned_by),
            "assigned_at": _dt(a.assigned_at),
        },
    )


def unpack_drive_assignment(payload: object) -> DriveAssignment:
    d = _open(payload, "drive_assignment")
    return DriveAssignment(
        drive_id=d.get("drive_id"),
        assignment_id=d.get("assignment_id"),
        revision=d.get("revision"),
        lifecycle_epoch=d.get("lifecycle_epoch"),
        assignee_graph_id=d.get("assignee_graph_id"),
        assignment_state=d.get("assignment_state"),
        updated_at=_parse_dt(d.get("updated_at"), "updated_at"),
        assignee_creature_id=d.get("assignee_creature_id"),
        lease_owner=d.get("lease_owner"),
        lease_expires_at=_parse_dt(d.get("lease_expires_at"), "lease_expires_at"),
        assigned_by=_parse_actor(d.get("assigned_by"), "assigned_by", allow_none=True),
        assigned_at=_parse_dt(d.get("assigned_at"), "assigned_at"),
    )


def pack_drive_delivery(x: DriveDelivery) -> dict[str, Any]:
    return _envelope(
        "drive_delivery",
        {
            "delivery_id": x.delivery_id,
            "drive_id": x.drive_id,
            "drive_revision": x.drive_revision,
            "lifecycle_epoch": x.lifecycle_epoch,
            "assignment_id": x.assignment_id,
            "assignee_creature_id": x.assignee_creature_id,
            "reason": x.reason,
            "state": x.state,
            "attempt": x.attempt,
            "available_at": _dt(x.available_at),
            "created_at": _dt(x.created_at),
            "readiness_generation": x.readiness_generation,
            "claimed_at": _dt(x.claimed_at),
            "admitted_at": _dt(x.admitted_at),
            "acknowledged_at": _dt(x.acknowledged_at),
            "last_error": x.last_error,
        },
    )


def unpack_drive_delivery(payload: object) -> DriveDelivery:
    d = _open(payload, "drive_delivery")
    return DriveDelivery(
        delivery_id=d.get("delivery_id"),
        drive_id=d.get("drive_id"),
        drive_revision=d.get("drive_revision"),
        lifecycle_epoch=d.get("lifecycle_epoch"),
        assignment_id=d.get("assignment_id"),
        assignee_creature_id=d.get("assignee_creature_id"),
        reason=d.get("reason"),
        state=d.get("state"),
        attempt=d.get("attempt"),
        available_at=_parse_dt(d.get("available_at"), "available_at"),
        created_at=_parse_dt(d.get("created_at"), "created_at"),
        readiness_generation=d.get("readiness_generation", 0),
        claimed_at=_parse_dt(d.get("claimed_at"), "claimed_at"),
        admitted_at=_parse_dt(d.get("admitted_at"), "admitted_at"),
        acknowledged_at=_parse_dt(d.get("acknowledged_at"), "acknowledged_at"),
        last_error=d.get("last_error"),
    )


def pack_drive_progress(p: DriveProgress) -> dict[str, Any]:
    return _envelope(
        "drive_progress",
        {
            "progress_id": p.progress_id,
            "drive_id": p.drive_id,
            "actor": _actor(p.actor),
            "summary": p.summary,
            "created_at": _dt(p.created_at),
            "evidence": p.evidence,
        },
    )


def unpack_drive_progress(payload: object) -> DriveProgress:
    d = _open(payload, "drive_progress")
    return DriveProgress(
        progress_id=d.get("progress_id"),
        drive_id=d.get("drive_id"),
        actor=_parse_actor(d.get("actor"), "actor"),
        summary=d.get("summary"),
        created_at=_parse_dt(d.get("created_at"), "created_at"),
        evidence=_dict(d.get("evidence"), "evidence"),
    )


def pack_drive_audit(a: DriveAuditRecord) -> dict[str, Any]:
    return _envelope(
        "drive_audit",
        {
            "audit_id": a.audit_id,
            "drive_id": a.drive_id,
            "revision": a.revision,
            "lifecycle_epoch": a.lifecycle_epoch,
            "actor": _actor(a.actor),
            "operation": a.operation,
            "created_at": _dt(a.created_at),
            "before_status": None if a.before_status is None else a.before_status.value,
            "after_status": None if a.after_status is None else a.after_status.value,
            "summary": a.summary,
            "details": a.details,
        },
    )


def unpack_drive_audit(payload: object) -> DriveAuditRecord:
    d = _open(payload, "drive_audit")
    return DriveAuditRecord(
        audit_id=d.get("audit_id"),
        drive_id=d.get("drive_id"),
        revision=d.get("revision"),
        lifecycle_epoch=d.get("lifecycle_epoch"),
        actor=_parse_actor(d.get("actor"), "actor"),
        operation=d.get("operation"),
        created_at=_parse_dt(d.get("created_at"), "created_at"),
        before_status=_opt_status(d.get("before_status"), "before_status"),
        after_status=_opt_status(d.get("after_status"), "after_status"),
        summary=d.get("summary", ""),
        details=_dict(d.get("details"), "details"),
    )


def pack_create_request(r: CreateDriveRequest) -> dict[str, Any]:
    return _envelope(
        "create_drive_request",
        {
            "kind": r.kind,
            "title": r.title,
            "scope_type": r.scope_type,
            "scope_id": r.scope_id,
            "owner": _actor(r.owner),
            "owner_scope": r.owner_scope,
            "created_by": _actor(r.created_by),
            "schema_version": r.schema_version,
            "spec": r.spec,
            "presentation": r.presentation,
            "metadata": r.metadata,
            "assignee_creature_id": r.assignee_creature_id,
            "priority": r.priority,
            "not_before": _dt(r.not_before),
            "expires_at": _dt(r.expires_at),
            "dependency_ids": list(r.dependency_ids),
            "policy_name": r.policy_name,
            "policy_options": r.policy_options,
            "idempotency_key": r.idempotency_key,
        },
    )


def unpack_create_request(payload: object) -> CreateDriveRequest:
    d = _open(payload, "create_drive_request")
    return CreateDriveRequest(
        kind=d.get("kind"),
        title=d.get("title"),
        scope_type=d.get("scope_type"),
        scope_id=d.get("scope_id"),
        owner=_parse_actor(d.get("owner"), "owner"),
        owner_scope=d.get("owner_scope"),
        created_by=_parse_actor(d.get("created_by"), "created_by"),
        schema_version=d.get("schema_version", 1),
        spec=_dict(d.get("spec"), "spec"),
        presentation=_dict(d.get("presentation"), "presentation"),
        metadata=_dict(d.get("metadata"), "metadata"),
        assignee_creature_id=d.get("assignee_creature_id"),
        priority=d.get("priority", 0),
        not_before=_parse_dt(d.get("not_before"), "not_before"),
        expires_at=_parse_dt(d.get("expires_at"), "expires_at"),
        dependency_ids=tuple(_seq(d.get("dependency_ids"), "dependency_ids")),
        policy_name=d.get("policy_name", "generic"),
        policy_options=_dict(d.get("policy_options"), "policy_options"),
        idempotency_key=d.get("idempotency_key"),
    )


# Patch payloads include only fields explicitly supplied by the caller.
def pack_drive_patch(patch: DrivePatch) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for name, value in patch.changes().items():
        if name in ("not_before", "expires_at"):
            data[name] = _dt(value)
        elif name == "dependency_ids":
            data[name] = list(value)
        else:
            data[name] = value
    return _envelope("drive_patch", data)


def unpack_drive_patch(payload: object) -> DrivePatch:
    d = _open(payload, "drive_patch")
    kwargs: dict[str, Any] = {}
    for name in _PATCH_FIELDS:
        if name not in d:
            continue
        value = d[name]
        if name in ("not_before", "expires_at"):
            kwargs[name] = _parse_dt(value, name)
        elif name == "dependency_ids":
            kwargs[name] = tuple(_seq(value, name))
        else:
            kwargs[name] = value
    return DrivePatch(**kwargs)


def pack_drive_query(q: DriveQuery) -> dict[str, Any]:
    return _envelope(
        "drive_query",
        {
            "graph_id": q.graph_id,
            "scope_type": q.scope_type,
            "scope_id": q.scope_id,
            "statuses": (
                None if q.statuses is None else sorted(s.value for s in q.statuses)
            ),
            "kinds": None if q.kinds is None else sorted(q.kinds),
            "assignee_creature_id": q.assignee_creature_id,
            "owner": _actor(q.owner),
            "include_terminal": q.include_terminal,
            "limit": q.limit,
        },
    )


def unpack_drive_query(payload: object) -> DriveQuery:
    d = _open(payload, "drive_query")
    raw_statuses = d.get("statuses")
    raw_kinds = d.get("kinds")
    statuses = (
        None
        if raw_statuses is None
        else frozenset(
            _status(s, "statuses entry") for s in _seq(raw_statuses, "statuses")
        )
    )
    kinds = None if raw_kinds is None else frozenset(_seq(raw_kinds, "kinds"))
    return DriveQuery(
        graph_id=d.get("graph_id"),
        scope_type=d.get("scope_type"),
        scope_id=d.get("scope_id"),
        statuses=statuses,
        kinds=kinds,
        assignee_creature_id=d.get("assignee_creature_id"),
        owner=_parse_actor(d.get("owner"), "owner", allow_none=True),
        include_terminal=d.get("include_terminal", True),
        limit=d.get("limit"),
    )


def pack_transition_proposal(p: DriveTransitionProposal) -> dict[str, Any]:
    return _envelope(
        "drive_transition_proposal",
        {
            "proposal_id": p.proposal_id,
            "drive_id": p.drive_id,
            "target_status": p.target_status.value,
            "proposed_by": _actor(p.proposed_by),
            "created_at": _dt(p.created_at),
            "reason": p.reason,
            "evidence": p.evidence,
            "expected_revision": p.expected_revision,
            "lifecycle_epoch": p.lifecycle_epoch,
        },
    )


def unpack_transition_proposal(payload: object) -> DriveTransitionProposal:
    d = _open(payload, "drive_transition_proposal")
    return DriveTransitionProposal(
        proposal_id=d.get("proposal_id"),
        drive_id=d.get("drive_id"),
        target_status=_status(d.get("target_status"), "target_status"),
        proposed_by=_parse_actor(d.get("proposed_by"), "proposed_by"),
        created_at=_parse_dt(d.get("created_at"), "created_at"),
        reason=d.get("reason"),
        evidence=_dict(d.get("evidence"), "evidence"),
        expected_revision=d.get("expected_revision"),
        lifecycle_epoch=d.get("lifecycle_epoch", 0),
    )


_UNPACKERS = {
    "drive_record": unpack_drive_record,
    "drive_assignment": unpack_drive_assignment,
    "drive_delivery": unpack_drive_delivery,
    "drive_progress": unpack_drive_progress,
    "drive_audit": unpack_drive_audit,
    "create_drive_request": unpack_create_request,
    "drive_patch": unpack_drive_patch,
    "drive_query": unpack_drive_query,
    "drive_transition_proposal": unpack_transition_proposal,
}

_PACKERS = {
    DriveRecord: pack_drive_record,
    DriveAssignment: pack_drive_assignment,
    DriveDelivery: pack_drive_delivery,
    DriveProgress: pack_drive_progress,
    DriveAuditRecord: pack_drive_audit,
    CreateDriveRequest: pack_create_request,
    DrivePatch: pack_drive_patch,
    DriveQuery: pack_drive_query,
    DriveTransitionProposal: pack_transition_proposal,
}


def pack(obj: object) -> dict[str, Any]:
    """Pack a supported Drive record or DTO into a versioned wire envelope."""
    packer = _PACKERS.get(type(obj))
    if packer is None:
        raise DriveValidationError(f"cannot pack object of type {type(obj).__name__}")
    return packer(obj)


def unpack(payload: object) -> Any:
    """Unpack a versioned Drive wire envelope into its record or DTO."""
    if not isinstance(payload, dict):
        raise DriveValidationError(f"wire payload must be a dict, got {payload!r}")
    version = payload.get("wire_schema")
    if version != WIRE_SCHEMA_VERSION:
        raise DriveValidationError(f"unsupported wire schema version {version!r}")
    wire_type = payload.get("wire_type")
    unpacker = _UNPACKERS.get(wire_type)
    if unpacker is None:
        raise DriveValidationError(f"unknown wire_type {wire_type!r}")
    return unpacker(payload)
