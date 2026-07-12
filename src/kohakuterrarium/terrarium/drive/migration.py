"""Explicit migration from ad-hoc goal/plugin state to Drive records (§14.2).

Migrating legacy goal state is deliberately *not* implicit guessing. The caller
supplies each legacy record and a ``mapper`` that turns it into a
:class:`CreateDriveRequest`; this helper only adds the cross-cutting mechanics
the design mandates:

- the original payload is preserved verbatim under ``metadata.migration.source``;
- each new Drive gets a fresh id + revision 1 (minted by ``create``), never the
  legacy id;
- assignee/scope come from the caller's mapper — never inferred here;
- a ``completed`` status is never inferred from conversation text (creation only
  ever mints a non-terminal Drive, so a legacy "done" flag cannot smuggle a
  terminal record in);
- migration is idempotent via a per-record source key/hash recorded in
  ``metadata.migration``; a re-run skips already-migrated keys;
- legacy state is left untouched — nothing is created until ``create`` commits,
  and the source store is only ever read.

``dry_run=True`` returns the same report with nothing created, so an operator can
preview the mapping before committing.
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

# The metadata block every migrated Drive carries so a re-run is idempotent and
# the original payload survives audit (design §14.2).
MIGRATION_METADATA_KEY = "migration"

LegacyRecord = tuple[str, dict[str, Any]]
Mapper = Callable[[dict[str, Any]], CreateDriveRequest]
CreateSink = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class MigrationEntry:
    """One legacy record's disposition in a migration run."""

    source_key: str
    source_hash: str
    title: str
    kind: str
    reason: str  # "created" | "would_create" | "already_migrated"
    drive_id: str | None = None


@dataclass(frozen=True)
class MigrationReport:
    """The outcome of a :func:`migrate_goal_state` run."""

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
    """Content hash of a legacy payload (stable key order → stable hash)."""
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _with_migration_metadata(
    request: CreateDriveRequest,
    *,
    source_key: str,
    source_hash: str,
    payload: dict[str, Any],
) -> CreateDriveRequest:
    """A copy of ``request`` whose metadata preserves the legacy payload + key."""
    metadata = dict(request.metadata)
    metadata[MIGRATION_METADATA_KEY] = {
        "source_key": source_key,
        "source_hash": source_hash,
        "source": payload,
        "migrated_from": "goal_state",
    }
    return replace(request, metadata=metadata)


def migrated_source_keys(records: Iterable[Any]) -> set[str]:
    """Source keys already migrated, read from records' migration metadata.

    ``records`` are :class:`DriveRecord`-shaped (anything with a ``.metadata``
    dict). Callers pass the graph's existing Drives to make a re-run idempotent
    without re-reading the legacy store.
    """
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
    """Best-effort extractor for ad-hoc goal state on a session store.

    There is no single canonical legacy format, so this only handles a store
    that explicitly exposes ``iter_goal_state()``/``list_goal_state()`` returning
    ``(key, payload)`` pairs. Anything else returns ``[]`` and the caller should
    pass ``legacy=`` with its own extraction. Never guesses record boundaries.
    """
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
    """Migrate ad-hoc goal state into Drive records (design §14.2).

    ``mapper`` turns each ``(source_key, payload)`` into a
    :class:`CreateDriveRequest`; this helper enriches it with idempotent
    migration metadata and, unless ``dry_run``, commits it through ``create``
    (an async ``(request, *, graph_id, actor) -> record`` sink such as a bound
    ``TerrariumService.create_drive``). ``create`` is required unless ``dry_run``.
    """
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
