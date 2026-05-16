"""Unit tests for :mod:`kohakuterrarium.modules.subagent.manager`.

Behavior-first: SubAgentManager registers configs, spawns sub-agents
that actually run against a ScriptedLLM, tracks job status, enforces the
depth limit, resolves child budgets per config, and cleans up jobs.
"""

import pytest

from kohakuterrarium.core.budget import IterationBudget
from kohakuterrarium.core.job import JobState, JobType
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.subagent.manager import SubAgentManager
from kohakuterrarium.testing.llm import ScriptedLLM


def _manager(llm=None, **kwargs):
    llm = llm or ScriptedLLM(["done"])
    return SubAgentManager(Registry(), llm, **kwargs)


class TestRegistration:
    def test_register_and_lookup_config(self):
        mgr = _manager()
        cfg = SubAgentConfig(name="explore", description="finds code")
        mgr.register(cfg)
        assert mgr.get_config("explore") is cfg
        assert mgr.list_subagents() == ["explore"]

    def test_get_config_missing_returns_none(self):
        assert _manager().get_config("ghost") is None

    def test_subagent_info_derived_from_config(self):
        mgr = _manager()
        mgr.register(
            SubAgentConfig(name="critic", description="critiques", can_modify=True)
        )
        info = mgr.get_subagent_info("critic")
        assert info.name == "critic"
        assert info.can_modify is True
        assert mgr.get_subagent_info("missing") is None

    def test_subagents_prompt_lists_registered(self):
        mgr = _manager()
        mgr.register(SubAgentConfig(name="explore", description="finds"))
        prompt = mgr.get_subagents_prompt()
        assert "## Available Sub-Agents" in prompt
        assert "- explore: finds" in prompt

    def test_subagents_prompt_empty_when_none_registered(self):
        assert _manager().get_subagents_prompt() == ""

    def test_subagents_prompt_native_format_mentions_task_param(self):
        mgr = _manager(tool_format="native")
        mgr.register(SubAgentConfig(name="explore", description="finds"))
        prompt = mgr.get_subagents_prompt()
        # Native mode hint references the API ``task`` param.
        assert "task" in prompt
        assert "API" in prompt

    def test_register_warns_on_tools_missing_from_parent_registry(self):
        # A config referencing tools the parent doesn't have still
        # registers (the parent may add them later) — it just logs.
        mgr = _manager()
        cfg = SubAgentConfig(name="x", tools=["ghost_tool"])
        mgr.register(cfg)
        assert mgr.get_config("x") is cfg


class TestSpawn:
    async def test_spawn_unregistered_raises(self):
        mgr = _manager()
        with pytest.raises(ValueError, match="not registered"):
            await mgr.spawn("ghost", "task")

    async def test_spawn_runs_subagent_and_records_result(self):
        mgr = _manager(ScriptedLLM(["sub-agent finished the task"]))
        mgr.register(SubAgentConfig(name="explore", max_turns=2))
        job_id = await mgr.spawn("explore", "find the bug", background=False)
        result = mgr.get_result(job_id)
        assert result is not None
        assert result.success is True
        assert result.output == "sub-agent finished the task"
        # Job status reflects completion.
        status = mgr.get_status(job_id)
        assert status.state is JobState.DONE
        assert status.job_type is JobType.SUBAGENT

    async def test_spawn_background_returns_before_completion(self):
        mgr = _manager(ScriptedLLM(["bg result"]))
        mgr.register(SubAgentConfig(name="explore", max_turns=2))
        job_id = await mgr.spawn("explore", "task", background=True)
        # The job is tracked even if not yet finished.
        assert job_id in mgr._tasks
        result = await mgr.wait_for(job_id)
        assert result.output == "bg result"

    async def test_depth_limit_blocks_spawn_with_error_result(self):
        # A manager already at max depth must refuse to spawn and store
        # an ERROR result instead of running the sub-agent.
        mgr = _manager(current_depth=3, max_depth=3)
        mgr.register(SubAgentConfig(name="explore"))
        job_id = await mgr.spawn("explore", "task")
        result = mgr.get_result(job_id)
        assert result.success is False
        assert "depth limit" in result.error
        assert mgr.get_status(job_id).state is JobState.ERROR

    async def test_spawn_inherits_parent_executor_context_builder(self):
        # When the manager has a parent executor, the spawned sub-agent
        # inherits its tool-context builder (working dir, file guards).
        class _FakeExecutor:
            def _build_tool_context(self):
                return "parent-context"

        mgr = _manager(ScriptedLLM(["done"]))
        mgr._parent_executor = _FakeExecutor()
        mgr.register(SubAgentConfig(name="explore", max_turns=1))
        job_id = await mgr.spawn("explore", "task", background=False)
        job = mgr._jobs.get(job_id)
        # The sub-agent's context builder is the parent's.
        assert job.subagent._build_tool_context() == "parent-context"

    async def test_spawn_wires_session_store_for_persistence(self):
        # A manager with a session store passes it to spawned sub-agents
        # along with the parent name and a fresh run index.
        class _FakeStore:
            def next_subagent_run(self, parent, name):
                return 7

        mgr = _manager(ScriptedLLM(["done"]))
        mgr._session_store = _FakeStore()
        mgr._parent_name = "controller"
        mgr.register(SubAgentConfig(name="explore", max_turns=1))
        job_id = await mgr.spawn("explore", "task", background=False)
        job = mgr._jobs.get(job_id)
        assert job.subagent._parent_name == "controller"
        assert job.subagent._run_index == 7

    async def test_spawn_from_event_extracts_task_and_background_flag(self):
        from kohakuterrarium.parsing.events import SubAgentCallEvent

        mgr = _manager(ScriptedLLM(["event-driven result"]))
        mgr.register(SubAgentConfig(name="explore", max_turns=2))
        event = SubAgentCallEvent(
            name="explore",
            args={"task": "do it", "run_in_background": False},
        )
        job_id, is_background = await mgr.spawn_from_event(event)
        assert is_background is False
        result = await mgr.wait_for(job_id)
        assert result.output == "event-driven result"


