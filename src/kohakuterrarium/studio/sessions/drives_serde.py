"""Serialization + primitive-parsing helpers for the Drive record façade.

Split out of :mod:`studio.sessions.drives` (file-size cap): the record-shape
serializers (row / detail / progress / delivery / audit) and the
primitive-``dict`` → typed-DTO builders (create request / patch / status /
actor parsing). The record-operation functions in ``drives`` import from here;
the public names stay re-exported from ``drives`` so callers keep importing
``studio.sessions.drives.build_create_request`` etc.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.models import (
    ActorRef,
    DriveAuditRecord,
    DriveDelivery,
    DriveProgress,
    DriveStatus,
)
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest, DrivePatch
from kohakuterrarium.terrarium.drive.wire_service import DriveView

# Fields dropped from a list row — sensitive/verbose payloads live on detail only.
_REDACTED_KEYS = ("spec", "presentation", "metadata", "terminal_evidence")


# ---------------------------------------------------------------------------
# serialization
# ---------------------------------------------------------------------------


def row(view: DriveView) -> dict[str, Any]:
    """A redacted list row (design §12.1): no spec/presentation/metadata."""
    data = view.to_dict()
    for key in _REDACTED_KEYS:
        data.pop(key, None)
    return data


def detail(view: DriveView, *, actor: ActorRef, is_privileged: bool) -> dict[str, Any]:
    """Full record when the caller is authorized, else the redacted row.

    A caller is authorized for the detail (spec/evidence) when it is the owner or
    holds operator/privileged elevation; anyone else who can merely *see* the
    Drive gets the row shape so sensitive spec/evidence stays collapsed (§12.1).
    """
    if is_privileged or view.record.owner == actor:
        return view.to_dict()
    return row(view)


def progress_to_dict(progress: DriveProgress) -> dict[str, Any]:
    return {
        "progress_id": progress.progress_id,
        "drive_id": progress.drive_id,
        "actor": progress.actor.format(),
        "summary": progress.summary,
        "evidence": progress.evidence,
        "created_at": progress.created_at.isoformat(),
    }


def delivery_to_dict(delivery: DriveDelivery) -> dict[str, Any]:
    return {
        "delivery_id": delivery.delivery_id,
        "drive_id": delivery.drive_id,
        "drive_revision": delivery.drive_revision,
        "lifecycle_epoch": delivery.lifecycle_epoch,
        "assignment_id": delivery.assignment_id,
        "assignee_creature_id": delivery.assignee_creature_id,
        "reason": delivery.reason,
        "state": delivery.state,
        "attempt": delivery.attempt,
        "readiness_generation": delivery.readiness_generation,
        "available_at": delivery.available_at.isoformat(),
        "created_at": delivery.created_at.isoformat(),
        "claimed_at": _iso_or_none(delivery.claimed_at),
        "admitted_at": _iso_or_none(delivery.admitted_at),
        "acknowledged_at": _iso_or_none(delivery.acknowledged_at),
        "last_error": delivery.last_error,
    }


def audit_to_dict(audit: DriveAuditRecord) -> dict[str, Any]:
    """One canonical-mutation audit row (design §7.3) as a JSON-safe dict."""
    return {
        "audit_id": audit.audit_id,
        "drive_id": audit.drive_id,
        "revision": audit.revision,
        "lifecycle_epoch": audit.lifecycle_epoch,
        "actor": audit.actor.format(),
        "operation": audit.operation,
        "before_status": audit.before_status.value if audit.before_status else None,
        "after_status": audit.after_status.value if audit.after_status else None,
        "summary": audit.summary,
        "details": audit.details,
        "created_at": audit.created_at.isoformat(),
    }


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


# ---------------------------------------------------------------------------
# primitive -> typed input
# ---------------------------------------------------------------------------


def parse_status(value: str) -> DriveStatus:
    """A :class:`DriveStatus` from its wire string, or a typed error."""
    try:
        return DriveStatus(value)
    except ValueError as exc:
        raise DriveValidationError(
            f"unknown drive status {value!r}; expected one of "
            f"{[s.value for s in DriveStatus]}"
        ) from exc


def parse_statuses(value: object) -> frozenset[DriveStatus] | None:
    """A status filter set from a CSV string or list; ``None`` when unset."""
    if value is None:
        return None
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    elif isinstance(value, (list, tuple, set, frozenset)):
        parts = [str(p).strip() for p in value if str(p).strip()]
    else:
        raise DriveValidationError("statuses must be a comma string or list")
    if not parts:
        return None
    return frozenset(parse_status(p) for p in parts)


def parse_kinds(value: object) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    elif isinstance(value, (list, tuple, set, frozenset)):
        parts = [str(p).strip() for p in value if str(p).strip()]
    else:
        raise DriveValidationError("kinds must be a comma string or list")
    return frozenset(parts) or None


def parse_actor(value: object) -> ActorRef | None:
    if value is None:
        return None
    if isinstance(value, ActorRef):
        return value
    if isinstance(value, str) and value:
        return ActorRef.parse(value)
    raise DriveValidationError(
        f"owner must be a '<kind>:<identity>' string, got {value!r}"
    )


def _parse_aware(value: object, name: str) -> datetime | None:
    """Parse an ISO-8601 string into a tz-aware datetime (or ``None``)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise DriveValidationError(
                f"{name} must be ISO-8601, got {value!r}"
            ) from exc
    else:
        raise DriveValidationError(f"{name} must be an ISO-8601 string")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def build_create_request(
    *,
    graph_id: str,
    actor: ActorRef,
    body: dict[str, Any],
) -> CreateDriveRequest:
    """A :class:`CreateDriveRequest` from a primitive body (owner defaults to actor).

    The caller-supplied body never carries an actor; ``created_by``/``owner``
    default to the authenticated ``actor`` and can only be widened by a trusted
    operator through the ``owner`` field (design §9.1, §13).
    """
    kind = _require_str(body, "kind")
    title = _require_str(body, "title")
    scope_type = body.get("scope_type") or "graph"
    if scope_type not in ("graph", "creature"):
        raise DriveValidationError("scope_type must be 'graph' or 'creature'")
    # Creature scope must name its creature; only graph scope defaults to the graph.
    scope_id = body.get("scope_id")
    if scope_type == "creature" and not scope_id:
        raise DriveValidationError(
            "scope_type 'creature' requires an explicit scope_id (the creature id)"
        )
    if scope_type == "graph":
        scope_id = scope_id or graph_id
    owner = parse_actor(body.get("owner")) or actor
    owner_scope = body.get("owner_scope") or ("actor" if owner == actor else "service")
    return CreateDriveRequest(
        kind=kind,
        title=title,
        scope_type=scope_type,
        scope_id=str(scope_id),
        owner=owner,
        owner_scope=owner_scope,
        created_by=actor,
        schema_version=int(body.get("schema_version", 1)),
        spec=dict(body.get("spec") or {}),
        presentation=dict(body.get("presentation") or {}),
        metadata=dict(body.get("metadata") or {}),
        assignee_creature_id=body.get("assignee_creature_id"),
        priority=int(body.get("priority", 0)),
        not_before=_parse_aware(body.get("not_before"), "not_before"),
        expires_at=_parse_aware(body.get("expires_at"), "expires_at"),
        dependency_ids=tuple(body.get("dependency_ids") or ()),
        policy_name=body.get("policy_name") or "generic",
        policy_options=dict(body.get("policy_options") or {}),
        idempotency_key=body.get("idempotency_key"),
    )


