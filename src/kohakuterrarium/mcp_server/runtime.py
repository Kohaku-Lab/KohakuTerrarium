"""MCP tools and optional delegation using KT dispatch and runtime owners."""

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from kohakuterrarium.bootstrap.plugins import init_plugins
from kohakuterrarium.bootstrap.tools import create_tool
from kohakuterrarium.core.backgroundify import PromotionResult
from kohakuterrarium.core.config_types import ToolConfigItem
from kohakuterrarium.core.execution_context import ExecutionBinding
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.job import JobResult
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.core.tool_dispatch import (
    ToolDispatchBlocked,
    dispatch_tool_hooks,
    start_tool_task,
    tool_background_handle,
)
from kohakuterrarium.llm.tools import build_tool_schemas
from kohakuterrarium.mcp_server.config import MCPToolsConfig
from kohakuterrarium.mcp_server.delegation import DelegationRuntime
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.parsing import ToolCallEvent
from kohakuterrarium.utils.file_guard import FileReadState, PathBoundaryGuard

_EXECUTION_OPTIONS = {"max_output"}
_UNSUPPORTED_HOOKS = (
    "pre_llm_call",
    "post_llm_call",
    "pre_subagent_run",
    "post_subagent_run",
    "on_agent_start",
    "on_agent_stop",
    "on_event",
    "on_interrupt",
    "on_compact_start",
    "on_compact_end",
    "get_prompt_content",
    "get_tool_visibility",
    "contribute_commands",
    "contribute_user_commands",
    "contribute_termination_check",
)


@dataclass(frozen=True)
class JobOperationResult:
    """Operation outcome, independent of the retained job's execution state."""

    data: dict[str, Any]
    outcome: Literal["ok", "not_found"] = "ok"


class ToolCatalog:
    """Validate configured modules and expose their workspace-independent schemas."""

    def __init__(
        self,
        config,
        base_dir,
        *,
        plugins: list[BasePlugin] = (),
    ):
        self.config = config
        self.registry = Registry()
        loader = ModuleLoader(base_dir)
        self.plugins = init_plugins(
            [p.model_dump(by_alias=True, exclude_none=True) for p in config.plugins],
            loader,
            strict=True,
        )
        for plugin in plugins:
            self.plugins.register(plugin)
        for entry in self.plugins.list_plugins():
            if not entry["enabled"]:
                continue
            plugin = self.plugins.get_plugin(entry["name"])
            for hook in _UNSUPPORTED_HOOKS:
                if getattr(type(plugin), hook) is not getattr(BasePlugin, hook):
                    raise ValueError(
                        f"Plugin {entry['name']} requires unsupported hook {hook}"
                    )
        for spec in config.tools:
            tool = create_tool(
                ToolConfigItem(
                    name=spec.name,
                    options=dict(spec.config),
                    doc_mode="full",
                ),
                loader,
                strict=True,
            )
            supported = _EXECUTION_OPTIONS | set(tool.runtime_option_schema())
            if spec.name in {"bash", "python"}:
                supported.add("timeout")
            if spec.name == "bash":
                supported.add("env")
            unknown = set(spec.config) - supported
            if unknown:
                raise ValueError(
                    f"Unsupported options for {spec.name}: {sorted(unknown)}"
                )
            self.registry.register_tool(tool)

    def schemas(self):
        return build_tool_schemas(self.registry, tool_doc_mode="full")


