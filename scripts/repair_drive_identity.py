"""Preview or explicitly repair one saved Drive's identity without activating it."""

import argparse
import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.graph_manifest import MANIFEST_KEY, parse_manifest
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.drive.policy import is_terminal
from kohakuterrarium.terrarium.drive.repository import Mutation, new_audit, new_outbox
from kohakuterrarium.terrarium.drive.store import (
    SqliteDriveRepository,
    open_drive_repository_readonly,
)

REPAIR_ACTOR = ActorRef("service", "offline-drive-identity-repair")


def repair_plan(record, assignment, manifest, *, old_id, new_id, transfer_owner=False):
    """Validate an explicit identity mapping against the saved graph manifest."""
    if is_terminal(record.status):
        raise ValueError("terminal Drives cannot be repaired")
    if assignment is None:
        raise ValueError("the Drive has no assignment row")
    if new_id not in {c.creature_id for c in manifest.creatures}:
        raise ValueError("target creature is not in the saved manifest")
    if old_id != new_id and old_id in {c.creature_id for c in manifest.creatures}:
        raise ValueError("old creature still exists; use normal reassignment instead")
    if assignment.assignee_creature_id not in {None, old_id, new_id}:
        raise ValueError("assignment belongs to a different creature")
    if record.scope_type == "creature" and record.scope_id not in {old_id, new_id}:
        raise ValueError("scope belongs to a different creature")
    if record.scope_type == "graph" and record.scope_id not in {
        assignment.assignee_graph_id,
        manifest.graph_id,
    }:
        raise ValueError("scope and assignment disagree about the original graph")
    owner = record.owner
    if transfer_owner:
        if owner not in {ActorRef("creature", old_id), ActorRef("creature", new_id)}:
            raise ValueError("owner does not match the explicit creature mapping")
        owner = ActorRef("creature", new_id)
    fields = {
        "owner": owner,
        "scope_id": new_id if record.scope_type == "creature" else manifest.graph_id,
    }
    assignment_fields = {
        "assignee_creature_id": new_id,
        "assignee_graph_id": manifest.graph_id,
        "assignment_state": "assigned",
    }
    changed = any(getattr(record, k) != v for k, v in fields.items()) or any(
        getattr(assignment, k) != v for k, v in assignment_fields.items()
    )
    return fields, assignment_fields, changed


async def repair(
    repository,
    manifest,
    *,
    drive_id,
    old_id,
    new_id,
    expected_revision=None,
    transfer_owner=False,
    apply=False,
):
    """Apply one version-checked identity mutation with an immutable audit trail."""
    async with repository.transaction() as txn:
        record = await txn.get_drive(drive_id)
        if record is None:
            raise ValueError("Drive not found")
        assignment = await txn.get_assignment(drive_id)
        fields, assignment_fields, changed = repair_plan(
            record,
            assignment,
            manifest,
            old_id=old_id,
            new_id=new_id,
            transfer_owner=transfer_owner,
        )
        preview = {
            "drive_id": drive_id,
            "revision": record.revision,
            "changed": changed,
            "status": record.status.value,
            "status_reason": record.status_reason,
            "owner_before": record.owner.format(),
            "owner_after": fields["owner"].format(),
            "scope_before": record.scope_id,
            "scope_after": fields["scope_id"],
            "assignment_before": assignment.assignee_creature_id,
            "assignment_after": new_id,
            "graph_after": manifest.graph_id,
            "applied": False,
        }
        if not apply or not changed:
            return preview
        if expected_revision is None or record.revision != expected_revision:
            raise ValueError("revision changed; preview again before applying")
        now = datetime.now(timezone.utc)
        updated = replace(
            record,
            **fields,
            revision=record.revision + 1,
            lifecycle_epoch=record.lifecycle_epoch + 1,
            updated_by=REPAIR_ACTOR,
            updated_at=now,
        )
        repaired = replace(
            assignment,
            **assignment_fields,
            revision=updated.revision,
            lifecycle_epoch=updated.lifecycle_epoch,
            assignment_id=uuid4().hex,
            assigned_by=REPAIR_ACTOR,
            assigned_at=now,
            updated_at=now,
            lease_owner=None,
            lease_expires_at=None,
        )
        deliveries = [
            replace(d, state="superseded")
            for d in await txn.deliveries_for_drive(drive_id)
            if d.state in {"pending", "claimed", "retry_wait"}
        ]
        audit = new_audit(
            updated,
            REPAIR_ACTOR,
            "repair_identity",
            now,
            uuid4().hex,
            before=record.status,
            after=updated.status,
            details=preview,
        )
        await txn.apply(
            Mutation(
                drives=[updated],
                assignments=[repaired],
                deliveries=deliveries,
                audit=[audit],
                outbox=[new_outbox(drive_id, "drive_reassigned", now, uuid4().hex)],
            )
        )
        return {**preview, "applied": True, "revision": updated.revision}


def backup_database(source, destination):
    """Create a consistent SQLite backup including committed WAL rows."""
    reader = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    writer = sqlite3.connect(destination)
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()


async def run(args):
    path = Path(args.session).expanduser().resolve()
    sidecar = Path(str(path) + ".drives")
    if not path.is_file() or not sidecar.is_file():
        raise ValueError("an existing session and Drive sidecar are required")
    if args.apply and args.expected_revision is None:
        raise ValueError("--apply requires --expected-revision from the preview")
    store = None
    repo = None
    backup = None
    try:
        if args.apply:
            store = SessionStore(path, writer_lock=True)
            raw_manifest = store.meta.get(MANIFEST_KEY)
        else:
            raw_manifest = read_session_meta(path).get(MANIFEST_KEY)
        if raw_manifest is None:
            raise ValueError(
                "a restored manifest is required; repair a copied session after restoring it"
            )
        manifest = parse_manifest(raw_manifest)
        if args.apply:
            backup = path.parent / "drive-repair-backups" / uuid4().hex
            backup.mkdir(parents=True)
            backup_database(path, backup / path.name)
            backup_database(sidecar, backup / sidecar.name)
            repo = SqliteDriveRepository(sidecar)
        else:
            repo = open_drive_repository_readonly(sidecar, session_path=path)
        result = await repair(
            repo,
            manifest,
            drive_id=args.drive_id,
            old_id=args.old_creature_id,
            new_id=args.creature_id,
            expected_revision=args.expected_revision,
            transfer_owner=args.transfer_owner,
            apply=args.apply,
        )
        if backup is not None:
            result["backup"] = str(backup)
        return result
    finally:
        if repo is not None:
            await repo.close()
        if store is not None:
            store.close(update_status=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True)
    parser.add_argument("--drive-id", required=True)
    parser.add_argument("--old-creature-id", required=True)
    parser.add_argument("--creature-id", required=True)
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--transfer-owner", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
