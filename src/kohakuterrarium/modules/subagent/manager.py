"""
Sub-agent manager.

Handles sub-agent lifecycle, spawning, and status tracking.
Supports both regular (stateless) and interactive (long-lived) sub-agents.
"""

import asyncio
from pathlib import Path
from typing import Any, Callable

from kohakuterrarium.core.budget import IterationBudget
from kohakuterrarium.core.job import (
    JobState,
    JobStatus,
    JobStore,
    JobType,
    generate_job_id,
)
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.modules.subagent.base import SubAgent, SubAgentJob, SubAgentResult
from kohakuterrarium.modules.subagent.config import SubAgentConfig, SubAgentInfo
from kohakuterrarium.modules.subagent.runtime_builders import (
    build_compact_manager,
    build_plugin_manager,
    load_and_wrap_plugins,
    resolve_llm,
)
from kohakuterrarium.modules.subagent.interactive import (
    InteractiveOutput,
    InteractiveSubAgent,
)
from kohakuterrarium.modules.subagent.interactive_mgr import InteractiveManagerMixin
from kohakuterrarium.parsing.events import SubAgentCallEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SubAgentManager(InteractiveManagerMixin):
    """
    Manages sub-agent lifecycle and execution.

    Responsibilities:
    - Register sub-agent configurations
    - Spawn sub-agents on demand
    - Track running sub-agents
    - Handle results and cleanup

    Usage:
        manager = SubAgentManager(registry, llm, job_store)
        manager.register(SubAgentConfig(name="explore", ...))

        # Spawn and run
        job_id = await manager.spawn("explore", "Find auth code")
        result = await manager.wait_for(job_id)

        # Or spawn from event
        job_id = await manager.spawn_from_event(subagent_call_event)
    """

    def __init__(
        self,
        parent_registry: Registry,
        llm: LLMProvider,
        job_store: JobStore | None = None,
        agent_path: Path | None = None,
        current_depth: int = 0,
        max_depth: int = 3,
        tool_format: str | None = None,
        default_plugin_specs: list[dict[str, Any]] | None = None,
    ):
        """
        Initialize sub-agent manager.

        Args:
            parent_registry: Parent's registry for tool access
            llm: LLM provider for sub-agents
            job_store: Store for job status tracking
            agent_path: Path to agent folder for prompt loading
            current_depth: Current nesting depth of this agent
            max_depth: Maximum allowed sub-agent depth (0 = unlimited)
            tool_format: Parent's tool_format (inherited by sub-agents)
        """
        self.parent_registry = parent_registry
        self.llm = llm
        self.job_store = job_store or JobStore()
        self.agent_path = agent_path
        self._current_depth: int = current_depth
        self._max_depth: int = max_depth
        self._tool_format: str | None = tool_format
        self._default_plugin_specs = default_plugin_specs or []
        self._loader = ModuleLoader(agent_path=agent_path)

        # Completion callback (wired by agent to deliver results as events)
        self._on_complete: Callable[[Any], None] | None = None
        # Callback: (subagent_name, activity_type, tool_name, detail) -> None
        self._on_tool_activity: Callable[[str, str, str, str], None] | None = None
        # Parent executor (for inheriting tool context builder)
        self._parent_executor: Any = None
        # Parent's shared ``IterationBudget`` (legacy single-axis shim
        # tied to ``max_iterations``). The runtime ``budget`` plugin owns
        # its own state and is not propagated through the manager.
        self.iteration_budget: IterationBudget | None = None
        # Session store for persisting sub-agent conversations
        self._session_store: Any = None
        self._parent_name: str = ""

        # Registered sub-agent configs
        self._configs: dict[str, SubAgentConfig] = {}

        # Running sub-agent jobs (for stateless sub-agents)
        self._jobs: dict[str, SubAgentJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._results: dict[str, SubAgentResult] = {}

        # Interactive sub-agents (long-lived)
        self._interactive: dict[str, InteractiveSubAgent] = {}
        self._output_callbacks: dict[str, Callable[[InteractiveOutput], None]] = {}

    def _resolve_child_budget(self, config: SubAgentConfig) -> IterationBudget | None:
        """Decide which IterationBudget a new child should run under.

        Precedence:
        1. ``config.budget_allocation`` is non-None → fresh isolated budget.
        2. ``config.budget_inherit`` is True and parent has one → reuse it.
        3. Otherwise → ``None`` (no budget enforcement).
        """
        allocation = config.budget_allocation
        if allocation is not None:
            return IterationBudget(remaining=int(allocation), total=int(allocation))
        if config.budget_inherit and self.iteration_budget is not None:
            return self.iteration_budget
        return None

    def register(self, config: SubAgentConfig) -> None:
        """
        Register a sub-agent configuration.

        Validates that all tools in the sub-agent config exist in the parent
        registry. Missing tools are logged as warnings (not errors) because
        the parent agent may add tools after registration.

        Args:
            config: Sub-agent configuration
        """
        # Validate tool availability - warn early about missing tools
        missing_tools = [
            t for t in config.tools if self.parent_registry.get_tool(t) is None
        ]
        if missing_tools:
            logger.warning(
                "Sub-agent references tools not in parent registry",
                subagent_name=config.name,
                missing_tools=missing_tools,
            )

        self._configs[config.name] = config
        logger.debug(
            "Registered sub-agent",
            subagent_name=config.name,
            tools=config.tools,
        )

    def get_config(self, name: str) -> SubAgentConfig | None:
        """Get sub-agent config by name."""
        return self._configs.get(name)

    def list_subagents(self) -> list[str]:
        """List registered sub-agent names."""
        return list(self._configs.keys())

    def get_subagent_info(self, name: str) -> SubAgentInfo | None:
        """Get sub-agent info for system prompt."""
        config = self._configs.get(name)
        if config:
            return SubAgentInfo.from_config(config)
        return None

    def get_subagents_prompt(self) -> str:
        """Generate sub-agents section for system prompt."""
        if not self._configs:
            return ""

        lines = ["## Available Sub-Agents", ""]
        for name, config in self._configs.items():
            info = SubAgentInfo.from_config(config)
            lines.append(info.to_prompt_line())

        lines.append("")
        if self._tool_format == "native":
            lines.append("Sub-agents are called as tools via the API (param: `task`).")
        else:
            lines.append("Call sub-agents like tools with a task description.")

        return "\n".join(lines)

    async def spawn(
        self,
        name: str,
        task: str,
        job_id: str | None = None,
        background: bool = True,
    ) -> str:
        """
        Spawn a sub-agent to execute a task.

        Args:
            name: Sub-agent name
            task: Task description
            job_id: Optional job ID (generated if not provided)
            background: If True (default), run as background task.
                If False, run synchronously and return job_id after completion.

        Returns:
            Job ID

        Raises:
            ValueError: If sub-agent not registered
        """
        config = self._configs.get(name)
        if config is None:
            raise ValueError(f"Sub-agent not registered: {name}")

        # Check depth limit before spawning
        if self._max_depth > 0 and self._current_depth >= self._max_depth:
            error_msg = (
                f"Sub-agent depth limit reached ({self._current_depth}/{self._max_depth}). "
                f"Cannot spawn '{name}'. Simplify your approach or use tools directly."
            )
            logger.warning(
                "Sub-agent depth limit reached",
                subagent_name=name,
                current_depth=self._current_depth,
                max_depth=self._max_depth,
            )
            # Generate job ID and store error result
            if job_id is None:
                job_id = generate_job_id(f"agent_{name}")

            error_result = SubAgentResult(success=False, error=error_msg)
            self._results[job_id] = error_result

            status = JobStatus(
                job_id=job_id,
                job_type=JobType.SUBAGENT,
                type_name=name,
                state=JobState.ERROR,
                error=error_msg,
            )
            self.job_store.register(status)

            return job_id

        # Generate job ID
        if job_id is None:
            job_id = generate_job_id(f"agent_{name}")

        # Resolve tool_format: config override > parent inherited
        effective_tool_format = config.tool_format or self._tool_format

        plugin_manager = build_plugin_manager(
            config, self._loader, self._default_plugin_specs
        )
        llm = resolve_llm(self.llm, config)
        compact_manager = build_compact_manager(config, llm)

        # Create sub-agent
        subagent = SubAgent(
            config=config,
            parent_registry=self.parent_registry,
            llm=llm,
            agent_path=self.agent_path,
            tool_format=effective_tool_format,
            plugin_manager=plugin_manager,
            compact_manager=compact_manager,
        )

        # Legacy shared iteration budget remains as a compat fallback.
        subagent.iteration_budget = self._resolve_child_budget(config)
        await load_and_wrap_plugins(plugin_manager, subagent, llm, self.agent_path)

        # Forward sub-agent tool activity to parent's callback
        if self._on_tool_activity:
            sa_name = name
            sa_job_id = job_id
            parent_cb = self._on_tool_activity

            def _forward_activity(activity_type, tool_name, detail, extra=None):
                parent_cb(sa_name, activity_type, tool_name, detail, sa_job_id, extra)

            subagent.on_tool_activity = _forward_activity

        # Inherit parent's tool context builder (working_dir, file guards, etc.)
        if self._parent_executor:
            subagent._build_tool_context = self._parent_executor._build_tool_context

        # Pass session store for conversation persistence
        if self._session_store:
            subagent._session_store = self._session_store
            subagent._parent_name = self._parent_name
            subagent._run_index = self._session_store.next_subagent_run(
                self._parent_name, name
            )

        # Create job wrapper
        job = SubAgentJob(subagent, job_id)
        self._jobs[job_id] = job

        # Register job status
        status = JobStatus(
            job_id=job_id,
            job_type=JobType.SUBAGENT,
            type_name=name,
            state=JobState.RUNNING,
        )
        self.job_store.register(status)

        # Create asyncio task — caller decides whether to wait (direct) or not
        task_obj = asyncio.create_task(self._run_subagent(job_id, job, task))
        self._tasks[job_id] = task_obj

        if not background:
            # Programmatic API: wait for completion before returning
            await task_obj

        logger.info(
            "Spawned sub-agent",
            subagent_name=name,
            job_id=job_id,
            background=background,
        )

        return job_id

    async def spawn_from_event(self, event: SubAgentCallEvent) -> tuple[str, bool]:
        """Spawn sub-agent from a parsed event.

        Always starts the sub-agent as a background asyncio task so the
        caller can decide whether to wait (direct) or not (background).

        Returns:
            (job_id, is_background) — is_background reflects the model's intent
        """
        task = event.args.get("task", event.args.get("content", ""))
        is_background = event.args.pop("run_in_background", True)
        # Always spawn as background task — caller handles waiting for direct
        job_id = await self.spawn(event.name, task, background=True)
        return job_id, is_background

    async def _run_subagent(
        self,
        job_id: str,
        job: SubAgentJob,
        task: str,
    ) -> SubAgentResult:
        """Run sub-agent and update status."""
        try:
            result = await job.run(task)
            self._results[job_id] = result

            if result.interrupted or result.cancelled:
                state = JobState.CANCELLED
            elif result.success:
                state = JobState.DONE
            else:
                state = JobState.ERROR
            self.job_store.update_status(
                job_id,
                state=state,
                output_lines=result.output.count("\n") + 1 if result.output else 0,
                output_bytes=len(result.output),
                preview=result.output[:200] if result.output else "",
                error=result.error,
            )

            job_result = job.to_job_result()
            if job_result:
                self.job_store.store_result(job_result)

            logger.info(
                "Sub-agent completed",
                job_id=job_id,
                success=result.success,
                turns=result.turns,
            )
            return result

        except asyncio.CancelledError:
            error_msg = "Background sub-agent was cancelled by user."
            logger.info("Sub-agent cancelled by user", job_id=job_id)
            current = getattr(job.subagent, "_build_partial_result", None)
            if callable(current):
                result = current(error_msg, cancelled=True)
            else:
                result = SubAgentResult(success=False, error=error_msg, cancelled=True)
            self._results[job_id] = result
            self.job_store.update_status(
                job_id,
                state=JobState.CANCELLED,
                error=error_msg,
            )
            return result

        except Exception as e:
            logger.error("Sub-agent failed", job_id=job_id, error=str(e))
            current = getattr(job.subagent, "_build_partial_result", None)
            if callable(current):
                result = current(str(e))
            else:
                result = SubAgentResult(success=False, error=str(e))
            self._results[job_id] = result
            self.job_store.update_status(
                job_id,
                state=JobState.ERROR,
                error=str(e),
            )
            return result

        finally:
            if hasattr(job.subagent, "plugins") and job.subagent.plugins:
                await job.subagent.plugins.unload_all()

    async def wait_for(
        self,
        job_id: str,
        timeout: float | None = None,
    ) -> SubAgentResult | None:
        """
        Wait for a sub-agent to complete.

        Args:
            job_id: Job ID to wait for
            timeout: Maximum wait time

        Returns:
            SubAgentResult if completed, None if timeout
        """
        task = self._tasks.get(job_id)
        if task is None:
            return self._results.get(job_id)

        try:
            return await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Wait timed out", job_id=job_id)
            return None

    async def wait_all(
        self,
        timeout: float | None = None,
    ) -> dict[str, SubAgentResult]:
        """
        Wait for all running sub-agents.

        Args:
            timeout: Maximum total wait time

        Returns:
            Dict of job_id -> SubAgentResult
        """
        if not self._tasks:
            return {}

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*self._tasks.values(), return_exceptions=True),
                timeout=timeout,
            )

            return {
                job_id: (
                    result
                    if isinstance(result, SubAgentResult)
                    else SubAgentResult(error=str(result))
                )
                for job_id, result in zip(self._tasks.keys(), results)
            }
        except asyncio.TimeoutError:
            return {
                job_id: self._results.get(job_id, SubAgentResult(error="Timeout"))
                for job_id in self._tasks.keys()
            }

    async def cancel_all(self) -> int:
        """Cancel all running sub-agent tasks."""
        cancelled = 0
        for job_id, task in list(self._tasks.items()):
            if not task.done():
                job = self._jobs.get(job_id)
                if job and hasattr(job, "subagent"):
                    job.subagent.cancel()
                task.cancel()
                cancelled += 1
        # Also stop all interactive sub-agents
        await self.stop_all_interactive()
        return cancelled

    async def cancel(self, job_id: str) -> bool:
        """
        Cancel a running sub-agent.

        Sets the cancelled flag on the SubAgent so its run loop exits
        at the next checkpoint, then also cancels the asyncio task as
        a fallback.

        Args:
            job_id: Job to cancel

        Returns:
            True if cancelled, False if not found or already done
        """
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False

        # Set the cancel flag on the SubAgent instance so the run loop
        # breaks at the next turn boundary (cooperative cancellation)
        job = self._jobs.get(job_id)
        if job and hasattr(job, "subagent"):
            job.subagent.cancel()

        # Also cancel the asyncio task as a fallback
        task.cancel()
        logger.debug("Cancelled sub-agent", job_id=job_id)
        return True

    def get_status(self, job_id: str) -> JobStatus | None:
        """Get sub-agent job status."""
        return self.job_store.get_status(job_id)

    def get_result(self, job_id: str) -> SubAgentResult | None:
        """Get sub-agent result (if completed)."""
        return self._results.get(job_id)

    def get_running_jobs(self) -> list[JobStatus]:
        """Get all running sub-agent jobs."""
        return [
            status
            for status in self.job_store.get_running_jobs()
            if status.job_type == JobType.SUBAGENT
        ]

    def cleanup(self, job_id: str) -> None:
        """
        Cleanup a completed sub-agent job.

        Args:
            job_id: Job to cleanup
        """
        self._jobs.pop(job_id, None)
        self._tasks.pop(job_id, None)
        # Keep result for potential later access
        logger.debug("Cleaned up sub-agent", job_id=job_id)

    def cleanup_all_completed(self) -> int:
        """
        Cleanup all completed sub-agent jobs.

        Returns:
            Number of jobs cleaned up
        """
        completed = [job_id for job_id, task in self._tasks.items() if task.done()]

        for job_id in completed:
            self.cleanup(job_id)

        return len(completed)