class ToolRuntime(ToolCatalog):
    """Own direct tools and explicitly registered delegates for one workspace."""

    def __init__(
        self,
        config: MCPToolsConfig,
        *,
        plugins: list[BasePlugin] = (),
        llm_factory=None,
    ):
        super().__init__(config, config.workspace, plugins=plugins)
        self.instance_id = uuid.uuid4().hex
        context = ToolContext(
            agent_name=config.name,
            session=None,
            working_dir=config.workspace,
            file_read_state=FileReadState(),
            path_guard=PathBoundaryGuard(config.workspace, mode=config.pwd_guard),
        )
        self.executor = Executor(
            binding=ExecutionBinding(
                context,
                self.plugins,
                tool_doc_mode="full",
                job_namespace=self.instance_id,
            )
        )
        for name in self.registry.list_tools():
            self.executor.register_tool(self.registry.get_tool(name))
        self.plugin_context = PluginContext(
            agent_name=config.name,
            working_dir=config.workspace,
            session_id=self.instance_id,
        )
        self._handles = {}
        self._running = False
        self._closed = False
        self._calls: set[asyncio.Task] = set()
        self._close_task: asyncio.Task[None] | None = None
        self._delegation_closed = False
        self.delegation = DelegationRuntime(
            config, self.executor.job_store, self.instance_id, llm_factory=llm_factory
        )

    async def __aenter__(self):
        if self._closed or self._running:
            raise RuntimeError("Tool runtime cannot be started twice")
        try:
            await self.plugins.load_all(self.plugin_context, strict=True)
        except BaseException:
            await self.plugins.unload_all()
            self._closed = True
            raise
        self._running = True
        return self

    async def __aexit__(self, *_):
        await self.close()

    @property
    def is_busy(self) -> bool:
        """Whether execution or an unclosed delegation session occupies this runtime."""
        return bool(
            self._calls
            or self.executor.get_pending_count()
            or any(s["state"] != "closed" for s in self.delegation.sessions())
        )

    async def close(self) -> None:
        """Stop admission and release owned resources; failed cleanup can be retried."""
        self._running = False
        self._closed = True
        if self._close_task is None or (
            self._close_task.done()
            and (
                self._close_task.cancelled() or self._close_task.exception() is not None
            )
        ):
            self._close_task = asyncio.create_task(self._close_resources())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close_resources(self) -> None:
        errors = []
        calls = set(self._calls)
        for task in calls:
            task.cancel()
        await asyncio.gather(*calls, return_exceptions=True)
        if not self._delegation_closed:
            try:
                await self.delegation.close()
                self._delegation_closed = True
            except BaseException as exc:
                errors.append(exc)
        for job in self.executor.get_running_jobs():
            try:
                await self.executor.cancel(job.job_id)
            except BaseException as exc:
                errors.append(exc)
        try:
            await self.executor.wait_all()
        except BaseException as exc:
            errors.append(exc)
        try:
            await self.plugins.unload_all(strict=True)
        except BaseException as exc:
            errors.append(exc)
        if errors:
            raise errors[0]

    async def call(
        self, name: str, args: dict[str, Any]
    ) -> JobResult | PromotionResult:
        if not self._running:
            raise RuntimeError("Tool runtime is not running")
        task = asyncio.current_task()
        self._calls.add(task)
        try:
            return await self._call(name, args)
        finally:
            self._calls.discard(task)

    async def _call(self, name, args):
        if name not in self.registry.list_tools():
            raise ValueError(f"Unknown tool: {name}")
        event = ToolCallEvent(name=name, args=dict(args))
        try:
            event = await dispatch_tool_hooks(
                self.plugins,
                event,
                self.plugin_context,
                self.registry.list_tools(),
            )
        except ToolDispatchBlocked as block:
            return JobResult(job_id="", error=f"[{block.plugin_name}] {block}")
        run_background = event.args.pop("run_in_background", False)
        job_id, task, direct = await start_tool_task(self.executor, event)
        handle = tool_background_handle(task, job_id, direct, run_background)
        self._handles[job_id] = handle
        task.add_done_callback(lambda _: self._handles.pop(job_id, None))
        # No Controller completion delivery: all exported tools are DIRECT, and
        # promotion reuses that same job. Clients explicitly query retained results.
        return await handle.wait()

    def job(self, job_id: str) -> dict[str, Any]:
        status = self.executor.get_status(job_id)
        if status is None:
            return {"job_id": job_id, "error": "Unknown job"}
        result = self.executor.get_result(job_id)
        return {
            "job_id": job_id,
            "tool": status.type_name,
            "state": status.state.value,
            "output": result.get_text_output() if result else "",
            "error": result.error if result else status.error,
            "exit_code": result.exit_code if result else None,
            "metadata": {
                **(status.context if self.delegation.owns(job_id) else {}),
                **(result.metadata if result else {}),
            },
        }

    def jobs(self) -> list[dict[str, Any]]:
        return [self.job(s.job_id) for s in self.executor.job_store.get_all_statuses()]

    async def job_operation(
        self, operation: str, job_id: str | None = None, *, timeout: float = 10
    ) -> JobOperationResult:
        """Query or control a job without conflating missing handles and task failure."""
        if operation not in {"status", "wait", "cancel", "promote"}:
            raise ValueError("Unknown job operation")
        if operation == "status" and job_id is None:
            return JobOperationResult(
                {"instance_id": self.instance_id, "jobs": self.jobs()}
            )
        if job_id is None:
            raise ValueError("job_id is required")
        if self.executor.get_status(job_id) is None:
            return JobOperationResult(
                {"job_id": job_id, "error": "Unknown job"}, "not_found"
            )
        if operation == "status":
            return JobOperationResult(self.job(job_id))
        if operation == "wait":
            data = await self.wait(job_id, timeout)
            return JobOperationResult(
                data,
                "ok" if self.executor.get_status(job_id) is not None else "not_found",
            )
        if operation == "cancel":
            return JobOperationResult(
                {"job_id": job_id, "cancelled": await self.cancel(job_id)}
            )
        return JobOperationResult({"job_id": job_id, "promoted": self.promote(job_id)})

    async def wait(self, job_id: str, timeout: float = 10) -> dict[str, Any]:
        if not 0 <= timeout <= 60:
            raise ValueError("Wait timeout must be between 0 and 60 seconds")
        if self.delegation.owns(job_id):
            await self.delegation.wait(job_id, timeout)
        else:
            await self.executor.wait_for(job_id, timeout)
        return self.job(job_id)

    async def cancel(self, job_id: str) -> bool:
        if self.delegation.owns(job_id):
            return await self.delegation.cancel(job_id)
        return await self.executor.cancel(job_id)

    def promote(self, job_id: str) -> bool:
        handle = self._handles.get(job_id)
        if handle is None or handle.promoted or not handle.promote():
            return False
        asyncio.create_task(
            self.plugins.notify(
                "on_task_promoted",
                job_id=job_id,
                tool_name=self.executor.get_status(job_id).type_name,
            )
        )
        return True
