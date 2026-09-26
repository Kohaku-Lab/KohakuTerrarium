"""Explicit tool execution bindings shared by agents and tool-only hosts."""

from dataclasses import dataclass, replace
from typing import Any, Callable

from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.modules.tool.base import BaseTool, Tool, ToolContext
from kohakuterrarium.modules.tool.doc_mode import (
    DEFAULT_DOC_MODE,
    DOC_MODE_BRIEF,
    DOC_MODE_FULL,
    resolve_doc_mode,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ExecutionBinding:
    """Dependencies for execution without constructing an Agent or LLM."""

    context: ToolContext
    plugins: PluginManager
    tool_doc_mode: str = DEFAULT_DOC_MODE
    job_namespace: str = "tool"
    artifact_store: Any = None


class ExecutorContextMixin:
    """Resolve explicit bindings or the existing live Agent binding."""

    @property
    def _execution_plugins(self):
        if self.binding is not None:
            return self.binding.plugins
        return getattr(self._agent, "plugins", None)

    @property
    def _artifact_store(self):
        if self.binding is not None:
            return self.binding.artifact_store
        return getattr(self._agent, "session_store", None)

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
        plugins = self._execution_plugins
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

    def _manual_read_gate_active(self) -> bool:
        """Return whether first-use documentation gating is actionable.

        Inlined docs make the gate redundant; a missing ``info`` tool makes it
        impossible to satisfy.
        """
        if self.binding is not None:
            return self.binding.tool_doc_mode != DOC_MODE_FULL
        agent = self._agent
        if agent is None:
            return True
        config = getattr(agent, "config", None)
        if getattr(config, "tool_doc_mode", DEFAULT_DOC_MODE) == DOC_MODE_FULL:
            return False
        registry = getattr(agent, "registry", None)
        if registry is not None and registry.get_tool("info") is None:
            return False
        return True

    def _requires_manual_read(self, tool: Tool) -> bool:
        """Return whether this tool must be documented before its first use.

        Either the tool declares it, or its resolved tier withheld the
        parameter prose that would let the model call it correctly.
        """
        if not isinstance(tool, BaseTool):
            return False
        if tool.require_manual_read:
            return True
        default = getattr(
            getattr(self._agent, "config", None), "tool_doc_mode", DEFAULT_DOC_MODE
        )
        if self.binding is not None:
            default = self.binding.tool_doc_mode
        return resolve_doc_mode(tool, default) == DOC_MODE_BRIEF

    def _build_tool_context(self) -> ToolContext:
        """Build ToolContext for context-aware tools."""
        if self.binding is not None:
            context = replace(
                self.binding.context,
                runtime_services=dict(self.binding.context.runtime_services),
            )
            context.runtime_services.update(
                self.binding.plugins.collect_runtime_services(context)
            )
            return context
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
        plugins = self._execution_plugins
        if plugins is not None and hasattr(plugins, "collect_runtime_services"):
            context.runtime_services.update(plugins.collect_runtime_services(context))
        return context