def build_patch(body: dict[str, Any]) -> DrivePatch:
    """A :class:`DrivePatch` carrying only the fields the body actually set.

    Absent keys stay :data:`UNSET`. For a dict-valued field a ``null`` is
    normalized to an empty dict (a Drive spec/metadata is never literally
    ``None``); ``status_reason`` keeps ``null`` since it is a nullable clear.
    """
    kwargs: dict[str, Any] = {}
    if "title" in body:
        kwargs["title"] = body["title"]
    if "priority" in body:
        kwargs["priority"] = body["priority"]
    if "status_reason" in body:
        kwargs["status_reason"] = body["status_reason"]
    for key in ("spec", "presentation", "metadata", "policy_options"):
        if key in body:
            kwargs[key] = dict(body[key] or {})
    if "not_before" in body:
        kwargs["not_before"] = _parse_aware(body.get("not_before"), "not_before")
    if "expires_at" in body:
        kwargs["expires_at"] = _parse_aware(body.get("expires_at"), "expires_at")
    if "dependency_ids" in body:
        kwargs["dependency_ids"] = tuple(body.get("dependency_ids") or ())
    return DrivePatch(**kwargs)


def _require_str(body: dict[str, Any], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise DriveValidationError(f"{name} is required and must be a non-empty string")
    return value


__all__ = [
    "audit_to_dict",
    "build_create_request",
    "build_patch",
    "delivery_to_dict",
    "detail",
    "parse_actor",
    "parse_kinds",
    "parse_status",
    "parse_statuses",
    "progress_to_dict",
    "row",
]
