"""Delegation admission and lifecycle with real KT runtimes and scripted models."""

import asyncio
import json

import pytest

from kohakuterrarium.mcp_server.config import MCPToolsConfig
from kohakuterrarium.mcp_server.runtime import ToolRuntime
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry


@pytest.fixture(autouse=True)
def scripted_compaction(monkeypatch):
    monkeypatch.setattr(
        "kohakuterrarium.core.agent_compact.create_llm_from_profile_name",
        lambda *_args, **_kwargs: ScriptedLLM(["compacted"]),
    )


def configuration(tmp_path, kind="creature", **extra):
    target = tmp_path / f"{kind}.json"
    data = {
        "name": "worker",
        "tools": [],
        "system_prompt": "Follow the task.",
        **extra,
    }
    if kind == "creature":
        data.update(
            input={"type": "none"}, output={"type": "none"}, compact={"enabled": False}
        )
    target.write_text(json.dumps(data), encoding="utf-8")
    return MCPToolsConfig.model_validate(
        {
            "workspace": tmp_path,
            "tools": [],
            "delegation": {
                "worker": {
                    "kind": kind,
                    "config": str(target),
                    "description": "Test worker",
                }
            },
        }
    )


async def test_creature_turns_busy_wait_cancel_restore_and_close(tmp_path, capsys):
    models = []

    def provider(_target):
        model = ScriptedLLM(
            [
                ScriptEntry("first answer", match="remember"),
                ScriptEntry(
                    "slow answer", match="slow", delay_per_chunk=0.2, chunk_size=1
                ),
                ScriptEntry("continued answer", match="continue"),
            ]
        )
        models.append(model)
        return model

    config = configuration(tmp_path)
    target = tmp_path / "creature.json"
    definition = json.loads(target.read_text(encoding="utf-8"))
    definition["output"] = {"type": "stdout"}
    target.write_text(json.dumps(definition), encoding="utf-8")
    async with ToolRuntime(config, llm_factory=provider) as runtime:
        delegation = runtime.delegation
        assert delegation.targets()[0]["name"] == "worker"
        first = await delegation.submit("worker", "remember blue")
        sid, job = first["session_id"], first["job_id"]
        done = await runtime.wait(job, 10)
        assert done["state"] == "done" and "first answer" in done["output"]
        slow = await delegation.submit("worker", "slow", session_id=sid)
        busy = await delegation.submit("worker", "must not run", session_id=sid)
        assert busy["error"] == "Session is busy" and busy["job_id"] == slow["job_id"]
        assert (await runtime.wait(slow["job_id"], 0))["state"] in {
            "pending",
            "running",
        }
        await asyncio.sleep(0.1)
        assert await runtime.cancel(slow["job_id"])
        assert runtime.job(slow["job_id"])["state"] == "cancelled"
        assert delegation.sessions()[0]["state"] == "stopped"
        continued = await delegation.submit("worker", "continue", session_id=sid)
        assert (await runtime.wait(continued["job_id"], 10))["state"] == "done"
        assert (
            capsys.readouterr().out == ""
        ), "resume must retain headless default output"
        assert len(models) == 2
        assert "remember blue" in json.dumps(models[-1].call_log)
        history = delegation.history(sid, limit=2)
        assert len(history["events"]) == 2 and history["next_cursor"] > 0
        assert "first answer" in "".join(
            e.get("text", "") for e in delegation.history(sid, limit=200)["events"]
        )
        await delegation.close_session(sid)
        assert delegation.sessions()[0]["state"] == "closed"
        with pytest.raises(ValueError, match="closed"):
            await delegation.submit("worker", "continue", session_id=sid)


@pytest.mark.parametrize("cancellations", [1, 2])
async def test_cancel_during_subagent_cleanup_waits_for_provider(
    tmp_path, cancellations
):
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class ClosingLLM(ScriptedLLM):
        async def close(self):
            entered.set()
            await release.wait()
            finished.set()

    model = ClosingLLM(["answer"])
    async with ToolRuntime(
        configuration(tmp_path, "subagent"), llm_factory=lambda _: model
    ) as runtime:
        submitted = await runtime.delegation.submit("worker", "task")
        await asyncio.wait_for(entered.wait(), 5)
        pending = [
            asyncio.create_task(runtime.cancel(submitted["job_id"]))
            for _ in range(cancellations)
        ]
        try:
            await asyncio.sleep(0.05)
            assert not any(
                task.done() for task in pending
            ), "cancellation must await owned resource cleanup"
        finally:
            release.set()
            await asyncio.gather(*pending)
        assert finished.is_set()
        assert runtime.job(submitted["job_id"])["state"] == "cancelled"
        events = runtime.delegation.history(submitted["session_id"])["events"]
        assert any(e["kind"] == "job_end" and e["state"] == "cancelled" for e in events)


