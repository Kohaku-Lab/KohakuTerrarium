"""Agent tool execution mixin — handles tool dispatch, result collection, and background jobs."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.core.backgroundify import BackgroundifyHandle, PromotionResult
from kohakuterrarium.core.controller import Controller
from kohakuterrarium.core.events import create_tool_complete_event
from kohakuterrarium.core.agent_runtime_tools import (
    AgentRuntimeToolsMixin,
    _make_job_label,
)
from kohakuterrarium.core.job import JobResult
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode
from kohakuterrarium.parsing import ToolCallEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentToolsMixin(AgentRuntimeToolsMixin):
    """Mixin providing tool execution and background job handling for the Agent class.

    Contains tool startup, result collection, sub-agent spawning,
    and background completion callbacks.
    """

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _start_tool_async(
        self, tool_call: ToolCallEvent
    ) -> tuple[str, asyncio.Task, bool]:
        """Start a tool execution immediately as an async task.

        Does NOT wait for completion.

        Returns:
            (job_id, task, is_direct): is_direct indicates if we should wait
        """
        try:
            logger.info("Running tool: %s", tool_call.name)
            tool = self.executor.get_tool(tool_call.name)
            is_direct = True
            if tool and isinstance(tool, BaseTool):
                is_direct = tool.execution_mode == ExecutionMode.DIRECT

            job_id = await self.executor.submit_from_event(
                tool_call, is_direct=is_direct
            )
            task = self.executor.get_task(job_id)
            if task is None:

                async def _get_result():
                    return self.executor.get_result(job_id)

                task = asyncio.create_task(_get_result())

            return job_id, task, is_direct
        except Exception as e:
            logger.error("Failed to start tool", tool_name=tool_call.name, error=str(e))
            error_msg = str(e)
            error_job_id = f"error_{tool_call.name}"

            async def _error_result():
                return JobResult(job_id=error_job_id, error=error_msg)

            task = asyncio.create_task(_error_result())
            return error_job_id, task, True

    # ------------------------------------------------------------------
    # Handle-based waiting (replaces asyncio.gather)
    # ------------------------------------------------------------------

    async def _wait_handles(
        self,
        handles: dict[str, BackgroundifyHandle],
        handle_order: list[str],
        controller: Controller,
        tool_call_ids: dict[str, str],
        native_mode: bool,
    ) -> tuple[dict[str, Any], bool]:
        """Wait for all handles, processing promotions as they occur.

        Returns:
            (results, had_promotions) — results maps job_id → result for
            tasks that completed as direct.  had_promotions is True if
            any task was promoted (placeholder already added to conversation).
        """
        if not handles:
            return {}, False

        results: dict[str, Any] = {}
        had_promotions = False
        pending = dict(handles)

        while pending:
            futures = {
                asyncio.ensure_future(h.wait()): jid for jid, h in pending.items()
            }

            done, _ = await asyncio.wait(
                futures.keys(), return_when=asyncio.FIRST_COMPLETED
            )

            for future in done:
                jid = futures[future]
                pending.pop(jid)
                try:
                    result = future.result()
                except (asyncio.CancelledError, Exception) as exc:
                    result = exc

                if isinstance(result, PromotionResult):
                    self._handle_promotion(jid, controller, tool_call_ids, native_mode)
                    self._clear_direct_job_tracking(jid)
                    had_promotions = True
                else:
                    results[jid] = result
                    self._clear_direct_job_tracking(jid)

            for f in futures:
                if f not in done:
                    f.cancel()

        return results, had_promotions

    def _handle_promotion(
        self,
        job_id: str,
        controller: Controller,
        tool_call_ids: dict[str, str],
        native_mode: bool,
    ) -> None:
        """Handle a task that was promoted to background mid-wait."""
        tool_name, label = _make_job_label(job_id)
        logger.info("Task promoted to background", job_id=job_id)

        # In native mode, add placeholder tool result so conversation stays valid
        tool_call_id = tool_call_ids.get(job_id)
        if native_mode and tool_call_id:
            controller.conversation.append(
                "tool",
                f"[{tool_name}] Promoted to background. Result arrives in a later turn.",
                tool_call_id=tool_call_id,
                name=tool_name,
            )

        # Plugin callback
        if hasattr(self, "plugins") and self.plugins:
            asyncio.create_task(
                self.plugins.notify(
                    "on_task_promoted", job_id=job_id, tool_name=tool_name
                )
            )

        meta = self._direct_job_meta.get(job_id)
        if meta is not None:
            meta["background"] = True
            meta["interruptible"] = False

        self.output_router.notify_activity(
            "task_promoted",
            f"[{label}] Moved to background",
            metadata={"job_id": job_id},
        )

    def _interrupt_direct_job(self, job_id: str) -> bool:
        """Cancel and finalize a direct job tracked by the current run."""
        meta = self._direct_job_meta.get(job_id)
        handle = self._active_handles.get(job_id)
        if not meta or not handle or handle.promoted or handle.done:
            return False
        if meta.get("kind") == "subagent":
            job = self.subagent_manager._jobs.get(job_id)
            if job and hasattr(job, "subagent"):
                job.subagent.cancel()
        handle.task.cancel()
        asyncio.create_task(self._finalize_interrupted_direct_job(job_id))
        return True

    def _register_direct_job(
        self,
        job_id: str,
        *,
        kind: str,
        name: str,
        tool_call_id: str | None = None,
    ) -> None:
        """Track a direct job so interrupt/cancel can finalize it reliably."""
        self._direct_job_meta[job_id] = {
            "kind": kind,
            "name": name,
            "tool_call_id": tool_call_id or job_id,
            "background": False,
            "interruptible": True,
        }

    def _clear_direct_job_tracking(self, job_id: str) -> None:
        self._active_handles.pop(job_id, None)
        self._direct_job_meta.pop(job_id, None)

    def _emit_interrupted_activity(self, job_id: str, result: Any) -> None:
        """Emit terminal activity for an interrupted direct job."""
        meta = self._direct_job_meta.get(job_id, {})
        kind = meta.get("kind", "tool")
        _, label = _make_job_label(job_id)
        error = getattr(result, "error", None) or "User manually interrupted this job."
        activity = "subagent_error" if kind == "subagent" else "tool_error"
        activity_meta: dict[str, Any] = {
            "job_id": job_id,
            "interrupted": True,
            "final_state": "interrupted",
            "error": error,
        }
        if kind == "subagent":
            activity_meta["result"] = getattr(result, "output", "") or error
            activity_meta["turns"] = getattr(result, "turns", 0)
            activity_meta["duration"] = getattr(result, "duration", 0)
            activity_meta["total_tokens"] = getattr(result, "total_tokens", 0)
            activity_meta["prompt_tokens"] = getattr(result, "prompt_tokens", 0)
            activity_meta["completion_tokens"] = getattr(result, "completion_tokens", 0)
            activity_meta["tools_used"] = getattr(result, "metadata", {}).get(
                "tools_used", []
            )
        self.output_router.notify_activity(
            activity,
            f"[{label}] INTERRUPTED: {error}",
            metadata=activity_meta,
        )

    async def _finalize_interrupted_direct_job(self, job_id: str) -> None:
        """Wait for cancellation to settle, then emit a terminal interrupted event."""
        handle = self._active_handles.get(job_id)
        meta = self._direct_job_meta.get(job_id)
        if not handle or not meta:
            return

        try:
            result = await asyncio.shield(handle.task)
        except asyncio.CancelledError:
            result = None

        if result is None:
            kind = meta.get("kind", "tool")
            if kind == "subagent":
                result = self.subagent_manager.get_result(job_id)
            else:
                result = self.executor.get_result(job_id)

        if result is None:
            result = JobResult(
                job_id=job_id, error="User manually interrupted this job."
            )

        self._emit_interrupted_activity(job_id, result)
        self._clear_direct_job_tracking(job_id)

    async def _on_backgroundify_complete(self, job_id: str, result: Any) -> None:
        """Callback when a promoted (backgroundified) task completes.

        Builds a TriggerEvent and reuses the existing ``_on_bg_complete``
        path for activity notification and event processing.
        """
        if isinstance(result, Exception):
            error = str(result)
            if isinstance(result, asyncio.CancelledError):
                error = "User manually interrupted this job."
            event = create_tool_complete_event(job_id=job_id, content="", error=error)
        elif hasattr(result, "output"):
            # JobResult or SubAgentResult
            event = create_tool_complete_event(
                job_id=job_id,
                content=result.output or "",
                exit_code=getattr(result, "exit_code", 0),
                error=result.error if hasattr(result, "error") else None,
            )
            # Attach sub-agent metadata if present
            if hasattr(result, "turns"):
                if event.context is None:
                    event.context = {}
                event.context["subagent_metadata"] = {
                    "tools_used": getattr(result, "metadata", {}).get("tools_used", []),
                    "turns": result.turns,
                    "duration": getattr(result, "duration", 0),
                    "total_tokens": getattr(result, "total_tokens", 0),
                    "prompt_tokens": getattr(result, "prompt_tokens", 0),
                    "completion_tokens": getattr(result, "completion_tokens", 0),
                }
        else:
            event = create_tool_complete_event(
                job_id=job_id, content=str(result) if result else ""
            )

        self._on_bg_complete(event)

    # ------------------------------------------------------------------
    # Result processing (native and text format)
    # ------------------------------------------------------------------

    def _add_native_results_to_conversation(
        self,
        controller: Controller,
        handle_order: list[str],
        results: dict[str, Any],
        tool_call_ids: dict[str, str],
    ) -> None:
        """Add completed results as role='tool' messages (native mode)."""
        for job_id in handle_order:
            if job_id not in results:
                continue  # Was promoted — placeholder already added

            result = results[job_id]
            tool_name, label = _make_job_label(job_id)
            tool_call_id = tool_call_ids.get(job_id, job_id)

            if isinstance(result, Exception):
                interrupted = isinstance(result, asyncio.CancelledError)
                error_text = (
                    "User manually interrupted this job."
                    if interrupted
                    else str(result)
                )
                content = f"Error: {error_text}"
                self.output_router.notify_activity(
                    "tool_error",
                    f"[{label}] {'INTERRUPTED' if interrupted else 'FAILED'}: {error_text}",
                    metadata={
                        "job_id": job_id,
                        "interrupted": interrupted,
                        "final_state": "interrupted" if interrupted else "error",
                        "error": error_text,
                    },
                )
            elif result is not None and hasattr(result, "error") and result.error:
                output = result.output or ""
                content = f"Error: {result.error}"
                if output:
                    content += f"\n{output}"
                self.output_router.notify_activity(
                    "tool_error",
                    f"[{label}] {'INTERRUPTED' if getattr(result, 'error', None) == 'User manually interrupted this job.' else 'ERROR'}: {result.error}",
                    metadata={
                        "job_id": job_id,
                        "interrupted": getattr(result, "error", None)
                        == "User manually interrupted this job.",
                        "final_state": (
                            "interrupted"
                            if getattr(result, "error", None)
                            == "User manually interrupted this job."
                            else "error"
                        ),
                        "error": result.error,
                        "result": output,
                    },
                )
            elif result is not None:
                content = result.output if hasattr(result, "output") else str(result)
                content = content or ""
                exit_code = getattr(result, "exit_code", 0)
                status = "OK" if exit_code == 0 else f"exit={exit_code}"
                preview = (
                    result.get_text_output()[:5000]
                    if hasattr(result, "get_text_output")
                    else str(content)[:5000]
                )
                is_subagent = job_id.startswith("agent_")
                activity = "subagent_done" if is_subagent else "tool_done"
                meta: dict = {"job_id": job_id, "output": preview}
                if is_subagent:
                    meta["result"] = preview
                    meta["turns"] = getattr(result, "turns", 0)
                    meta["duration"] = getattr(result, "duration", 0)
                    meta["total_tokens"] = getattr(result, "total_tokens", 0)
                    meta["tools_used"] = getattr(result, "metadata", {}).get(
                        "tools_used", []
                    )
                self.output_router.notify_activity(
                    activity, f"[{label}] {status}", metadata=meta
                )
            else:
                content = ""

            controller.conversation.append(
                "tool", content, tool_call_id=tool_call_id, name=tool_name
            )

    def _format_text_results(
        self,
        handle_order: list[str],
        results: dict[str, Any],
    ) -> str:
        """Format completed results as text feedback (non-native mode)."""
        result_strs: list[str] = []
        for job_id in handle_order:
            if job_id not in results:
                continue  # Was promoted

            result = results[job_id]
            _, label = _make_job_label(job_id)

            if isinstance(result, Exception):
                interrupted = isinstance(result, asyncio.CancelledError)
                error_text = (
                    "User manually interrupted this job."
                    if interrupted
                    else str(result)
                )
                result_strs.append(
                    f"## {job_id} - {'INTERRUPTED' if interrupted else 'FAILED'}\n{error_text}"
                )
                self.output_router.notify_activity(
                    "tool_error",
                    f"[{label}] {'INTERRUPTED' if interrupted else 'FAILED'}: {error_text}",
                    metadata={
                        "job_id": job_id,
                        "interrupted": interrupted,
                        "final_state": "interrupted" if interrupted else "error",
                        "error": error_text,
                    },
                )
            elif result is not None:
                output = result.output if hasattr(result, "output") else str(result)
                output = output or ""
                error = getattr(result, "error", None)
                if error:
                    result_strs.append(f"## {job_id} - ERROR\n{error}\n{output}")
                    interrupted = error == "User manually interrupted this job."
                    self.output_router.notify_activity(
                        "tool_error",
                        f"[{label}] {'INTERRUPTED' if interrupted else 'ERROR'}: {error}",
                        metadata={
                            "job_id": job_id,
                            "interrupted": interrupted,
                            "final_state": "interrupted" if interrupted else "error",
                            "error": error,
                            "result": output,
                        },
                    )
                else:
                    exit_code = getattr(result, "exit_code", 0)
                    status = "OK" if exit_code == 0 else f"exit={exit_code}"
                    result_strs.append(f"## {job_id} - {status}\n{output}")
                    self.output_router.notify_activity(
                        "tool_done",
                        f"[{label}] {status}",
                        metadata={"job_id": job_id, "result": output[:5000]},
                    )

        return "\n\n".join(result_strs) if result_strs else ""


@dataclass(slots=True)
class _TurnResult:
    """Results from a single LLM turn, used internally by the controller loop."""

    handles: dict[str, BackgroundifyHandle] = field(default_factory=dict)
    handle_order: list[str] = field(default_factory=list)
    text_output: list[str] = field(default_factory=list)
    native_mode: bool = False
    native_tool_call_ids: dict[str, str] = field(default_factory=dict)
