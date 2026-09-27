"""Explicit offline identity repair preserves lifecycle and rejects guesses."""

import importlib.util
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.errors import SessionLockedError
from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.config_serde import pack_agent_config
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.graph_manifest import (
    GraphManifest,
    ManifestCreature,
    save_manifest,
)
from kohakuterrarium.terrarium.drive.errors import DriveStorageError
from kohakuterrarium.terrarium.drive.memory import MemoryDriveRepository
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.repository import Mutation
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.terrarium.drive.store import (
    SqliteDriveRepository,
    open_drive_repository_readonly,
)

_spec = importlib.util.spec_from_file_location(
    "repair_drive_identity",
    Path(__file__).resolve().parents[2] / "scripts/repair_drive_identity.py",
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
repair, run = _module.repair, _module.run


def manifest(tmp_path, ids=("new",)):
    return GraphManifest(
        "new-graph",
        tuple(
            ManifestCreature(
                creature_id=cid,
                name=cid,
                config_snapshot=pack_agent_config(AgentConfig(name=cid)),
                source_ref=None,
                pwd=str(tmp_path),
                is_privileged=False,
                parent_creature_id=None,
            )
            for cid in ids
        ),
        (),
        (),
        (),
    )


async def damaged(repo, scope="creature", status=DriveStatus.BLOCKED):
    actor = ActorRef("creature", "old")
    record = await repo.create_drive(
        CreateDriveRequest(
            kind="goal",
            title="test",
            scope_type=scope,
            scope_id="old" if scope == "creature" else "old-graph",
            owner=actor,
            owner_scope="creature",
            created_by=actor,
            assignee_creature_id="old",
            spec={"objective": "test"},
        ),
        actor=actor,
        graph_id="old-graph",
        initial_status=status,
    )
    assignment = await repo.get_assignment(record.drive_id)
    async with repo.transaction() as txn:
        await txn.apply(
            Mutation(
                drives=[replace(record, status_reason="user decision")],
                assignments=[replace(assignment, assignment_state="orphaned")],
            )
        )
    return record


@pytest.mark.parametrize("scope", ["creature", "graph"])
@pytest.mark.parametrize("status", [DriveStatus.BLOCKED, DriveStatus.PAUSED])
async def test_preview_apply_and_repeat_preserve_status(tmp_path, scope, status):
    repo = MemoryDriveRepository()
    record = await damaged(repo, scope, status)
    kwargs = dict(
        drive_id=record.drive_id, old_id="old", new_id="new", transfer_owner=True
    )
    preview = await repair(repo, manifest(tmp_path), **kwargs)
    assert preview["applied"] is False
    assert (await repo.get_assignment(record.drive_id)).assignee_creature_id == "old"
    with pytest.raises(ValueError, match="revision"):
        await repair(
            repo, manifest(tmp_path), **kwargs, apply=True, expected_revision=0
        )
    result = await repair(
        repo,
        manifest(tmp_path),
        **kwargs,
        apply=True,
        expected_revision=record.revision,
    )
    assert result["applied"] is True
    current = await repo.get(record.drive_id)
    assert current.status is status
    assert current.status_reason == "user decision"
    assert current.owner == ActorRef("creature", "new")
    assert current.created_by == ActorRef("creature", "old")
    assert current.origin_scope_id == record.origin_scope_id
    assert current.scope_id == ("new" if scope == "creature" else "new-graph")
    assert current.lifecycle_epoch == record.lifecycle_epoch + 1
    assert (await repo.get_assignment(record.drive_id)).assignment_state == "assigned"
    again = await repair(
        repo,
        manifest(tmp_path),
        **kwargs,
        apply=True,
        expected_revision=record.revision,
    )
    assert again["changed"] is False
    assert again["applied"] is False
    assert (await repo.get(record.drive_id)).revision == current.revision
    async with repo.transaction() as txn:
        audit = await txn.audit_for_drive(record.drive_id)
    assert len([a for a in audit if a.operation == "repair_identity"]) == 1


async def test_repair_refuses_live_old_identity_and_unknown_target(tmp_path):
    repo = MemoryDriveRepository()
    record = await damaged(repo)
    kwargs = dict(drive_id=record.drive_id, old_id="old", new_id="new")
    with pytest.raises(ValueError, match="old creature still exists"):
        await repair(repo, manifest(tmp_path, ("old", "new")), **kwargs)
    with pytest.raises(ValueError, match="target creature"):
        await repair(repo, manifest(tmp_path, ("same-name",)), **kwargs)
    assert (await repo.get(record.drive_id)).revision == record.revision


async def test_owner_transfer_is_separately_explicit(tmp_path):
    repo = MemoryDriveRepository()
    record = await damaged(repo)
    await repair(
        repo,
        manifest(tmp_path),
        drive_id=record.drive_id,
        old_id="old",
        new_id="new",
        apply=True,
        expected_revision=record.revision,
    )
    assert (await repo.get(record.drive_id)).owner == record.owner


async def test_offline_tool_backs_up_before_apply(tmp_path):
    path = tmp_path / "session.kohakutr"
    store = SessionStore(path)
    save_manifest(store, manifest(tmp_path))
    store.close(update_status=False)
    repo = SqliteDriveRepository(str(path) + ".drives")
    record = await damaged(repo)
    await repo.close()
    args = SimpleNamespace(
        session=path,
        drive_id=record.drive_id,
        old_creature_id="old",
        creature_id="new",
        transfer_owner=True,
        apply=False,
        expected_revision=None,
    )
    before = path.read_bytes(), (tmp_path / "session.kohakutr.drives").read_bytes()
    result = await run(args)
    assert result["changed"] is True
    assert before == (
        path.read_bytes(),
        (tmp_path / "session.kohakutr.drives").read_bytes(),
    )
    args.apply = True
    args.expected_revision = record.revision
    live = SessionStore(path, writer_lock=True)
    try:
        with pytest.raises(SessionLockedError):
            await run(args)
        assert not (tmp_path / "drive-repair-backups").exists()
    finally:
        live.close(update_status=False)
    result = await run(args)
    assert result["applied"] is True
    backups = list(
        (tmp_path / "drive-repair-backups").glob("*/session.kohakutr.drives")
    )
    assert len(backups) == 1
    saved = open_drive_repository_readonly(
        backups[0], session_path=backups[0].with_suffix("")
    )
    try:
        assert (await saved.get(record.drive_id)).owner == ActorRef("creature", "old")
    finally:
        await saved.close()


@pytest.mark.parametrize("conflict", ["terminal", "owner", "scope", "assignee"])
async def test_repair_rejects_conflicting_identity(tmp_path, conflict):
    repo = MemoryDriveRepository()
    record = await damaged(repo)
    assignment = await repo.get_assignment(record.drive_id)
    fields = {
        "terminal": {"status": DriveStatus.COMPLETED},
        "owner": {"owner": ActorRef("user", "someone-else")},
        "scope": {"scope_id": "another-creature"},
        "assignee": {},
    }[conflict]
    async with repo.transaction() as txn:
        await txn.apply(
            Mutation(
                drives=[replace(record, **fields)],
                assignments=(
                    [replace(assignment, assignee_creature_id="other")]
                    if conflict == "assignee"
                    else []
                ),
            )
        )
    with pytest.raises(ValueError):
        await repair(
            repo,
            manifest(tmp_path),
            drive_id=record.drive_id,
            old_id="old",
            new_id="new",
            transfer_owner=True,
            apply=True,
            expected_revision=record.revision,
        )
    assert (await repo.get(record.drive_id)).revision == record.revision


@pytest.mark.parametrize(
    "error",
    [
        sqlite3.DatabaseError("corrupt database"),
        DriveStorageError("unreadable sidecar"),
        SessionLockedError("session is in use"),
    ],
)
def test_main_reports_expected_storage_errors(monkeypatch, capsys, error):
    async def fail(args):
        raise error

    monkeypatch.setattr(_module, "run", fail)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "repair",
            "--session",
            "unused",
            "--drive-id",
            "d",
            "--old-creature-id",
            "old",
            "--creature-id",
            "new",
        ],
    )
    with pytest.raises(SystemExit) as exited:
        _module.main()
    assert exited.value.code == 1
    assert capsys.readouterr().err == f"{error}\n"


def test_backup_closes_reader_when_destination_cannot_open(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
    connection.close()
    real_connect = sqlite3.connect
    readers = []

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        readers.append(conn)
        return conn

    monkeypatch.setattr(_module.sqlite3, "connect", connect)
    try:
        with pytest.raises(sqlite3.OperationalError):
            _module.backup_database(source, tmp_path / "absent" / "backup.db")
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            readers[0].execute("SELECT 1")
    finally:
        for reader in readers:
            reader.close()
