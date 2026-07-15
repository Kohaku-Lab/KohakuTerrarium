"""Explicit migration from ad-hoc goal/plugin state to Drive records (§14.2).

Migration never guesses legacy semantics. The caller supplies records and a
mapper that chooses drive scope, assignee, kind, and payload. This helper
preserves the source payload in migration metadata, assigns fresh canonical
identity through normal creation, and never infers completion from legacy text or
flags. Source keys make reruns idempotent, the legacy store is read-only, and dry
run reports the same mapping without creating records.
"""

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Iterable

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Migration metadata retains source evidence and the stable rerun key.
MIGRATION_METADATA_KEY = "migration"

LegacyRecord = tuple[str, dict[str, Any]]
Mapper = Callable[[dict[str, Any]], CreateDriveRequest]
CreateSink = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class MigrationEntry:
    """Disposition of one legacy record during migration."""

    source_key: str
    source_hash: str
    title: str
    kind: str
    reason: str
    drive_id: str | None = None


@dataclass(frozen=True)
class MigrationReport:
    """Structured outcome of a legacy goal migration."""

    graph_id: str
    dry_run: bool
    entries: tuple[MigrationEntry, ...]

    @property
    def created(self) -> tuple[MigrationEntry, ...]:
        return tuple(e for e in self.entries if e.reason == "created")

    @property
    def planned(self) -> tuple[MigrationEntry, ...]:
        return tuple(e for e in self.entries if e.reason == "would_create")

    @property
    def skipped(self) -> tuple[MigrationEntry, ...]:
        return tuple(e for e in self.entries if e.reason == "already_migrated")

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "dry_run": self.dry_run,
            "created": len(self.created),
            "planned": len(self.planned),
            "skipped": len(self.skipped),
            "entries": [
                {
                    "source_key": e.source_key,
                    "source_hash": e.source_hash,
                    "title": e.title,
                    "kind": e.kind,
                    "reason": e.reason,
                    "drive_id": e.drive_id,
                }
                for e in self.entries
            ],
        }


def _stable_hash(payload: dict[str, Any]) -> str:
    """Hash a legacy payload with stable key ordering."""
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _with_migration_metadata(
    request: CreateDriveRequest,
    *,
    source_key: str,
    source_hash: str,
    payload: dict[str, Any],
) -> CreateDriveRequest:
    """Copy a request with source evidence and idempotency metadata."""
    metadata = dict(request.metadata)
    metadata[MIGRATION_METADATA_KEY] = {
        "source_key": source_key,
        "source_hash": source_hash,
        "source": payload,
        "migrated_from": "goal_state",
    }
    return replace(request, metadata=metadata)


def migrated_source_keys(records: Iterable[Any]) -> set[str]:
    """Collect stable source keys already represented by migrated Drive records."""
    keys: set[str] = set()
    for record in records:
        metadata = getattr(record, "metadata", None)
        if not isinstance(metadata, dict):
            continue
        block = metadata.get(MIGRATION_METADATA_KEY)
        if isinstance(block, dict) and block.get("source_key"):
            keys.add(str(block["source_key"]))
    return keys


def _default_legacy_reader(source_store: Any) -> list[LegacyRecord]:
    """Read declared goal-state pairs without inferring a legacy record format."""
    for attr in ("iter_goal_state", "list_goal_state"):
        reader = getattr(source_store, attr, None)
        if callable(reader):
            return [(str(k), dict(v)) for k, v in reader()]
    logger.warning(
        "no legacy goal-state reader on the source store; pass legacy= explicitly"
    )
    return []


async def migrate_goal_state(
    source_store: Any,
    *,
    graph_id: str,
    mapper: Mapper,
    actor: ActorRef,
    create: CreateSink | None = None,
    legacy: Iterable[LegacyRecord] | None = None,
    already_migrated: Iterable[str] = (),
    dry_run: bool = False,
) -> MigrationReport:
    """Map legacy goal state into new Drives with stable migration metadata."""
    if not isinstance(actor, ActorRef):
        raise DriveValidationError("actor must be an ActorRef")
    if not dry_run and create is None:
        raise DriveValidationError("create sink is required unless dry_run=True")
    records = (
        list(legacy) if legacy is not None else _default_legacy_reader(source_store)
    )
    seen: set[str] = {str(k) for k in already_migrated}
    entries: list[MigrationEntry] = []
    for source_key, payload in records:
        key = str(source_key)
        if not key:
            raise DriveValidationError("legacy record source_key must be non-empty")
        if not isinstance(payload, dict):
            raise DriveValidationError(
                f"legacy payload for {key!r} must be a dict, got {payload!r}"
            )
        source_hash = _stable_hash(payload)
        request = mapper(payload)
        if not isinstance(request, CreateDriveRequest):
            raise DriveValidationError(
                f"mapper for {key!r} must return a CreateDriveRequest, got {request!r}"
            )
        if key in seen:
            entries.append(
                MigrationEntry(
                    source_key=key,
                    source_hash=source_hash,
                    title=request.title,
                    kind=request.kind,
                    reason="already_migrated",
                )
            )
            continue
        seen.add(key)
        enriched = _with_migration_metadata(
            request, source_key=key, source_hash=source_hash, payload=payload
        )
        if dry_run:
            entries.append(
                MigrationEntry(
                    source_key=key,
                    source_hash=source_hash,
                    title=enriched.title,
                    kind=enriched.kind,
                    reason="would_create",
                )
            )
            continue
        record = await create(enriched, graph_id=graph_id, actor=actor)  # type: ignore[misc]
        entries.append(
            MigrationEntry(
                source_key=key,
                source_hash=source_hash,
                title=enriched.title,
                kind=enriched.kind,
                reason="created",
                drive_id=getattr(getattr(record, "record", record), "drive_id", None),
            )
        )
    return MigrationReport(graph_id=graph_id, dry_run=dry_run, entries=tuple(entries))


__all__ = [
    "MIGRATION_METADATA_KEY",
    "MigrationEntry",
    "MigrationReport",
    "migrate_goal_state",
    "migrated_source_keys",
]
