"""SQLite connection ownership when Drive initialization is cancelled."""

import asyncio
import sqlite3
import threading

import pytest

from kohakuterrarium.terrarium.drive.store import SqliteDriveRepository


@pytest.mark.parametrize("retry", [False, True])
async def test_cancelled_open_releases_every_connection(tmp_path, retry):
    path = tmp_path / "cancelled.drives"
    repo = SqliteDriveRepository(path)
    entered = threading.Event()
    release = threading.Event()
    connections = []
    original = repo._open_conn

    def blocked_open():
        entered.set()
        if not release.wait(5):
            raise TimeoutError("open barrier was not released")
        original()
        connections.append(repo._conn)

    repo._open_conn = blocked_open
    opening = asyncio.create_task(repo._ensure_open())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening
        release.set()
        if retry:
            await repo._ensure_open()
        repo.close_blocking()
        assert connections
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
        path.replace(tmp_path / "released.drives")
    finally:
        release.set()
        await asyncio.gather(opening, return_exceptions=True)
        repo.close_blocking()
        for connection in connections:
            connection.close()
