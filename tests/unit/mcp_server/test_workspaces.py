"""Registered names own independent runtimes, including across reincarnation."""

import asyncio
import json

import pytest

from kohakuterrarium.mcp_server.config import GlobalToolsConfig
from kohakuterrarium.mcp_server.workspaces import WorkspacePool, WorkspaceRegistry
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry


@pytest.mark.parametrize("kind", ["creature", "subagent"])
async def test_unclosed_sessions_keep_workspace_busy(tmp_path, kind):
    target = tmp_path / "worker.json"
    definition = {"name": "worker", "tools": []}
    if kind == "creature":
        definition.update(
            input={"type": "none"}, output={"type": "none"}, compact={"enabled": False}
        )
    target.write_text(json.dumps(definition), encoding="utf-8")
    registry = WorkspaceRegistry(tmp_path / "registry.json")
    registration = registry.add("project", tmp_path)
    config = GlobalToolsConfig(
        tools=[], delegation={"worker": {"kind": kind, "config": str(target)}}
    )
    model = ScriptedLLM(
        [
            ScriptEntry("answer", match="first"),
            ScriptEntry("slow answer", match="slow", delay_per_chunk=0.2, chunk_size=1),
        ]
    )
    async with WorkspacePool(config, registry, llm_factory=lambda _: model) as pool:
        async with pool.use("project") as runtime:
            submitted = await runtime.delegation.submit("worker", "first")
            assert (await runtime.wait(submitted["job_id"]))["state"] == "done"
        assert runtime.is_busy
        with pytest.raises(ValueError, match="busy"):
            await pool.remove("project")
        if kind == "creature":
            slow = await runtime.delegation.submit(
                "worker", "slow", session_id=submitted["session_id"]
            )
            await runtime.cancel(slow["job_id"])
            assert runtime.delegation.sessions()[0]["state"] == "stopped"
            assert runtime.is_busy
            with pytest.raises(ValueError, match="busy"):
                await pool.remove("project")
        assert registry.read()["project"] == registration
        await runtime.delegation.close_session(submitted["session_id"])
        assert not runtime.is_busy
        await pool.remove("project")
        assert not registry.read()


@pytest.mark.parametrize("operation", ["remove", "shutdown"])
async def test_failed_cleanup_quarantines_registration(tmp_path, operation):
    registry = WorkspaceRegistry(tmp_path / "registry.json")
    registry.add("project", tmp_path)
    plugin = tmp_path / "cleanup.py"
    plugin.write_text(
        "from kohakuterrarium.modules.plugin.base import BasePlugin\n"
        "class Cleanup(BasePlugin):\n"
        "    name = 'cleanup'\n"
        "    async def on_load(self, context):\n"
        "        self.workspace = context.working_dir\n"
        "    async def on_unload(self):\n"
        "        if not (self.workspace / 'release').exists():\n"
        "            raise OSError('cleanup failed')\n"
        "        (self.workspace / 'released').touch()\n"
    )
    config = GlobalToolsConfig(
        plugins=[
            {
                "name": "cleanup",
                "type": "custom",
                "module": str(plugin),
                "class": "Cleanup",
            }
        ]
    )
    async with WorkspacePool(config, registry) as pool:

        async def close():
            if operation == "remove":
                await pool.remove("project", force=True)
            else:
                await pool.__aexit__(None, None, None)

        async with pool.use("project"):
            pass
        with pytest.raises(OSError, match="cleanup"):
            await close()
        assert "project" in registry.read()
        with pytest.raises(ValueError, match="stopping"):
            async with pool.use("project"):
                pass
        (tmp_path / "release").touch()
        await close()
        assert (tmp_path / "released").exists()
        assert bool(registry.read()) == (operation == "shutdown")


async def test_shutdown_cancels_pending_first_load(tmp_path):
    registry = WorkspaceRegistry(tmp_path / "registry.json")
    registry.add("project", tmp_path)
    plugin = tmp_path / "loading.py"
    plugin.write_text(
        "import asyncio\n"
        "from kohakuterrarium.modules.plugin.base import BasePlugin\n"
        "class Loading(BasePlugin):\n"
        "    name = 'loading'\n"
        "    async def on_load(self, context):\n"
        "        self.workspace = context.working_dir\n"
        "        (self.workspace / 'entered').touch()\n"
        "        await asyncio.Event().wait()\n"
        "    async def on_unload(self):\n"
        "        (self.workspace / 'unloaded').touch()\n"
    )
    config = GlobalToolsConfig(
        plugins=[
            {
                "name": "loading",
                "type": "custom",
                "module": str(plugin),
                "class": "Loading",
            }
        ]
    )
    pool = WorkspacePool(config, registry)
    await pool.__aenter__()

    async def pending():
        async with pool.use("project"):
            pytest.fail("admitted after shutdown")

    task = asyncio.create_task(pending())
    for _ in range(100):
        if (tmp_path / "entered").exists():
            break
        await asyncio.sleep(0.01)
    try:
        await pool.__aexit__(None, None, None)
        assert (
            task.done()
        ), "shutdown left an admitted request waiting on initialization"
        assert (tmp_path / "unloaded").exists()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


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