async def test_standalone_subagent_tools_workspace_and_no_continuation(tmp_path):
    model = ScriptedLLM(
        [
            "[/write]\n@@path=answer.txt\n@@content=delegated\n[write/]",
            "written",
        ]
    )
    config = configuration(
        tmp_path, "subagent", tools=[{"name": "write"}], can_modify=True
    )
    async with ToolRuntime(config, llm_factory=lambda _: model) as runtime:
        first = await runtime.delegation.submit("worker", "write the file")
        result = await runtime.wait(first["job_id"], 10)
        assert result["error"] is None, result["error"]
        assert result["state"] == "done", result
        assert (tmp_path / "answer.txt").read_text() == "delegated"
        assert "written" in result["output"]
        assert result["metadata"]["kind"] == "subagent"
        assert "answer.txt" in json.dumps(
            runtime.delegation.history(first["session_id"])
        )
        with pytest.raises(ValueError, match="one-shot"):
            await runtime.delegation.submit(
                "worker", "again", session_id=first["session_id"]
            )
        assert not await runtime.delegation.send(first["job_id"], "too late")


async def test_cancel_one_subagent_does_not_cancel_another(tmp_path):
    config = configuration(tmp_path, "subagent")

    def provider(_):
        return ScriptedLLM([ScriptEntry("abcdef", delay_per_chunk=0.1, chunk_size=1)])

    async with ToolRuntime(config, llm_factory=provider) as runtime:
        first = await runtime.delegation.submit("worker", "first")
        second = await runtime.delegation.submit("worker", "second")
        await asyncio.sleep(0.05)
        assert await runtime.delegation.send(first["job_id"], "additional instruction")
        assert (await runtime.wait(first["job_id"], 0.01))["state"] == "running"
        assert await runtime.cancel(first["job_id"])
        assert runtime.job(first["job_id"])["state"] == "cancelled"
        assert (await runtime.wait(second["job_id"], 10))["state"] == "done"


async def test_unknown_targets_and_sessions_never_create_execution(tmp_path):
    async with ToolRuntime(
        configuration(tmp_path), llm_factory=lambda _: pytest.fail("no model")
    ) as runtime:
        with pytest.raises(ValueError, match="Unknown target"):
            await runtime.delegation.submit("other", "task")
        with pytest.raises(ValueError, match="Unknown session"):
            await runtime.delegation.submit("worker", "task", session_id="foreign")
        assert runtime.jobs() == []


async def test_shutdown_before_task_starts_settles_job_and_releases_admission(tmp_path):
    runtime = ToolRuntime(
        configuration(tmp_path), llm_factory=lambda _: ScriptedLLM(["unused"])
    )
    async with runtime:
        submitted = await runtime.delegation.submit("worker", "task")
    assert runtime.job(submitted["job_id"])["state"] == "cancelled"
    assert runtime.delegation.sessions()[0]["job_id"] is None
    assert runtime.delegation.sessions()[0]["state"] == "closed"


async def test_failed_start_is_terminal_and_close_releases_provider(tmp_path):
    providers = []

    class TrackingLLM(ScriptedLLM):
        closed = False

        async def close(self):
            self.closed = True

    def provider(_):
        model = TrackingLLM(["unused"])
        providers.append(model)
        return model

    config = configuration(
        tmp_path,
        "subagent",
        plugins=[
            {
                "name": "missing",
                "type": "custom",
                "module": "missing.py",
                "class": "Missing",
            }
        ],
    )
    async with ToolRuntime(config, llm_factory=provider) as runtime:
        submitted = await runtime.delegation.submit("worker", "task")
        result = await runtime.wait(submitted["job_id"], 10)
        assert result["state"] == "error" and result["error"]
        assert providers[0].closed and providers[0].call_count == 0
        assert runtime.delegation.sessions()[0]["job_id"] is None


async def test_autonomous_turns_remain_enabled_and_observable(tmp_path):
    model = ScriptedLLM(
        [
            ScriptEntry("initial answer", match="hello"),
            ScriptEntry("autonomous work", delay_per_chunk=0.1, chunk_size=1),
        ]
    )
    config = configuration(
        tmp_path, triggers=[{"type": "timer", "interval": 0.3, "prompt": "autonomous"}]
    )
    async with ToolRuntime(config, llm_factory=lambda _: model) as runtime:
        result = await runtime.delegation.submit("worker", "hello")
        assert (await runtime.wait(result["job_id"], 10))["state"] == "done"
        for _ in range(200):
            if model.call_count >= 2:
                break
            await asyncio.sleep(0.01)
        assert model.call_count >= 2
        busy = await runtime.delegation.submit(
            "worker", "must not execute", session_id=result["session_id"]
        )
        assert busy["error"] == "Session is busy" and busy["job_id"] is None
        events = runtime.delegation.history(result["session_id"], limit=200)["events"]
        assert any(e["kind"] == "trigger_fired" for e in events)
        await runtime.delegation.close_session(result["session_id"])
        calls = model.call_count
        await asyncio.sleep(0.35)
        assert model.call_count == calls


