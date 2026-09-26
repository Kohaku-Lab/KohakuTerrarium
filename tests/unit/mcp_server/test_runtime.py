"""Tool-only runtime behavior with real tools, plugins and executor jobs."""

import asyncio

import pytest

from kohakuterrarium.core.backgroundify import PromotionResult
from kohakuterrarium.mcp_server.config import MCPToolsConfig
from kohakuterrarium.mcp_server.runtime import ToolRuntime
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError


class Guard(BasePlugin):
    name = "test_guard"

    def __init__(self):
        super().__init__()
        self.seen = []

    async def pre_tool_dispatch(self, call, context):
        assert context.host_agent is None
        if call.args.get("path") == "forbidden.txt":
            raise PluginBlockError("dispatch denied")
        return call

    async def pre_tool_execute(self, args, **kwargs):
        self.seen.append(kwargs["context"].working_dir)
        if args.get("path") == "execution-denied.txt":
            raise PluginBlockError("execution denied")


def test_runtime_rejects_unknown_options(tmp_path):
    with pytest.raises(ValueError, match="option"):
        ToolRuntime(
            MCPToolsConfig.model_validate(
                {
                    "workspace": tmp_path,
                    "tools": [{"name": "python", "config": {"timeot": 5}}],
                }
            )
        )


@pytest.mark.parametrize(
    "name, option",
    [
        ("python", "working_dir"),
        ("python", "env"),
        ("read", "timeout"),
        ("bash", "notify_controller_on_background_complete"),
    ],
)
def test_rejects_options_without_effect(tmp_path, name, option):
    with pytest.raises(ValueError, match="Unsupported options"):
        ToolRuntime(
            MCPToolsConfig.model_validate(
                {
                    "workspace": tmp_path,
                    "tools": [
                        {"name": name, "config": {option: {} if option == "env" else 1}}
                    ],
                }
            )
        )


async def test_plugin_load_failure_is_fatal_and_cleanup_runs(tmp_path):
    class BrokenGuard(BasePlugin):
        name = "broken_guard"
        unloaded = False

        async def on_load(self, context):
            raise ValueError("policy unavailable")

        async def on_unload(self):
            self.unloaded = True

    plugin = BrokenGuard()
    runtime = ToolRuntime(MCPToolsConfig(workspace=tmp_path), plugins=[plugin])
    with pytest.raises(ValueError, match="policy unavailable"):
        async with runtime:
            pytest.fail("must not serve with failed policy")
    assert plugin.unloaded
    with pytest.raises(RuntimeError, match="twice"):
        await runtime.__aenter__()


def test_llm_plugin_is_rejected(tmp_path):
    class ModelPlugin(BasePlugin):
        name = "model_plugin"

        async def pre_llm_call(self, messages, **kwargs):
            return messages

    with pytest.raises(ValueError, match="unsupported hook pre_llm_call"):
        ToolRuntime(MCPToolsConfig(workspace=tmp_path), plugins=[ModelPlugin()])


async def test_file_checks_plugins_and_isolated_jobs(tmp_path):
    guard = Guard()
    folder = tmp_path / "first"
    other = tmp_path / "second"
    folder.mkdir()
    other.mkdir()
    (folder / "note.txt").write_text("before")
    first = ToolRuntime(MCPToolsConfig(workspace=folder), plugins=[guard])
    second = ToolRuntime(MCPToolsConfig(workspace=other))
    async with first, second:
        blocked = await first.call("write", {"path": "note.txt", "content": "after"})
        assert not blocked.success
        assert (folder / "note.txt").read_text() == "before"
        assert (await first.call("read", {"path": "note.txt"})).success
        (folder / "note.txt").write_text("external change")
        assert not (
            await first.call("write", {"path": "note.txt", "content": "stale"})
        ).success
        assert (folder / "note.txt").read_text() == "external change"
        assert (await first.call("read", {"path": "note.txt"})).success
        assert (
            await first.call("write", {"path": "note.txt", "content": "after"})
        ).success
        assert (folder / "note.txt").read_text() == "after"
        denied = await first.call("write", {"path": "forbidden.txt", "content": "bad"})
        assert "dispatch denied" in denied.error
        denied = await first.call(
            "write", {"path": "execution-denied.txt", "content": "bad"}
        )
        assert "execution denied" in denied.error
        assert not (folder / "forbidden.txt").exists()
        assert not (folder / "execution-denied.txt").exists()
        assert guard.seen and all(path == folder for path in guard.seen)
        # Default workspace is not a file permission boundary. Reads still belong
        # to one instance: second cannot edit an absolute path first has read.
        assert not (
            await second.call(
                "write", {"path": str(folder / "note.txt"), "content": "bad"}
            )
        ).success
        outside = await first.call(
            "write", {"path": str(other / "outside.txt"), "content": "allowed"}
        )
        assert not outside.success and "retry" in outside.error
        assert not (other / "outside.txt").exists()
        outside = await first.call(
            "write", {"path": str(other / "outside.txt"), "content": "allowed"}
        )
        assert outside.success and (other / "outside.txt").read_text() == "allowed"
        background = await first.call(
            "python",
            {
                "code": "import time; from pathlib import Path; time.sleep(.3); Path('once').write_text('1'); print('complete')",
                "run_in_background": True,
            },
        )
        assert isinstance(background, PromotionResult)
        assert second.job(background.job_id)["error"] == "Unknown job"
        assert (await first.wait(background.job_id, 0.01))["state"] == "running"
        complete = await first.wait(background.job_id, 10)
        assert complete["state"] == "done" and "complete" in complete["output"]
        assert (folder / "once").read_text() == "1"
        assert not (other / "once").exists()
        assert first.executor.get_next_event_nowait() is None
        running = await first.call(
            "python", {"code": "import time; time.sleep(30)", "run_in_background": True}
        )
        assert await first.cancel(running.job_id)
        assert (await first.wait(running.job_id, 5))["state"] == "cancelled"
    restarted = ToolRuntime(MCPToolsConfig(workspace=folder))
    assert restarted.job(background.job_id)["error"] == "Unknown job"


async def test_promotion_and_waiter_cancellation_do_not_repeat_or_kill_work(tmp_path):
    async with ToolRuntime(MCPToolsConfig(workspace=tmp_path)) as runtime:
        call = asyncio.create_task(
            runtime.call(
                "python",
                {
                    "code": "import time; from pathlib import Path; time.sleep(.3); Path('marker').write_text('once')",
                },
            )
        )
        while not runtime.executor.get_running_jobs():
            await asyncio.sleep(0)
        job_id = runtime.executor.get_running_jobs()[0].job_id
        assert runtime.promote(job_id)
        promoted = await call
        assert promoted.job_id == job_id
        waiter = asyncio.create_task(runtime.wait(job_id, 10))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert (await runtime.wait(job_id, 10))["state"] == "done"
        assert (tmp_path / "marker").read_text() == "once"
        assert len(runtime.executor.job_store.get_all_statuses()) == 1


async def test_shutdown_cancels_owned_jobs_and_rejects_later_calls(tmp_path):
    runtime = ToolRuntime(MCPToolsConfig(workspace=tmp_path))
    async with runtime:
        running = await runtime.call(
            "python", {"code": "import time; time.sleep(30)", "run_in_background": True}
        )
    assert runtime.job(running.job_id)["state"] == "cancelled"
    with pytest.raises(RuntimeError, match="running"):
        await runtime.call("read", {"path": "anything"})
