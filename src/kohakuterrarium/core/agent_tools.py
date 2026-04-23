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
        waiters = {
            jid: asyncio.create_task(handle.wait()) for jid, handle in pending.items()
        }

        while waiters:
            done, _ = await asyncio.wait(
                waiters.values(), return_when=asyncio.FIRST_COMPLETED
            )

            finished_job_ids = [jid for jid, task in waiters.items() if task in done]
            for jid in finished_job_ids:
                future = waiters.pop(jid)
                pending.pop(jid, None)
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
                    self._emit_direct_completion_activity(jid, result)
                    self._clear_direct_job_tracking(jid)

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
        notify_controller_on_background_complete: bool = True,
    ) -> None:
        """Track a direct job so interrupt/cancel can finalize it reliably."""
        self._direct_job_meta[job_id] = {
            "kind": kind,
            "name": name,
            "tool_call_id": tool_call_id or job_id,
            "background": False,
            "interruptible": True,
            "notify_controller_on_background_complete": notify_controller_on_background_complete,
        }

    def _clear_direct_job_tracking(self, job_id: str) -> None:
        self._active_handles.pop(job_id, None)
        self._direct_job_meta.pop(job_id, None)
        self._bg_controller_notify.pop(job_id, None)

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
            "cancelled": False,
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
                job_id=job_id,
                error="User manually interrupted this job.",
                metadata={"interrupted": True, "final_state": "interrupted"},
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
            extra_context: dict[str, Any] = {}
            if isinstance(result, asyncio.CancelledError):
                error = "User manually interrupted this job."
                extra_context = {"interrupted": True, "final_state": "interrupted"}
            event = create_tool_complete_event(
                job_id=job_id, content="", error=error, **extra_context
            )
        elif hasattr(result, "output"):
            # JobResult or SubAgentResult
            extra_context = {
                "interrupted": bool(getattr(result, "interrupted", False)),
                "cancelled": bool(getattr(result, "cancelled", False)),
            }
            if extra_context["interrupted"]:
                extra_context["final_state"] = "interrupted"
            elif extra_context["cancelled"]:
                extra_context["final_state"] = "cancelled"
            event = create_tool_complete_event(
                job_id=job_id,
                content=result.output or "",
                exit_code=getattr(result, "exit_code", 0),
                error=result.error if hasattr(result, "error") else None,
                **extra_context,
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
                    "interrupted": bool(getattr(result, "interrupted", False)),
                    "cancelled": bool(getattr(result, "cancelled", False)),
                }
        else:
            event = create_tool_complete_event(
                job_id=job_id, content=str(result) if result else ""
            )

        self._on_bg_complete(event)

    def _emit_direct_completion_activity(self, job_id: str, result: Any) -> None:
        """Emit terminal activity immediately when a direct job finishes."""
        # Record for plugin termination checkers that want to peek at
        # the recent tool-result tail (cluster C.2 TerminationContext).
        checker = getattr(self, "_termination_checker", None)
        if checker is not None and hasattr(checker, "record_tool_result"):
            try:
                checker.record_tool_result(result)
            except Exception:  # pragma: no cover — defensive
                pass
        meta = self._direct_job_meta.get(job_id, {})
        kind = meta.get("kind", "subagent" if job_id.startswith("agent_") else "tool")
        _, label = _make_job_label(job_id)
        is_subagent = kind == "subagent"
        done_activity = "subagent_done" if is_subagent else "tool_done"
        error_activity = "subagent_error" if is_subagent else "tool_error"

        if isinstance(result, Exception):
            interrupted = isinstance(result, asyncio.CancelledError)
            error_text = (
                "User manually interrupted this job." if interrupted else str(result)
            )
            metadata: dict[str, Any] = {
                "job_id": job_id,
                "interrupted": interrupted,
                "cancelled": False,
                "final_state": "interrupted" if interrupted else "error",
                "error": error_text,
            }
            if is_subagent:
                metadata["result"] = error_text
            self.output_router.notify_activity(
                error_activity,
                f"[{label}] {'INTERRUPTED' if interrupted else 'FAILED'}: {error_text}",
                metadata=metadata,
            )
            return

        if result is not None and hasattr(result, "error") and result.error:
            output = result.output or ""
            interrupted = bool(getattr(result, "interrupted", False))
            cancelled = bool(getattr(result, "cancelled", False))
            final_state = (
                "interrupted" if interrupted else "cancelled" if cancelled else "error"
            )
            metadata = {
                "job_id": job_id,
                "interrupted": interrupted,
                "cancelled": cancelled,
                "final_state": final_state,
                "error": result.error,
                "result": output,
            }
            if is_subagent:
                metadata["turns"] = getattr(result, "turns", 0)
                metadata["duration"] = getattr(result, "duration", 0)
                metadata["total_tokens"] = getattr(result, "total_tokens", 0)
                metadata["prompt_tokens"] = getattr(result, "prompt_tokens", 0)
                metadata["completion_tokens"] = getattr(result, "completion_tokens", 0)
                metadata["tools_used"] = getattr(result, "metadata", {}).get(
                    "tools_used", []
                )
            state_label = (
                "INTERRUPTED" if interrupted else "CANCELLED" if cancelled else "ERROR"
            )
            self.output_router.notify_activity(
                error_activity,
                f"[{label}] {state_label}: {result.error}",
                metadata=metadata,
            )
            return

        output = (
            result.output
            if hasattr(result, "output")
            else str(result) if result else ""
        )
        output = output or ""
        exit_code = getattr(result, "exit_code", 0)
        status = "OK" if exit_code == 0 else f"exit={exit_code}"
        preview = (
            result.get_text_output()[:5000]
            if hasattr(result, "get_text_output")
            else str(output)[:5000]
        )
        metadata = {"job_id": job_id, "output": preview}
        if is_subagent:
            metadata["result"] = preview
            metadata["turns"] = getattr(result, "turns", 0)
            metadata["duration"] = getattr(result, "duration", 0)
            metadata["total_tokens"] = getattr(result, "total_tokens", 0)
            metadata["prompt_tokens"] = getattr(result, "prompt_tokens", 0)
            metadata["completion_tokens"] = getattr(result, "completion_tokens", 0)
            metadata["tools_used"] = getattr(result, "metadata", {}).get(
                "tools_used", []
            )
        self.output_router.notify_activity(
            done_activity,
            f"[{label}] {status}",
            metadata=metadata,
        )

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
            tool_name, _ = _make_job_label(job_id)
            tool_call_id = tool_call_ids.get(job_id, job_id)

            if isinstance(result, Exception):
                interrupted = isinstance(result, asyncio.CancelledError)
                error_text = (
                    "User manually interrupted this job."
                    if interrupted
                    else str(result)
                )
                prefix = "Interrupted" if interrupted else "Error"
                content = f"{prefix}: {error_text}"
            elif result is not None and hasattr(result, "error") and result.error:
                output = result.output or ""
                interrupted = bool(getattr(result, "interrupted", False))
                cancelled = bool(getattr(result, "cancelled", False))
                prefix = (
                    "Interrupted"
                    if interrupted
                    else "Cancelled" if cancelled else "Error"
                )
                content = f"{prefix}: {result.error}"
                if output:
                    content += f"\n{output}"
            elif result is not None:
                content = result.output if hasattr(result, "output") else str(result)
                content = content or ""
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
            elif result is not None:
                output = result.output if hasattr(result, "output") else str(result)
                output = output or ""
                error = getattr(result, "error", None)
                if error:
                    interrupted = bool(getattr(result, "interrupted", False))
                    cancelled = bool(getattr(result, "cancelled", False))
                    state = (
                        "INTERRUPTED"
                        if interrupted
                        else "CANCELLED" if cancelled else "ERROR"
                    )
                    result_strs.append(f"## {job_id} - {state}\n{error}\n{output}")
                else:
                    exit_code = getattr(result, "exit_code", 0)
                    status = "OK" if exit_code == 0 else f"exit={exit_code}"
                    result_strs.append(f"## {job_id} - {status}\n{output}")

        return "\n\n".join(result_strs) if result_strs else ""


@dataclass(slots=True)
class _TurnResult:
    """Results from a single LLM turn, used internally by the controller loop."""

    handles: dict[str, BackgroundifyHandle] = field(default_factory=dict)
    handle_order: list[str] = field(default_factory=list)
    text_output: list[str] = field(default_factory=list)
    native_mode: bool = False
    native_tool_call_ids: dict[str, str] = field(default_factory=dict)
