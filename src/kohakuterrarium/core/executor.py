"""
Background executor for tools and sub-agents.

Manages async execution of tools without blocking the controller.
"""

import asyncio
from pathlib import Path
from typing import Any, Callable

from kohakuterrarium.core.events import TriggerEvent, create_tool_complete_event
from kohakuterrarium.core.job import (
    JobResult,
    JobState,
    JobStatus,
    JobStore,
    JobType,
    generate_job_id,
)
from kohakuterrarium.core.tool_output import normalize_tool_output
from kohakuterrarium.modules.tool.base import BaseTool, Tool, ToolContext
from kohakuterrarium.parsing.events import ToolCallEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class Executor:
    """
    Background executor for tools.

    Manages tool execution in background tasks and tracks job status.

    Usage:
        executor = Executor()
        executor.register_tool(BashTool())

        # Submit from parse event
        job_id = await executor.submit_from_event(tool_call_event)

        # Or submit directly
        job_id = await executor.submit("bash", {"command": "ls"})

        # Wait for result
        result = await executor.wait_for(job_id)

        # Or get events when jobs complete
        async for event in executor.events():
            handle_completion(event)
    """

    def __init__(
        self,
        job_store: JobStore | None = None,
        on_complete: Callable[[TriggerEvent], Any] | None = None,
    ):
        """
        Initialize executor.

        Args:
            job_store: Store for job statuses (creates new if None)
            on_complete: Callback when jobs complete
        """
        self.job_store = job_store or JobStore()
        self._tools: dict[str, Tool] = {}
        self._tasks: dict[str, asyncio.Task[JobResult]] = {}
        self._results: dict[str, JobResult] = {}
        self._on_complete = on_complete
        self._event_queue: asyncio.Queue[TriggerEvent] = asyncio.Queue()

        # Shared serial lock for tools with ``is_concurrency_safe = False``
        # (Cluster 5 / G.1 of the extension-point decisions). Tools that
        # mutate shared state — file writes, destructive shell commands —
        # acquire this lock before running so at most one unsafe tool
        # executes at a time. Safe tools skip the lock entirely and keep
        # running in parallel. Created lazily inside ``_run_tool`` so the
        # executor can be constructed outside a running event loop.
        self._serial_lock: asyncio.Lock | None = None

        # Context for tools (set by agent during init)
        self._agent_name: str = ""
        self._tool_format: str = "native"
        self._agent: Any = None  # Agent instance, set during init
        self._session: Any = None  # Session, set by agent during init
        self._environment: Any = None  # Environment, set by agent during init
        self._working_dir: Path = Path.cwd()
        self._memory_path: Path | None = None
        self._file_read_state: Any = None  # FileReadState, set by agent
        self._path_guard: Any = None  # PathBoundaryGuard, set by agent

    def register_tool(self, tool: Tool) -> None:
        """Register a tool for execution."""
        self._tools[tool.tool_name] = tool
        logger.debug("Registered tool", tool_name=tool.tool_name)

    def _emit_tool_wait(self, tool_name: str, wait_ms: float, reason: str) -> None:
        """Emit a Wave B ``tool_wait`` activity to the agent's output router.

        Fires only when a tool call blocked on a concurrency-unsafe lock
        so viewers (session store, Studio) can surface serialization
        hotspots. Missing agent / router is tolerated — this is pure
        observability.
        """
        agent = self._agent
        if agent is None:
            return
        router = getattr(agent, "output_router", None)
        if router is None:
            return
        try:
            router.notify_activity(
                "tool_wait",
                f"[{tool_name}] waited {wait_ms:.1f}ms on {reason}",
                metadata={
                    "tool": tool_name,
                    "wait_ms": wait_ms,
                    "reason": reason,
                },
            )
        except Exception as e:  # pragma: no cover — pure observability
            logger.debug("tool_wait emit failed", error=str(e), exc_info=True)

    def _wrap_tool_execute(
        self,
        tool: Tool,
        args: dict[str, Any],
        *,
        job_id: str,
        context: ToolContext | None = None,
    ) -> Callable[..., Any]:
        """Return ``tool.execute`` wrapped with the agent's pre/post hooks.

        Returns the original ``tool.execute`` unchanged when the agent
        has no plugin manager or no plugin overrides those hooks
        (``PluginManager.wrap_method`` short-circuits in that case).

        Importantly the wrapper is a fresh closure — we do NOT assign
        it back onto the tool. Sub-agents share tool instances with
        the parent through ``parent_registry``; rebinding ``execute``
        would let one agent's plugin chain (e.g. its budget plugin)
        intercept another agent's tool calls.
        """
        if context is None:
            context = self._build_tool_context()
        agent = self._agent
        plugins = getattr(agent, "plugins", None) if agent is not None else None
        if plugins is None:
            return tool.execute
        return plugins.wrap_method(
            "pre_tool_execute",
            "post_tool_execute",
            tool.execute,
            input_kwarg="args",
            extra_kwargs={
                "tool_name": tool.tool_name,
                "job_id": job_id,
                "context": context,
            },
        )

    def get_tool(self, tool_name: str) -> Tool | None:
        """Get a registered tool by name."""
        return self._tools.get(tool_name)

    def list_tools(self) -> list[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    async def submit(
        self,
        tool_name: str,
        args: dict[str, Any],
        job_id: str | None = None,
        is_direct: bool = False,
    ) -> str:
        """
        Submit a tool for execution.

        Args:
            tool_name: Name of the tool to execute
            args: Arguments for the tool
            job_id: Optional job ID (generated if not provided)
            is_direct: If True, skip _on_complete callback and event queue
                       (direct tools are awaited by the processing loop)

        Returns:
            Job ID

        Raises:
            ValueError: If tool not registered
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ValueError(f"Tool not registered: {tool_name}")

        # Generate job ID
        if job_id is None:
            job_id = generate_job_id(tool_name)

        # Create job status
        status = JobStatus(
            job_id=job_id,
            job_type=JobType.TOOL,
            type_name=tool_name,
            state=JobState.RUNNING,
        )
        self.job_store.register(status)

        # Start task
        task = asyncio.create_task(self._run_tool(job_id, tool, args, is_direct))
        self._tasks[job_id] = task

        logger.info("Running tool: %s", tool_name)
        logger.debug("Tool job submitted", job_id=job_id, tool_name=tool_name)
        return job_id

    async def submit_from_event(
        self, event: ToolCallEvent, is_direct: bool = False
    ) -> str:
        """
        Submit a tool from a ToolCallEvent.

        Args:
            event: Parsed tool call event
            is_direct: If True, skip completion callback (awaited by loop)

        Returns:
            Job ID
        """
        return await self.submit(event.name, event.args, is_direct=is_direct)

    async def _run_tool(
        self,
        job_id: str,
        tool: Tool,
        args: dict[str, Any],
        is_direct: bool = False,
    ) -> JobResult:
        """Run a tool and update status."""
        try:
            # Check require_manual_read: block if manual not read yet
            if (
                isinstance(tool, BaseTool)
                and tool.require_manual_read
                and not tool._manual_read
            ):
                error_msg = f"Call info(name={tool.tool_name}) first to read tool docs"
                output_msg = (
                    f"Tool '{tool.tool_name}' requires reading its documentation "
                    f"before first use. Call: info(name={tool.tool_name})\n"
                    f"This is NOT about reading a file. Use the 'info' tool to "
                    f"load the tool's usage manual, then retry your call."
                )
                self.job_store.update_status(
                    job_id,
                    state=JobState.ERROR,
                    error=error_msg,
                )
                job_result = JobResult(
                    job_id=job_id,
                    output=output_msg,
                    exit_code=1,
                    error=error_msg,
                )
                self._results[job_id] = job_result
                return job_result

            # Build one context for plugin hooks and context-aware tools.
            context = self._build_tool_context()

            # Plugin hooks fire at the call site — wrap tool.execute
            # locally with the AGENT's plugin manager so each agent's
            # plugin chain only sees that agent's tool calls. Tool
            # instances are shared between the parent's registry and
            # every sub-agent's ``parent_registry`` reference, so we
            # must NOT mutate ``tool.execute``; the wrapper here is a
            # closure over the original.
            exec_fn = self._wrap_tool_execute(
                tool, args, job_id=job_id, context=context
            )

            # Concurrency-safety partition (Cluster 5 / G.1):
            # unsafe tools acquire the shared serial lock so at most
            # one unsafe tool runs at a time. Safe tools skip the lock
            # entirely and remain fully parallel.
            needs_lock = isinstance(tool, BaseTool) and not tool.is_concurrency_safe
            if needs_lock:
                if self._serial_lock is None:
                    self._serial_lock = asyncio.Lock()
                wait_start = asyncio.get_event_loop().time()
                async with self._serial_lock:
                    # Wave B additive ``tool_wait`` event: emit only when
                    # the caller actually blocked (>1ms). Keeps noise
                    # down for the common uncontended case.
                    wait_ms = (asyncio.get_event_loop().time() - wait_start) * 1000.0
                    if wait_ms >= 1.0:
                        self._emit_tool_wait(tool.tool_name, wait_ms, "serial_lock")
                    result = await exec_fn(args, context=context)
            else:
                result = await exec_fn(args, context=context)

            max_output = tool.config.max_output if isinstance(tool, BaseTool) else 0
            artifact_store = getattr(self._agent, "session_store", None)
            normalized = normalize_tool_output(
                result.output,
                max_output=max_output,
                job_id=job_id,
                tool_name=tool.tool_name,
                artifact_store=artifact_store,
            )
            metadata = dict(result.metadata or {})
            metadata.update(normalized.metadata)

            # Create job result from centrally normalized output.
            job_result = JobResult(
                job_id=job_id,
                output=normalized.output,
                exit_code=result.exit_code,
                error=result.error,
                metadata=metadata,
            )

            # Update status from normalized text stats.
            self.job_store.update_status(
                job_id,
                state=JobState.DONE if result.success else JobState.ERROR,
                output_lines=normalized.stats.lines,
                output_bytes=normalized.stats.bytes,
                preview=normalized.stats.preview,
                error=result.error,
            )
            self.job_store.store_result(job_result)
            self._results[job_id] = job_result

            status = "done" if result.success else "failed"
            logger.info("Tool %s: %s", tool.tool_name, status)
            logger.debug("Tool job completed", job_id=job_id, success=result.success)

            # For background tools: fire completion callback and queue event
            # Direct tools are awaited by the processing loop - no callback needed
            if not is_direct:
                event = create_tool_complete_event(
                    job_id=job_id,
                    content=normalized.output if normalized.output else "",
                    exit_code=result.exit_code,
                    error=result.error,
                )
                if self._on_complete:
                    self._on_complete(event)
                await self._event_queue.put(event)

            return job_result

        except asyncio.CancelledError:
            error_msg = "User manually interrupted this job."
            logger.info("Tool cancelled by user", job_id=job_id)

            self.job_store.update_status(
                job_id,
                state=JobState.CANCELLED,
                error=error_msg,
            )

            job_result = JobResult(job_id=job_id, error=error_msg)
            self.job_store.store_result(job_result)
            self._results[job_id] = job_result

            if not is_direct:
                event = create_tool_complete_event(
                    job_id=job_id,
                    content="",
                    error=error_msg,
                )
                if self._on_complete:
                    self._on_complete(event)
                await self._event_queue.put(event)

            return job_result

        except Exception as e:
            logger.error("Tool execution failed", job_id=job_id, error=str(e))

            # Update status with error
            self.job_store.update_status(
                job_id,
                state=JobState.ERROR,
                error=str(e),
            )

            job_result = JobResult(job_id=job_id, error=str(e))
            self.job_store.store_result(job_result)
            self._results[job_id] = job_result

            if not is_direct:
                event = create_tool_complete_event(
                    job_id=job_id,
                    content="",
                    error=str(e),
                )
                if self._on_complete:
                    self._on_complete(event)
                await self._event_queue.put(event)

            return job_result

    def _build_tool_context(self) -> ToolContext:
        """Build ToolContext for context-aware tools."""
        context = ToolContext(
            agent_name=self._agent_name,
            session=self._session,
            working_dir=self._working_dir,
            memory_path=self._memory_path,
            environment=self._environment,
            tool_format=self._tool_format,
            agent=self._agent,
            file_read_state=self._file_read_state,
            path_guard=self._path_guard,
        )
        agent = self._agent
        plugins = getattr(agent, "plugins", None) if agent is not None else None
        if plugins is not None and hasattr(plugins, "collect_runtime_services"):
            context.runtime_services.update(plugins.collect_runtime_services(context))
        return context

    async def wait_for(
        self,
        job_id: str,
        timeout: float | None = None,
    ) -> JobResult | None:
        """
        Wait for a job to complete.

        Args:
            job_id: Job ID to wait for
            timeout: Maximum wait time in seconds

        Returns:
            JobResult if completed, None if timeout or not found
        """
        task = self._tasks.get(job_id)
        if task is None:
            # Check if already completed
            return self._results.get(job_id)

        try:
            return await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Wait timed out", job_id=job_id)
            return None

    async def wait_all(
        self,
        timeout: float | None = None,
    ) -> dict[str, JobResult]:
        """
        Wait for all pending jobs to complete.

        Args:
            timeout: Maximum total wait time

        Returns:
            Dict of job_id -> JobResult
        """
        if not self._tasks:
            return {}

        tasks = list(self._tasks.values())
        job_ids = list(self._tasks.keys())

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout,
            )

            return {
                job_id: result
                for job_id, result in zip(job_ids, results)
                if isinstance(result, JobResult)
            }
        except asyncio.TimeoutError:
            logger.warning("Wait all timed out")
            return {
                job_id: self._results[job_id]
                for job_id in job_ids
                if job_id in self._results
            }

    async def cancel(self, job_id: str) -> bool:
        """
        Cancel a running job.

        Args:
            job_id: Job to cancel

        Returns:
            True if cancelled, False if not found or already done
        """
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False

        task.cancel()
        self.job_store.update_status(job_id, state=JobState.CANCELLED)
        logger.debug("Cancelled job", job_id=job_id)
        return True

    def get_status(self, job_id: str) -> JobStatus | None:
        """Get job status."""
        return self.job_store.get_status(job_id)

    def get_result(self, job_id: str) -> JobResult | None:
        """Get job result (if completed)."""
        return self._results.get(job_id) or self.job_store.get_result(job_id)

    def get_task(self, job_id: str) -> asyncio.Task | None:
        """
        Get the asyncio.Task for a job by ID.

        Args:
            job_id: Job ID to look up

        Returns:
            The asyncio.Task if found, None otherwise
        """
        return self._tasks.get(job_id)

    def get_pending_count(self) -> int:
        """
        Get the number of pending (not yet completed) tasks.

        Returns:
            Number of tasks still tracked by the executor
        """
        return len(self._tasks)

    def get_running_jobs(self) -> list[JobStatus]:
        """Get all running jobs."""
        return self.job_store.get_running_jobs()

    async def events(self) -> TriggerEvent:
        """
        Async generator for completion events.

        Yields TriggerEvents when jobs complete.
        """
        while True:
            event = await self._event_queue.get()
            yield event

    def get_next_event_nowait(self) -> TriggerEvent | None:
        """Get next completion event without waiting."""
        try:
            return self._event_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def get_next_event(self, timeout: float | None = None) -> TriggerEvent | None:
        """Get next completion event with optional timeout."""
        try:
            if timeout:
                return await asyncio.wait_for(self._event_queue.get(), timeout)
            return await self._event_queue.get()
        except asyncio.TimeoutError:
            return None
