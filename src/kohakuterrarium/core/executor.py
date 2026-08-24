"""Asynchronous tool execution, status tracking, and completion events."""

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
from kohakuterrarium.core.tool_output import (
    discard_raw_output_file,
    normalize_tool_output,
)
from kohakuterrarium.modules.tool.base import BaseTool, Tool, ToolContext, ToolResult
from kohakuterrarium.parsing.events import ToolCallEvent
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.mobile_sandbox import default_workdir
from kohakuterrarium.utils.timeouts import resolve_timeout_arg

logger = get_logger(__name__)


class Executor:
    """Run tools in background tasks and expose their lifecycle as jobs."""

    def __init__(
        self,
        job_store: JobStore | None = None,
        on_complete: Callable[[TriggerEvent], Any] | None = None,
    ):
        """Initialize execution state with an optional shared job store."""
        self.job_store = job_store or JobStore()
        self._tools: dict[str, Tool] = {}
        self._tasks: dict[str, asyncio.Task[JobResult]] = {}
        self._on_complete = on_complete
        self._event_queue: asyncio.Queue[TriggerEvent] = asyncio.Queue()

        # Unsafe tools share one lock to protect mutable resources while safe
        # tools remain parallel. Lazy creation permits construction off-loop.
        self._serial_lock: asyncio.Lock | None = None

        self._agent_name: str = ""
        self._creature_id: str = ""
        self._tool_format: str = "native"
        self._agent: Any = None  # Agent instance, set during init
        self._session: Any = None  # Session, set by agent during init
        self._environment: Any = None  # Environment, set by agent during init
        # Mobile packaging may start in an unwritable root directory, so tools
        # need a platform-safe default before the agent supplies its workdir.
        self._working_dir: Path = default_workdir()
        self._memory_path: Path | None = None
        self._file_read_state: Any = None  # FileReadState, set by agent
        self._path_guard: Any = None  # PathBoundaryGuard, set by agent

    def register_tool(self, tool: Tool) -> None:
        """Register a tool for execution."""
        self._tools[tool.tool_name] = tool
        logger.debug("Registered tool", tool_name=tool.tool_name)

    def _emit_tool_wait(self, tool_name: str, wait_ms: float, reason: str) -> None:
        """Emit lock-wait observability without affecting tool execution."""
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
        except Exception as e:  # pragma: no cover - telemetry must not fail tools
            logger.warning("tool_wait emit failed", error=str(e), exc_info=True)

    def _wrap_tool_execute(
        self,
        tool: Tool,
        args: dict[str, Any],
        *,
        job_id: str,
        context: ToolContext | None = None,
    ) -> Callable[..., Any]:
        """Wrap execution with this agent's plugin hooks without rebinding the tool.

        Tool instances may be shared with sub-agents, so mutating ``execute`` would
        leak one agent's policy chain into another agent's calls.
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

        if job_id is None:
            job_id = generate_job_id(tool_name)

        status = JobStatus(
            job_id=job_id,
            job_type=JobType.TOOL,
            type_name=tool_name,
            state=JobState.RUNNING,
        )
        self.job_store.register(status)

        task = asyncio.create_task(self._run_tool(job_id, tool, args, is_direct))
        self._tasks[job_id] = task
        task.add_done_callback(
            lambda completed, jid=job_id, direct=is_direct: self._finalize_task(
                jid, completed, direct
            )
        )

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

    def _manual_read_gate_active(self) -> bool:
        """Return whether first-use documentation gating is actionable.

        Static prompts already contain full docs, and absence of the ``info`` tool
        would make a manual-read requirement impossible to satisfy.
        """
        agent = self._agent
        if agent is None:
            return True
        config = getattr(agent, "config", None)
        if getattr(config, "skill_mode", "dynamic") == "static":
            return False
        registry = getattr(agent, "registry", None)
        if registry is not None and registry.get_tool("info") is None:
            return False
        return True

    async def _run_bash(
        self,
        tool: Tool,
        args: dict[str, Any],
        exec_fn: Callable[..., Any],
        context: ToolContext,
        needs_lock: bool,
        allow_concurrent: bool,
    ) -> ToolResult:
        """Run Bash with one timeout budget shared by lock and subprocess."""
        timeout, timeout_error = resolve_timeout_arg(
            args, tool.config.timeout if isinstance(tool, BaseTool) else 60.0
        )
        if timeout_error is not None:
            return ToolResult(error=timeout_error)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout if timeout > 0 else None
        execute_args = dict(args)
        lock_acquired = False
        wait_start = loop.time()
        try:
            if needs_lock and not allow_concurrent:
                if self._serial_lock is None:
                    self._serial_lock = asyncio.Lock()
                remaining = deadline - loop.time() if deadline is not None else None
                try:
                    if remaining is None:
                        await self._serial_lock.acquire()
                    else:
                        await asyncio.wait_for(
                            self._serial_lock.acquire(), timeout=max(0, remaining)
                        )
                except asyncio.TimeoutError:
                    wait_ms = (loop.time() - wait_start) * 1000.0
                    self._emit_tool_wait(tool.tool_name, wait_ms, "serial_lock")
                    return ToolResult(
                        error="Concurrency lock timeout; command not started; "
                        "retry with allow_concurrent=true if safe.",
                        metadata={
                            "blocked": True,
                            "blocked_by": "concurrency_lock",
                            "command_started": False,
                        },
                    )
                lock_acquired = True
                wait_ms = (loop.time() - wait_start) * 1000.0
                if wait_ms >= 1.0:
                    self._emit_tool_wait(tool.tool_name, wait_ms, "serial_lock")

            remaining = deadline - loop.time() if deadline is not None else None
            if remaining is not None:
                if remaining <= 0:
                    return ToolResult(
                        error="Command timed out before it started.",
                        exit_code=-1,
                        metadata={"timed_out": True, "command_started": False},
                    )
                execute_args["timeout"] = remaining
            return await exec_fn(execute_args, context=context)
        finally:
            if lock_acquired:
                self._serial_lock.release()

    async def _run_tool(
        self,
        job_id: str,
        tool: Tool,
        args: dict[str, Any],
        is_direct: bool = False,
    ) -> JobResult:
        """Run a tool and update status."""
        try:
            if (
                isinstance(tool, BaseTool)
                and tool.require_manual_read
                and not tool._manual_read
                and self._manual_read_gate_active()
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
                self.job_store.store_result(job_result)
                return job_result

            context = self._build_tool_context()

            # Call-site wrapping keeps shared tool instances free of agent-specific
            # plugin state.
            exec_fn = self._wrap_tool_execute(
                tool, args, job_id=job_id, context=context
            )

            # Only tools that declare unsafe shared-state access are serialized.
            needs_lock = isinstance(tool, BaseTool) and not tool.is_concurrency_safe
            allow_concurrent = (
                str(args.get("allow_concurrent", "")).strip().lower() == "true"
            )
            if tool.tool_name == "bash":
                result = await self._run_bash(
                    tool,
                    args,
                    exec_fn,
                    context,
                    needs_lock,
                    allow_concurrent,
                )
            elif needs_lock and not allow_concurrent:
                if self._serial_lock is None:
                    self._serial_lock = asyncio.Lock()
                wait_start = asyncio.get_event_loop().time()
                async with self._serial_lock:
                    # Ignore sub-millisecond contention to avoid noisy telemetry.
                    wait_ms = (asyncio.get_event_loop().time() - wait_start) * 1000.0
                    if wait_ms >= 1.0:
                        self._emit_tool_wait(tool.tool_name, wait_ms, "serial_lock")
                    result = await exec_fn(args, context=context)
            else:
                result = await exec_fn(args, context=context)
            max_output = tool.config.max_output if isinstance(tool, BaseTool) else 0
            artifact_store = getattr(self._agent, "session_store", None)
            result_metadata = (
                dict(result.metadata) if isinstance(result.metadata, dict) else {}
            )
            image_subdir = result_metadata.pop("_image_artifact_subdir", "tool_outputs")
            normalized = normalize_tool_output(
                result.output,
                max_output=max_output,
                job_id=job_id,
                tool_name=tool.tool_name,
                artifact_store=artifact_store,
                image_subdir=image_subdir,
                # Bash materializes the full output to a temp file and exposes
                # its path via this metadata key (see builtins.tools.bash).
                saved_to=result_metadata.get("raw_output_path"),
            )
            metadata = dict(result_metadata)
            metadata.update(normalized.metadata)
            artifacts = normalized.metadata.get("artifacts")
            if isinstance(artifacts, list) and artifacts:
                session_metadata = dict(metadata.get("session_metadata") or {})
                prior = session_metadata.get("artifacts")
                if not isinstance(prior, list):
                    prior = []
                session_metadata["artifacts"] = prior + artifacts
                metadata["session_metadata"] = session_metadata
            if tool.tool_name == "bash" and not normalized.metadata.get("truncated"):
                discard_raw_output_file(metadata)
            job_result = JobResult(
                job_id=job_id,
                output=normalized.output,
                exit_code=result.exit_code,
                error=result.error,
                metadata=metadata,
            )

            self.job_store.update_status(
                job_id,
                state=JobState.DONE if result.success else JobState.ERROR,
                output_lines=normalized.stats.lines,
                output_bytes=normalized.stats.bytes,
                preview=normalized.stats.preview,
                error=result.error,
            )
            self.job_store.store_result(job_result)

            status = "done" if result.success else "failed"
            logger.info("Tool %s: %s", tool.tool_name, status)
            logger.debug("Tool job completed", job_id=job_id, success=result.success)

            # Direct calls are already awaited by the processing loop; publishing a
            # completion event would process the same result twice.
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

            self.job_store.update_status(
                job_id,
                state=JobState.ERROR,
                error=str(e),
            )

            job_result = JobResult(job_id=job_id, error=str(e))
            self.job_store.store_result(job_result)

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

    def _finalize_task(
        self,
        job_id: str,
        task: asyncio.Task[JobResult],
        is_direct: bool,
    ) -> None:
        """Release completed tasks and record cancellation before coroutine entry."""
        if self._tasks.get(job_id) is task:
            self._tasks.pop(job_id, None)
        if not task.cancelled() or self.job_store.get_result(job_id) is not None:
            return

        error_msg = "User manually interrupted this job."
        self.job_store.update_status(
            job_id,
            state=JobState.CANCELLED,
            error=error_msg,
        )
        result = JobResult(job_id=job_id, error=error_msg)
        self.job_store.store_result(result)
        if is_direct:
            return
        event = create_tool_complete_event(
            job_id=job_id,
            content="",
            error=error_msg,
        )
        if self._on_complete:
            self._on_complete(event)
        self._event_queue.put_nowait(event)

    def _retained_results(self) -> dict[str, JobResult]:
        """Return the bounded completed-result history owned by the JobStore."""
        results: dict[str, JobResult] = {}
        for status in self.job_store.get_completed_jobs():
            result = self.job_store.get_result(status.job_id)
            if result is not None:
                results[status.job_id] = result
        return results

    def _build_tool_context(self) -> ToolContext:
        """Build ToolContext for context-aware tools."""
        context = ToolContext(
            agent_name=self._agent_name,
            session=self._session,
            working_dir=self._working_dir,
            creature_id=self._creature_id,
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
        """Wait for a job result, returning ``None`` on timeout or unknown id."""
        task = self._tasks.get(job_id)
        if task is None:
            return self.job_store.get_result(job_id)

        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Wait timed out", job_id=job_id)
            return None
        except asyncio.CancelledError:
            if task.cancelled():
                return self.job_store.get_result(job_id)
            raise

    async def wait_all(
        self,
        timeout: float | None = None,
    ) -> dict[str, JobResult]:
        """Wait for tracked jobs and return results available before timeout."""
        tasks = list(self._tasks.values())
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            if pending:
                logger.warning("Wait all timed out")
        return self._retained_results()

    async def cancel(self, job_id: str) -> bool:
        """Cancel a running job and report whether cancellation was requested."""
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
        return self.job_store.get_result(job_id)

    def get_task(self, job_id: str) -> asyncio.Task | None:
        """Return the task tracking ``job_id``, if present."""
        return self._tasks.get(job_id)

    def get_pending_count(self) -> int:
        """Return the number of unfinished tasks tracked by the executor."""
        return sum(not task.done() for task in self._tasks.values())

    def get_running_jobs(self) -> list[JobStatus]:
        """Get all running jobs."""
        return self.job_store.get_running_jobs()

    async def events(self) -> TriggerEvent:
        """Yield background tool completion events as they arrive."""
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
