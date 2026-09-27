"""MCP tools and optional delegation using KT dispatch and runtime owners."""

import asyncio
import uuid
from typing import Any

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


class ToolRuntime:
    """Own direct tools and explicitly registered delegates for one server."""

    def __init__(
        self,
        config: MCPToolsConfig,
        *,
        plugins: list[BasePlugin] = (),
        llm_factory=None,
    ):
        self.config = config
        self.instance_id = uuid.uuid4().hex
        self.registry = Registry()
        loader = ModuleLoader(config.workspace)
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
        self._running = False
        self._closed = True
        try:
            await self.delegation.close()
        finally:
            jobs = self.executor.get_running_jobs()
            for job in jobs:
                await self.executor.cancel(job.job_id)
            await self.executor.wait_all()
            await self.plugins.unload_all()

    def schemas(self):
        """Expose only registered executable tools, with full docs inline."""
        return build_tool_schemas(self.registry, tool_doc_mode="full")

    async def call(
        self, name: str, args: dict[str, Any]
    ) -> JobResult | PromotionResult:
        if not self._running:
            raise RuntimeError("Tool runtime is not running")
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
