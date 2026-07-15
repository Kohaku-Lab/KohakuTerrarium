"""Service-level Drive result DTOs + their schema-versioned wire packers.

The :class:`~kohakuterrarium.terrarium.service.TerrariumService` Drive surface
returns richer *views* than the raw records: a :class:`DriveView` folds a record
together with its live assignment summary, derived availability, durability, and
the actor-scoped ``allowed_actions`` (design §9.4). :class:`DriveRuntimeStatus`
is the running-runtime snapshot for the Settings/Drives UI.

These DTOs cross worker boundaries through the versioned envelopes in
:mod:`drive.wire`, so live Python objects never travel and unknown fields cannot
overwrite framework identity.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.models import DriveRecord
from kohakuterrarium.terrarium.drive.wire import (
    WIRE_SCHEMA_VERSION,
    pack_drive_record,
    unpack_drive_record,
)


@dataclass(frozen=True)
class DriveView:
    """Combine a Drive record with assignment, availability, and allowed actions."""

    record: DriveRecord
    assignee_creature_id: str | None
    assignment_state: str | None
    availability: str
    durability: str
    allowed_actions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        r = self.record
        return {
            "drive_id": r.drive_id,
            "kind": r.kind,
            "schema_version": r.schema_version,
            "revision": r.revision,
            "title": r.title,
            "status": r.status.value,
            "status_reason": r.status_reason,
            "scope_type": r.scope_type,
            "scope_id": r.scope_id,
            "priority": r.priority,
            "owner": r.owner.format(),
            "owner_scope": r.owner_scope,
            "created_by": r.created_by.format(),
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
            "lifecycle_epoch": r.lifecycle_epoch,
            "spec": r.spec,
            "presentation": r.presentation,
            "metadata": r.metadata,
            "dependency_ids": list(r.dependency_ids),
            "terminal_evidence": r.terminal_evidence,
            "assignee_creature_id": self.assignee_creature_id,
            "assignment_state": self.assignment_state,
            "availability": self.availability,
            "durability": self.durability,
            "allowed_actions": list(self.allowed_actions),
        }


@dataclass(frozen=True)
class DriveRuntimeStatus:
    """Summarize the running Drive runtime for management surfaces."""

    enabled: bool
    durability: str | None = None
    registrations: tuple[dict[str, Any], ...] = ()
    counts: dict[str, int] = field(default_factory=dict)
    running_revision: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "durability": self.durability,
            "registrations": [dict(r) for r in self.registrations],
            "counts": dict(self.counts),
            "running_revision": self.running_revision,
        }


def registration_dtos(runtime: Any) -> tuple[dict[str, Any], ...]:
    """Bounded per-registration DTOs from a runtime's enabled snapshot."""
    return tuple(
        {
            "name": entry.descriptor.name,
            "kind": entry.descriptor.kind,
            "schema_version": entry.descriptor.schema_version,
            "available": entry.available,
            "has_prompt": entry.prompt_text is not None,
        }
        for entry in runtime.snapshot.entries
    )


def running_revision(runtime: Any) -> str:
    """Opaque content hash of the runtime's enabled registration names."""
    names = sorted(entry.descriptor.name for entry in runtime.snapshot.entries)
    return hashlib.sha256(repr(names).encode("utf-8")).hexdigest()


def _envelope(wire_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"wire_schema": WIRE_SCHEMA_VERSION, "wire_type": wire_type, "data": data}


def _open(payload: object, expected_type: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DriveValidationError(f"wire payload must be a dict, got {payload!r}")
    if payload.get("wire_schema") != WIRE_SCHEMA_VERSION:
        raise DriveValidationError(
            f"unsupported wire schema version {payload.get('wire_schema')!r}"
        )
    if payload.get("wire_type") != expected_type:
        raise DriveValidationError(
            f"expected wire_type {expected_type!r}, got {payload.get('wire_type')!r}"
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise DriveValidationError("wire payload 'data' must be a dict")
    return data


def _str_list(value: object, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise DriveValidationError(f"{name} must be a list")
    return [str(v) for v in value]


def pack_drive_view(view: DriveView) -> dict[str, Any]:
    return _envelope(
        "drive_view",
        {
            "record": pack_drive_record(view.record),
            "assignee_creature_id": view.assignee_creature_id,
            "assignment_state": view.assignment_state,
            "availability": view.availability,
            "durability": view.durability,
            "allowed_actions": list(view.allowed_actions),
        },
    )


def unpack_drive_view(payload: object) -> DriveView:
    d = _open(payload, "drive_view")
    return DriveView(
        record=unpack_drive_record(d.get("record")),
        assignee_creature_id=d.get("assignee_creature_id"),
        assignment_state=d.get("assignment_state"),
        availability=str(d.get("availability", "")),
        durability=str(d.get("durability", "")),
        allowed_actions=tuple(_str_list(d.get("allowed_actions"), "allowed_actions")),
    )


def pack_runtime_status(status: DriveRuntimeStatus) -> dict[str, Any]:
    return _envelope("drive_runtime_status", status.to_dict())


def unpack_runtime_status(payload: object) -> DriveRuntimeStatus:
    d = _open(payload, "drive_runtime_status")
    counts = d.get("counts") or {}
    if not isinstance(counts, dict):
        raise DriveValidationError("counts must be a dict")
    regs = d.get("registrations") or []
    if not isinstance(regs, (list, tuple)):
        raise DriveValidationError("registrations must be a list")
    return DriveRuntimeStatus(
        enabled=bool(d.get("enabled")),
        durability=d.get("durability"),
        registrations=tuple(dict(r) for r in regs),
        counts={str(k): int(v) for k, v in counts.items()},
        running_revision=d.get("running_revision"),
    )


# Settings status also uses a versioned envelope despite its plain dict payload.
def pack_settings_status(status: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status, dict):
        raise DriveValidationError("settings status must be a dict")
    return _envelope("drive_settings_status", dict(status))


def unpack_settings_status(payload: object) -> dict[str, Any]:
    return dict(_open(payload, "drive_settings_status"))


__all__ = [
    "DriveRuntimeStatus",
    "DriveView",
    "pack_drive_view",
    "pack_runtime_status",
    "pack_settings_status",
    "registration_dtos",
    "running_revision",
    "unpack_drive_view",
    "unpack_runtime_status",
    "unpack_settings_status",
]