class TestChildBudget:
    def test_allocation_creates_fresh_isolated_budget(self):
        mgr = _manager()
        mgr.iteration_budget = IterationBudget(remaining=100, total=100)
        cfg = SubAgentConfig(name="x", budget_allocation=5)
        budget = mgr._resolve_child_budget(cfg)
        assert budget is not None
        assert budget.total == 5
        assert budget.remaining == 5
        # The parent budget is untouched.
        assert mgr.iteration_budget.remaining == 100

    def test_inherit_reuses_parent_budget(self):
        mgr = _manager()
        parent_budget = IterationBudget(remaining=50, total=100)
        mgr.iteration_budget = parent_budget
        cfg = SubAgentConfig(name="x", budget_inherit=True, budget_allocation=None)
        assert mgr._resolve_child_budget(cfg) is parent_budget

    def test_no_inherit_no_allocation_yields_none(self):
        mgr = _manager()
        mgr.iteration_budget = IterationBudget(remaining=50, total=100)
        cfg = SubAgentConfig(name="x", budget_inherit=False, budget_allocation=None)
        assert mgr._resolve_child_budget(cfg) is None

    def test_inherit_with_no_parent_budget_yields_none(self):
        mgr = _manager()
        # mgr.iteration_budget defaults to None.
        cfg = SubAgentConfig(name="x", budget_inherit=True)
        assert mgr._resolve_child_budget(cfg) is None


class TestWaitAndCancel:
    async def test_wait_for_unknown_job_returns_stored_result(self):
        mgr = _manager()
        # No task, no result → None.
        assert await mgr.wait_for("never-spawned") is None

    async def test_wait_all_collects_every_result(self):
        mgr = _manager(ScriptedLLM(["multi result"]))
        mgr.register(SubAgentConfig(name="explore", max_turns=2))
        j1 = await mgr.spawn("explore", "t1", background=True)
        j2 = await mgr.spawn("explore", "t2", background=True)
        results = await mgr.wait_all()
        assert set(results) == {j1, j2}
        assert all(r.success for r in results.values())

    async def test_cancel_unknown_job_returns_false(self):
        assert await _manager().cancel("ghost") is False

    async def test_cancel_all_returns_zero_when_idle(self):
        assert await _manager().cancel_all() == 0