async def test_stop_cancels_previous_turn_background_tools(tmp_path):
    tool = tmp_path / "background.py"
    tool.write_text(
        "import asyncio\n"
        "from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode\n"
        "class Background(BaseTool):\n"
        "    tool_name = 'background'\n"
        "    description = 'Hold a workspace resource until cancelled'\n"
        "    execution_mode = ExecutionMode.BACKGROUND\n"
        "    needs_context = True\n"
        "    async def _execute(self, args, context=None, **kwargs):\n"
        "        (context.working_dir / 'started').touch()\n"
        "        try:\n"
        "            await asyncio.Event().wait()\n"
        "        finally:\n"
        "            (context.working_dir / 'released').touch()\n"
    )
    config = configuration(
        tmp_path,
        tool_format="kohaku",
        tools=[
            {
                "name": "background",
                "type": "custom",
                "module": str(tool),
                "class": "Background",
            }
        ],
    )
    model = ScriptedLLM(
        [
            ScriptEntry("first answer [/background][background/]", match="first"),
            ScriptEntry("slow answer", chunk_size=1, delay_per_chunk=0.2),
        ]
    )
    async with ToolRuntime(config, llm_factory=lambda _: model) as runtime:
        submitted = await runtime.delegation.submit("worker", "first")
        assert (await runtime.wait(submitted["job_id"], 10))["state"] == "done"
        for _ in range(100):
            if (tmp_path / "started").exists():
                break
            await asyncio.sleep(0.01)
        assert (tmp_path / "started").exists(), json.dumps(
            runtime.delegation.history(submitted["session_id"], limit=200)
        )
        assert not (tmp_path / "released").exists()
        second = await runtime.delegation.submit(
            "worker", "slow", session_id=submitted["session_id"]
        )
        await asyncio.sleep(0.05)
        assert await runtime.cancel(second["job_id"])
        assert (tmp_path / "released").exists()
        assert runtime.delegation.sessions()[0]["state"] == "stopped"


async def test_cleanup_failure_is_reported_without_leaving_running_job(tmp_path):
    class BrokenClose(ScriptedLLM):
        fail = True
        released = False

        async def close(self):
            if self.fail:
                raise RuntimeError("provider cleanup failed")
            self.released = True

    model = BrokenClose(["answer"])
    async with ToolRuntime(
        configuration(tmp_path, "subagent"),
        llm_factory=lambda _: model,
    ) as runtime:
        submitted = await runtime.delegation.submit("worker", "task")
        result = await runtime.wait(submitted["job_id"], 10)
        assert result["state"] == "error" and "cleanup failed" in result["error"]
        assert runtime.delegation.sessions()[0]["job_id"] is None
        try:
            with pytest.raises(RuntimeError, match="cleanup failed"):
                await runtime.delegation.close_session(submitted["session_id"])
            assert runtime.delegation.sessions()[0]["state"] != "closed"
            assert runtime.is_busy
        finally:
            model.fail = False
            await runtime.delegation.close_session(submitted["session_id"])
        assert model.released
        assert runtime.delegation.sessions()[0]["state"] == "closed"


async def test_close_racing_new_turn_cannot_restart_a_closed_session(tmp_path):
    config = configuration(tmp_path)
    async with ToolRuntime(
        config, llm_factory=lambda _: ScriptedLLM(["reply"])
    ) as runtime:
        first = await runtime.delegation.submit("worker", "first")
        await runtime.wait(first["job_id"], 10)
        close = asyncio.create_task(
            runtime.delegation.close_session(first["session_id"])
        )
        await asyncio.sleep(0)
        response = await runtime.delegation.submit(
            "worker", "must not run", session_id=first["session_id"]
        )
        assert response["error"] == "Session is busy"
        await close
        assert runtime.delegation.sessions()[0]["state"] == "closed"


