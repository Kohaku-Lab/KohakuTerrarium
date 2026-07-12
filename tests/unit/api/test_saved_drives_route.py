"""Unit tests for :mod:`api.routes.persistence.saved_drives`.

A real Drive is created in a persistent engine, the engine is shut down (which
flushes the Drive sidecar), then the read-only saved-session endpoint reads it
back WITHOUT a live engine — the saved-session viewer's path.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.persistence import saved_drives as mod
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.terrarium.drive.store import (
    SqliteDriveRepository,
    drive_sidecar_path,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent
from kohakuterrarium.utils.file_lock import FileLock

pytestmark = pytest.mark.timeout(30)

OPS = ActorRef("user", "ops")


def _create_req(title: str) -> CreateDriveRequest:
    return CreateDriveRequest(
        kind="generic",
        title=title,
        scope_type="creature",
        scope_id="root",
        owner=OPS,
        owner_scope="creature",
        created_by=OPS,
    )


async def _make_saved_session(session_dir: Path) -> str:
    """Create + persist one Drive, return the saved session file stem."""
    engine = Terrarium(
        session_dir=str(session_dir),
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    root = Creature(
        creature_id="root",
        name="root",
        agent=_FakeAgent(name="root"),
        is_privileged=True,
    )
    await engine.add_creature(root, session=True)
    gid = root.graph_id
    svc = LocalTerrariumService(engine)
    from kohakuterrarium.studio.sessions import drives as facade

    await facade.create_record(
        svc,
        graph_id=gid,
        actor=OPS,
        body={"kind": "generic", "title": "persisted watch", "spec": {"secret": 1}},
        is_privileged=True,
        operator=True,
    )
    await engine.shutdown()
    return gid


async def test_read_saved_drives_redacted(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
    await _make_saved_session(session_dir)

    path = next(session_dir.glob("*.kohakutr"))
    rows = await mod._read_saved_drives(path)
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "persisted watch"
    assert row["durability"] == "persistent"
    assert row["allowed_actions"] == []  # read-only viewer, no live ACL
    assert "spec" not in row  # redacted


async def test_route_reads_saved_session(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
    await _make_saved_session(session_dir)
    stem = next(session_dir.glob("*.kohakutr")).stem

    app = FastAPI()
    app.include_router(mod.router, prefix="/api/persistence/viewer")
    client = TestClient(app)
    resp = client.get(f"/api/persistence/viewer/{stem}/drives")
    assert resp.status_code == 200
    drives = resp.json()["drives"]
    assert len(drives) == 1 and drives[0]["title"] == "persisted watch"


async def _make_saved_session_no_drive(session_dir: Path) -> None:
    """Persist a session that has NO Drive at all, so it carries no sidecar."""
    engine = Terrarium(session_dir=str(session_dir))
    await engine.__aenter__()
    root = Creature(
        creature_id="root",
        name="root",
        agent=_FakeAgent(name="root"),
        is_privileged=True,
    )
    await engine.add_creature(root, session=True)
    await engine.shutdown()


async def test_reading_driveless_session_creates_no_sidecar(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
    await _make_saved_session_no_drive(session_dir)

    path = next(session_dir.glob("*.kohakutr"))
    sidecar = Path(drive_sidecar_path(str(path)))
    assert not sidecar.exists()  # precondition: no Drive sidecar before viewing

    rows = await mod._read_saved_drives(path)
    assert rows == []
    # The read-only viewer must NOT conjure a sidecar just by looking.
    assert not sidecar.exists()


async def test_viewing_driveless_session_does_not_initialise_parent(tmp_path):
    """Reviewer repro: viewing a driveless session grew the parent 0 -> 106496.

    Constructing a writable ``SessionStore`` just to reach the sidecar initialises
    the parent ``.kohakutr`` database merely by looking. A bare (0-byte) session
    file that carries no Drive sidecar must come back with zero drives and its
    parent byte-size unchanged — the DB is never initialised, no WAL spun up.
    """
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    path = session_dir / "bare.kohakutr"
    path.touch()  # 0-byte, uninitialised parent — the reviewer's ``before 0``
    sidecar = Path(drive_sidecar_path(str(path)))
    assert not sidecar.exists()
    before = path.stat().st_size
    assert before == 0

    rows = await mod._read_saved_drives(path)
    assert rows == []

    after = path.stat().st_size
    assert after == before  # parent NOT initialised by viewing
    assert not sidecar.exists()
    # Viewing must not spin up WAL/shm companions on the parent either.
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


async def test_reading_existing_sidecar_does_not_mutate(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
    await _make_saved_session(session_dir)

    path = next(session_dir.glob("*.kohakutr"))
    parent_before = path.stat()
    sidecar = Path(drive_sidecar_path(str(path)))
    assert sidecar.exists()
    wal = Path(str(sidecar) + "-wal")
    shm = Path(str(sidecar) + "-shm")
    wal_before, shm_before = wal.exists(), shm.exists()
    before = sidecar.stat()

    rows = await mod._read_saved_drives(path)
    assert len(rows) == 1

    after = sidecar.stat()
    assert after.st_size == before.st_size
    assert after.st_mtime == before.st_mtime
    # A read-only open registers no writable companion and enables no WAL.
    assert wal.exists() == wal_before
    assert shm.exists() == shm_before
    # The parent .kohakutr is never opened, so it cannot be mutated either.
    parent_after = path.stat()
    assert parent_after.st_size == parent_before.st_size
    assert parent_after.st_mtime == parent_before.st_mtime


def test_route_missing_session_404(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/persistence/viewer")
    client = TestClient(app)
    resp = client.get("/api/persistence/viewer/no-such-session/drives")
    assert resp.status_code == 404


async def test_committed_wal_rows_are_read_offline(tmp_path, monkeypatch):
    """(round-3c defect) committed rows still in the sidecar ``-wal`` must be read.

    A live/uncheckpointed sidecar keeps freshly-committed Drive rows in its
    ``-wal``; an ``immutable=1`` open IGNORES the WAL and misses them, while a
    ``mode=ro`` open respects it. Build a sidecar with one checkpointed Drive plus
    one that lives ONLY in an open ``-wal`` and assert the offline read returns
    BOTH — the fail-first: the round-3c ``immutable=1`` open returns only the
    checkpointed one (stale/missed WAL row).
    """
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
    await _make_saved_session(session_dir)  # drive #1, checkpointed on shutdown

    path = next(session_dir.glob("*.kohakutr"))
    sidecar = str(drive_sidecar_path(str(path)))
    assert not Path(sidecar + "-wal").exists()  # clean close removed the -wal

    # A second writable connection commits drive #2 but never checkpoints/closes,
    # so that row lives only in the -wal the offline viewer must respect.
    live = SqliteDriveRepository(sidecar)
    try:
        rec2 = await live.create_drive(
            _create_req("wal-only watch"), actor=OPS, graph_id="g1"
        )
        assert Path(sidecar + "-wal").exists()  # drive #2 sits in the -wal

        rows = await mod._read_saved_drives(path)
        assert {r["title"] for r in rows} == {"persisted watch", "wal-only watch"}
        assert rec2.drive_id in {r["drive_id"] for r in rows}
    finally:
        live.close_blocking()


async def test_route_live_session_writer_returns_409(tmp_path, monkeypatch):
    """The saved viewer refuses a session while its canonical writer lock is held."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
    await _make_saved_session(session_dir)
    path = next(session_dir.glob("*.kohakutr"))

    holder = FileLock(str(path) + ".lock")
    holder.acquire()
    try:
        app = FastAPI()
        app.include_router(mod.router, prefix="/api/persistence/viewer")
        client = TestClient(app)
        resp = client.get(f"/api/persistence/viewer/{path.stem}/drives")
        assert resp.status_code == 409
        assert "live writer" in resp.json()["detail"]
    finally:
        holder.release()
