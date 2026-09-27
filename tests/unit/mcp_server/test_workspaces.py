"""Registered names own independent runtimes, including across reincarnation."""

import asyncio

import pytest

from kohakuterrarium.mcp_server.config import GlobalToolsConfig
from kohakuterrarium.mcp_server.workspaces import WorkspacePool, WorkspaceRegistry


async def test_failed_cleanup_quarantines_registration(tmp_path, monkeypatch):
    registry = WorkspaceRegistry(tmp_path / "registry.json")
    registry.add("project", tmp_path)
    async with WorkspacePool(GlobalToolsConfig(), registry) as pool:
        async with pool.use("project") as runtime:
            pass
        original = runtime.__aexit__

        async def failed_close(*args):
            await original(*args)
            raise OSError("cleanup failed")

        monkeypatch.setattr(runtime, "__aexit__", failed_close)
        with pytest.raises(OSError, match="cleanup"):
            await pool.remove("project", force=True)
        assert "project" in registry.read()
        with pytest.raises(ValueError, match="stopping"):
            async with pool.use("project"):
                pass
        monkeypatch.setattr(runtime, "__aexit__", original)
        await pool.remove("project", force=True)


async def test_shutdown_cancels_pending_first_load(tmp_path, monkeypatch):
    registry = WorkspaceRegistry(tmp_path / "registry.json")
    entry = registry.add("project", tmp_path)
    pool = WorkspacePool(GlobalToolsConfig(), registry)
    await pool.__aenter__()
    lock = asyncio.Lock()
    await lock.acquire()
    pool._locks[entry.registration_id] = lock

    async def pending():
        async with pool.use("project"):
            pytest.fail("admitted after shutdown")

    task = asyncio.create_task(pending())
    await asyncio.sleep(0)
    try:
        await pool.__aexit__(None, None, None)
        assert (
            task.done()
        ), "shutdown left an admitted request waiting on initialization"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        lock.release()


async def test_same_directory_isolation_removal_and_restart(tmp_path):
    registry = WorkspaceRegistry(tmp_path / "registry.json")
    first = registry.add("first", tmp_path)
    registry.add("second", tmp_path)
    (tmp_path / "note.txt").write_text("original")
    async with WorkspacePool(GlobalToolsConfig(), registry) as pool:
        assert all(w["state"] == "unloaded" for w in pool.list())
        async with pool.use("first") as a:
            await a.call("read", {"path": "note.txt"})
            job = await a.call("python", {"code": "print('first')"})
        async with pool.use("second") as b:
            assert b.instance_id != a.instance_id
            assert b.job(job.job_id)["error"]
            blocked = await b.call("write", {"path": "note.txt", "content": "wrong"})
            assert blocked.error
        assert (tmp_path / "note.txt").read_text() == "original"
        async with pool.use("first") as a:
            pending = await a.call(
                "python",
                {
                    "code": "import time; time.sleep(60)",
                    "run_in_background": True,
                },
            )
        with pytest.raises(ValueError, match="busy"):
            await pool.remove("first")
        await pool.remove("first", force=True)
        replacement = registry.add("first", tmp_path)
        assert replacement.registration_id != first.registration_id
        async with pool.use("first") as fresh:
            assert fresh.job(pending.job_id)["error"]
            assert fresh.job(job.job_id)["error"]
        with pytest.raises(ValueError, match="Unknown workspace"):
            async with pool.use("missing"):
                pass
    async with WorkspacePool(GlobalToolsConfig(), registry) as restarted:
        async with restarted.use("first") as fresh:
            assert fresh.job(job.job_id)["error"]


async def test_missing_directory_is_local_failure_and_registration_is_dynamic(tmp_path):
    registry = WorkspaceRegistry(tmp_path / "registry.json")
    missing = tmp_path / "missing"
    missing.mkdir()
    registry.add("missing", missing)
    missing.rmdir()
    async with WorkspacePool(GlobalToolsConfig(), registry) as pool:
        with pytest.raises(ValueError, match="directory"):
            async with pool.use("missing"):
                pass
        registry.add("working", tmp_path)
        async with pool.use("working") as runtime:
            result = await runtime.call("python", {"code": "print('ok')"})
            assert result.get_text_output().strip() == "ok"
        assert (
            next(w for w in pool.list() if w["workspace_id"] == "missing")["state"]
            == "unavailable"
        )