async def test_parallel_close_keeps_session_busy_after_one_cleanup_fails(tmp_path):
    first_entered, first_release = asyncio.Event(), asyncio.Event()
    second_entered, second_release = asyncio.Event(), asyncio.Event()

    class Provider(ScriptedLLM):
        attempts = 0

        async def close(self):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("initial cleanup failed")
            if self.attempts == 2:
                first_entered.set()
                await first_release.wait()
                raise RuntimeError("first close failed")
            second_entered.set()
            await second_release.wait()

    model = Provider(["answer"])
    async with ToolRuntime(
        configuration(tmp_path, "subagent"), llm_factory=lambda _: model
    ) as runtime:
        submitted = await runtime.delegation.submit("worker", "task")
        assert (await runtime.wait(submitted["job_id"]))["state"] == "error"
        sid = submitted["session_id"]
        first = asyncio.create_task(runtime.delegation.close_session(sid))
        second = None
        try:
            await asyncio.wait_for(first_entered.wait(), 5)
            second = asyncio.create_task(runtime.delegation.close_session(sid))
            await asyncio.sleep(0)
            first_release.set()
            with pytest.raises(RuntimeError, match="first close failed"):
                await first
            await asyncio.wait_for(second_entered.wait(), 5)
            assert runtime.delegation.sessions()[0]["state"] == "busy"
        finally:
            first_release.set()
            second_release.set()
            await asyncio.gather(
                *[task for task in (first, second) if task], return_exceptions=True
            )
        assert runtime.delegation.sessions()[0]["state"] == "closed"


@pytest.mark.parametrize("kind", ["creature", "subagent"])
async def test_invalid_definition_is_not_left_starting(tmp_path, kind):
    class Provider(ScriptedLLM):
        released = False

        async def close(self):
            self.released = True

    model = Provider()
    config = configuration(tmp_path, kind, tools=[{"name": "no_such_tool"}])
    async with ToolRuntime(config, llm_factory=lambda _: model) as runtime:
        job = await runtime.delegation.submit("worker", "task")
        assert (await runtime.wait(job["job_id"], 10))["state"] == "error"
        assert runtime.delegation.sessions()[0]["state"] == "error"
    assert model.released


async def test_waiter_disconnect_and_parallel_creatures(tmp_path):
    config = configuration(tmp_path)
    models = []

    def provider(_):
        model = ScriptedLLM([ScriptEntry("reply", chunk_size=1, delay_per_chunk=0.05)])
        models.append(model)
        return model

    async with ToolRuntime(config, llm_factory=provider) as runtime:
        first, second = await asyncio.gather(
            runtime.delegation.submit("worker", "first isolated task"),
            runtime.delegation.submit("worker", "second isolated task"),
        )
        waiter = asyncio.create_task(runtime.wait(first["job_id"], 10))
        await asyncio.sleep(0.05)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert (await runtime.wait(first["job_id"], 10))["state"] == "done"
        assert (await runtime.wait(second["job_id"], 10))["state"] == "done"
        assert "second isolated task" not in str(models[0].call_log)
        assert "first isolated task" not in str(models[1].call_log)


async def test_subagent_job_stays_running_until_owned_cleanup_finishes(tmp_path):
    cleaning, release = asyncio.Event(), asyncio.Event()

    class SlowClose(ScriptedLLM):
        async def close(self):
            cleaning.set()
            await release.wait()

    async with ToolRuntime(
        configuration(tmp_path, "subagent"),
        llm_factory=lambda _: SlowClose(["answer"]),
    ) as runtime:
        submitted = await runtime.delegation.submit("worker", "task")
        try:
            await asyncio.wait_for(cleaning.wait(), 5)
            status = runtime.job(submitted["job_id"])
            assert status["state"] == "running"
            assert status["metadata"]["session_id"] == submitted["session_id"]
        finally:
            release.set()
        assert (await runtime.wait(submitted["job_id"], 10))["state"] == "done"


async def test_shutdown_error_does_not_skip_other_sessions_or_direct_jobs(tmp_path):
    models = []

    class TrackingLLM(ScriptedLLM):
        closed = False
        fail = True

        async def close(self):
            self.closed = True
            if self is models[0] and self.fail:
                raise RuntimeError("first provider cleanup failed")

    def provider(_):
        model = TrackingLLM(["reply"])
        models.append(model)
        return model

    config = configuration(tmp_path)
    config.tools = MCPToolsConfig(workspace=tmp_path).tools
    runtime = ToolRuntime(config, llm_factory=provider)
    try:
        with pytest.raises(RuntimeError, match="cleanup failed"):
            async with runtime:
                for _ in range(2):
                    submitted = await runtime.delegation.submit("worker", "task")
                    await runtime.wait(submitted["job_id"], 10)
                direct = await runtime.call(
                    "python",
                    {"code": "import time; time.sleep(60)", "run_in_background": True},
                )
        assert all(model.closed for model in models)
        assert runtime.job(direct.job_id)["state"] == "cancelled"
    finally:
        for model in models:
            model.fail = False
        await runtime.close()