class TestCleanup:
    async def test_cleanup_removes_job_but_keeps_result(self):
        mgr = _manager(ScriptedLLM(["cleanup result"]))
        mgr.register(SubAgentConfig(name="explore", max_turns=2))
        job_id = await mgr.spawn("explore", "task", background=False)
        mgr.cleanup(job_id)
        assert job_id not in mgr._jobs
        assert job_id not in mgr._tasks
        # The result survives cleanup for later inspection.
        assert mgr.get_result(job_id) is not None

    async def test_cleanup_all_completed_counts_removed(self):
        mgr = _manager(ScriptedLLM(["x"]))
        mgr.register(SubAgentConfig(name="explore", max_turns=2))
        job_id = await mgr.spawn("explore", "task", background=False)
        # The task is done after a synchronous spawn.
        removed = mgr.cleanup_all_completed()
        assert removed == 1
        assert job_id not in mgr._tasks

    async def test_get_running_jobs_filters_to_subagents(self):
        mgr = _manager(ScriptedLLM(["x"]))
        mgr.register(SubAgentConfig(name="explore", max_turns=2))
        await mgr.spawn("explore", "task", background=False)
        # After completion there are no *running* subagent jobs.
        assert mgr.get_running_jobs() == []


class TestRunSubagentOutcomes:
    async def test_failing_subagent_marks_job_error(self):
        # An LLM that raises makes the SubAgent return a failed result;
        # _run_subagent records JobState.ERROR.
        class _BoomLLM:
            model = "boom"

            async def chat(self, messages, *, stream=True, **kwargs):
                raise RuntimeError("provider down")
                yield  # pragma: no cover

        mgr = SubAgentManager(Registry(), _BoomLLM())
        mgr.register(SubAgentConfig(name="explore", max_turns=2))
        job_id = await mgr.spawn("explore", "task", background=False)
        result = mgr.get_result(job_id)
        assert result.success is False
        assert mgr.get_status(job_id).state is JobState.ERROR

    async def test_post_subagent_run_hook_can_rewrite_result(self):
        # The parent's post_subagent_run hook may return a new
        # SubAgentResult — the manager must adopt it.
        from kohakuterrarium.modules.plugin.base import BasePlugin
        from kohakuterrarium.modules.plugin.manager import PluginManager
        from kohakuterrarium.modules.subagent.result import SubAgentResult

        class _RewritePlugin(BasePlugin):
            name = "rewriter"

            async def post_subagent_run(self, result, **kwargs):
                return SubAgentResult(output="REWRITTEN", success=True)

        pm = PluginManager()
        pm.register(_RewritePlugin())
        mgr = _manager(ScriptedLLM(["original output"]))
        mgr._parent_plugins = pm
        mgr.register(SubAgentConfig(name="explore", max_turns=2))
        job_id = await mgr.spawn("explore", "task", background=False)
        assert mgr.get_result(job_id).output == "REWRITTEN"

    async def test_cancelled_background_job_records_cancelled_state(self):
        import asyncio

        class _SlowLLM:
            model = "slow"

            async def chat(self, messages, *, stream=True, **kwargs):
                await asyncio.sleep(5)
                yield "late"

        mgr = SubAgentManager(Registry(), _SlowLLM())
        mgr.register(SubAgentConfig(name="explore", max_turns=2))
        job_id = await mgr.spawn("explore", "task", background=True)
        await asyncio.sleep(0.05)  # let it start
        assert await mgr.cancel(job_id) is True
        # Give the cancellation handler a tick to record state.
        await asyncio.sleep(0.05)
        assert mgr.get_status(job_id).state is JobState.CANCELLED

    async def test_tool_activity_forwarded_to_parent_callback(self):
        # The manager wires the sub-agent's on_tool_activity to the
        # parent's callback so tool events bubble up with the job id.
        forwarded: list[tuple] = []
        mgr = _manager(ScriptedLLM(["done"]))
        mgr._on_tool_activity = lambda *args: forwarded.append(args)
        mgr.register(SubAgentConfig(name="explore", max_turns=1))
        job_id = await mgr.spawn("explore", "task", background=False)
        # No tools ran here, but the forwarding callback is installed —
        # exercise it directly through the sub-agent handle.
        job = mgr._jobs.get(job_id)
        assert job is not None
        job.subagent.on_tool_activity("tool_start", "bash", "ls")
        assert forwarded[0][0] == "explore"  # subagent name prefixed
        assert forwarded[0][1] == "tool_start"

    async def test_wait_for_timeout_cancels_the_slow_job(self):
        import asyncio

        class _SlowLLM:
            model = "slow"

            async def chat(self, messages, *, stream=True, **kwargs):
                await asyncio.sleep(5)
                yield "late"

        mgr = SubAgentManager(Registry(), _SlowLLM())
        mgr.register(SubAgentConfig(name="explore", max_turns=2))
        job_id = await mgr.spawn("explore", "task", background=True)
        # A bounded wait against a slow job cancels it; the run surfaces
        # a cancelled SubAgentResult rather than hanging forever.
        result = await mgr.wait_for(job_id, timeout=0.1)
        assert result is not None
        assert result.success is False
        assert result.cancelled is True

    async def test_run_subagent_exception_handler_records_error(self):
        # job.run() itself raising (not the LLM, but the job wrapper) is
        # caught by _run_subagent's generic handler → failed result +
        # ERROR job status.
        from kohakuterrarium.core.job import JobStatus, JobType
        from kohakuterrarium.modules.subagent.result import SubAgentJob

        mgr = _manager(ScriptedLLM(["x"]))
        mgr.register(SubAgentConfig(name="explore", max_turns=1))

        class _BoomJob(SubAgentJob):
            async def run(self, task):
                raise RuntimeError("job wrapper exploded")

        # Spawn a normal one first to get a real SubAgent instance.
        normal_id = await mgr.spawn("explore", "t", background=False)
        normal_job = mgr._jobs[normal_id]
        job_id = "manual-job"
        # Register the job status so the handler's update_status lands.
        mgr.job_store.register(
            JobStatus(job_id=job_id, job_type=JobType.SUBAGENT, type_name="explore")
        )
        boom = _BoomJob(normal_job.subagent, job_id)
        result = await mgr._run_subagent(job_id, boom, "task")
        assert result.success is False
        assert "job wrapper exploded" in result.error
        assert mgr.get_status(job_id).state is JobState.ERROR

    async def test_wait_all_timeout_returns_partial_results(self):
        import asyncio

        class _SlowLLM:
            model = "slow"

            async def chat(self, messages, *, stream=True, **kwargs):
                await asyncio.sleep(5)
                yield "late"

        mgr = SubAgentManager(Registry(), _SlowLLM())
        mgr.register(SubAgentConfig(name="explore", max_turns=2))
        j1 = await mgr.spawn("explore", "t1", background=True)
        # wait_all with a tiny timeout returns a result entry per job
        # (the timeout fallback), not a hang.
        results = await mgr.wait_all(timeout=0.1)
        assert j1 in results
        # Clean up the lingering task.
        await mgr.cancel(j1)

    async def test_wait_all_empty_when_no_tasks(self):
        assert await _manager().wait_all() == {}

    async def test_interrupted_subagent_result_marks_job_cancelled(self):
        # A sub-agent that returns an interrupted SubAgentResult (not a
        # raised CancelledError) → _run_subagent records CANCELLED.
        class _SelfInterruptLLM:
            model = "x"

            def __init__(self):
                self._sa_holder = []

            async def chat(self, messages, *, stream=True, **kwargs):
                yield "partial "
                # Cancel the sub-agent mid-stream so run() returns an
                # interrupted result rather than raising.
                if self._sa_holder:
                    self._sa_holder[0].cancel()
                yield "more"

        llm = _SelfInterruptLLM()
        mgr = SubAgentManager(Registry(), llm)
        mgr.register(SubAgentConfig(name="explore", max_turns=3))
        job_id = await mgr.spawn("explore", "task", background=True)
        # Wire the sub-agent into the LLM so it can self-cancel.
        llm._sa_holder.append(mgr._jobs[job_id].subagent)
        result = await mgr.wait_for(job_id)
        assert result.interrupted is True
        assert mgr.get_status(job_id).state is JobState.CANCELLED

    async def test_cancel_all_cancels_running_tasks(self):
        import asyncio

        class _SlowLLM:
            model = "slow"

            async def chat(self, messages, *, stream=True, **kwargs):
                await asyncio.sleep(5)
                yield "late"

        mgr = SubAgentManager(Registry(), _SlowLLM())
        mgr.register(SubAgentConfig(name="explore", max_turns=2))
        await mgr.spawn("explore", "t1", background=True)
        await mgr.spawn("explore", "t2", background=True)
        await asyncio.sleep(0.05)
        cancelled = await mgr.cancel_all()
        assert cancelled == 2
