"""Agent tool execution mixin — handles tool dispatch, result collection, and background jobs."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.core.agent.tools.runtime_tools import (
    AgentRuntimeToolsMixin,
    _make_job_label,
)
from kohakuterrarium.core.agent.tools.metrics import (
    emit_completion_metrics as _emit_completion_metrics,
)
from kohakuterrarium.core.conversation.continuation import live_branch_path
from kohakuterrarium.core.jobs.backgroundify import BackgroundifyHandle, PromotionResult
from kohakuterrarium.core.controller.controller import Controller
from kohakuterrarium.core.controller.context import tool_feedback_metadata
from kohakuterrarium.core.events import create_tool_complete_event
from kohakuterrarium.core.tools.dispatch import start_tool_task
from kohakuterrarium.core.tools.output import render_content_text
from kohakuterrarium.llm.message import content_parts_to_dicts
from kohakuterrarium.parsing import ToolCallEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _is_interactive_user_input(event: Any) -> bool:
    return getattr(event, "type", "") == "user_input" and not (
        getattr(event, "context", None) or {}
    ).get("await_turn")


class AgentToolsMixin(AgentRuntimeToolsMixin):
    """Provide tool execution, result handling, and background job management."""

    async def _start_tool_async(
        self, tool_call: ToolCallEvent
    ) -> tuple[str, asyncio.Task, bool]:
        """Start a tool asynchronously and return its ID, task, and wait mode."""
        return await start_tool_task(self.executor, tool_call)

    async def _wait_handles(
        self,
        handles: dict[str, BackgroundifyHandle],
        handle_order: list[str],
        controller: Controller,
        tool_call_ids: dict[str, str],
        native_mode: bool,
    ) -> tuple[dict[str, Any], bool]:
        """Collect direct results while processing background promotions.

        Interactive user input must not wait out a long direct job: the only
        mid-turn drain runs AFTER this wait, so its arrival promotes the
        remaining handles — the round boundary (and the drain behind it)
        runs now, and each promoted job's real result returns through the
        background-completion fold in a later round. Background
        completions alone keep the natural boundary.
        """
        if not handles:
            return {}, False

        results: dict[str, Any] = {}
        had_promotions = False
        pending = dict(handles)
        waiters = {
            jid: asyncio.create_task(handle.wait()) for jid, handle in pending.items()
        }

        tracked = set(handles) & self._direct_job_meta.keys()
        try:
            while waiters:
                if self._event_inbox.has_event(_is_interactive_user_input):
                    for handle in pending.values():
                        if not handle.promoted:
                            handle.promote()
                arrival = asyncio.create_task(self._event_inbox.wait_put())
                try:
                    done, _ = await asyncio.wait(
                        set(waiters.values()) | {arrival},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    if not arrival.done():
                        arrival.cancel()
                        try:
                            await arrival
                        except asyncio.CancelledError:
                            pass

                finished_job_ids = [
                    jid for jid, task in waiters.items() if task in done
                ]
                for jid in finished_job_ids:
                    future = waiters.pop(jid)
                    pending.pop(jid, None)
                    try:
                        result = future.result()
                    except (asyncio.CancelledError, Exception) as exc:
                        result = exc
                    if isinstance(result, PromotionResult):
                        if jid not in self._turn_cancelled_jobs:
                            self._handle_promotion(
                                jid, controller, tool_call_ids, native_mode
                            )
                            self._direct_job_meta.pop(jid, None)
                        had_promotions = True
                    else:
                        if jid in self._turn_cancelled_jobs:
                            continue
                        results[jid] = result
                        if jid not in tracked or jid in self._direct_job_meta:
                            self._emit_direct_completion_activity(jid, result)
                            self._clear_direct_job_tracking(jid)
        except (asyncio.CancelledError, Exception):
            self._save_direct_results(
                controller, handle_order, results, tool_call_ids, native_mode
            )
            raise
        finally:
            for waiter in waiters.values():
                waiter.cancel()
            await asyncio.gather(*waiters.values(), return_exceptions=True)
        return results, had_promotions

    def _save_direct_results(
        self,
        controller: Controller,
        handle_order: list[str],
        results: dict[str, Any],
        tool_call_ids: dict[str, str],
        native_mode: bool,
    ) -> None:
        """Preserve collected job feedback without starting another model request."""
        if native_mode:
            self._add_native_results_to_conversation(
                controller, handle_order, results, tool_call_ids
            )
        elif text := self._format_text_results(handle_order, results):
            job_ids = [job_id for job_id in handle_order if job_id in results]
            events = [
                create_tool_complete_event(job_id=job_id, content="")
                for job_id in job_ids
            ]
            controller.conversation.append(
                "user",
                text,
                metadata={
                    **tool_feedback_metadata(events),
                    **self._message_origin(events),
                },
            )

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

        # Native tool-call protocols require a result even when the real result
        # will arrive in a later turn.
        tool_call_id = tool_call_ids.get(job_id)
        if (
            native_mode
            and tool_call_id
            and not self._native_result_exists(
                controller.conversation, job_id, tool_call_id
            )
        ):
            controller.conversation.append(
                "tool",
                (
                    f"[{tool_name}] Cancellation requested; outcome is not yet known. Result arrives in a later turn. Do not retry automatically."
                    if (self._direct_job_meta.get(job_id) or {}).get(
                        "cancellation_requested"
                    )
                    else f"[{tool_name}] Promoted to background. Result arrives in a later turn."
                ),
                tool_call_id=tool_call_id,
                name=tool_name,
                metadata={"job_id": job_id, "tool_result_phase": "pending"},
            )

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
        if not meta or not handle or handle.promoted:
            return False
        if handle.done:
            if not getattr(self, "_interrupt_requested", False):
                return False
            try:
                result = handle.task.result()
            except (asyncio.CancelledError, Exception) as exc:
                result = exc
            self._emit_direct_completion_activity(job_id, result)
            self._save_direct_results(
                self.controller,
                [job_id],
                {job_id: result},
                {job_id: meta["tool_call_id"]},
                meta["native_mode"],
            )
            self._turn_cancelled_jobs.add(job_id)
            self._clear_direct_job_tracking(job_id)
            return True
        if meta.get("kind") == "subagent":
            job = self.subagent_manager._jobs.get(job_id)
            if job and hasattr(job, "subagent"):
                job.subagent.cancel()
        if not getattr(self, "_interrupt_requested", False):
            handle.task.cancel()
            return True
        if meta.get("cancellation_requested"):
            return True
        meta["cancellation_requested"] = True
        self._turn_cancelled_jobs.add(job_id)
        if handle.promote():
            self._handle_promotion(
                job_id,
                self.controller,
                {job_id: meta["tool_call_id"]},
                meta["native_mode"],
            )
        handle.task.cancel()
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
            "native_mode": tool_call_id is not None,
            "branch_path": live_branch_path(self),
            "background": False,
            "interruptible": True,
            "notify_controller_on_background_complete": notify_controller_on_background_complete,
            # Monotonic time avoids wall-clock adjustments corrupting durations.
            "started_at": time.monotonic(),
        }

    def _clear_direct_job_tracking(self, job_id: str) -> None:
        self._active_handles.pop(job_id, None)
        self._direct_job_meta.pop(job_id, None)
        self._bg_controller_notify.pop(job_id, None)

    def _native_result_exists(
        self, conversation: Any, job_id: str, tool_call_id: str
    ) -> bool:
        """Check whether the current native call already has a response."""
        messages = conversation.get_messages()
        for message in reversed(messages):
            if message.role in {"assistant", "user"}:
                break
            if message.role == "tool" and message.tool_call_id == tool_call_id:
                existing_job = message.metadata.get("job_id")
                if not existing_job or existing_job == job_id:
                    return True
        return False

    async def _on_backgroundify_complete(self, job_id: str, result: Any) -> None:
        """Callback when a promoted (backgroundified) task completes.

        Builds a TriggerEvent and reuses the existing ``_on_bg_complete``
        path for activity notification and event processing.
        """
        # ``CancelledError`` is a ``BaseException`` and must precede the generic
        # arm so cancellation is reported as an interrupt rather than an error.
        if isinstance(result, asyncio.CancelledError):
            extra_context: dict[str, Any] = {
                "interrupted": True,
                "final_state": "interrupted",
            }
            event = create_tool_complete_event(
                job_id=job_id,
                content="",
                error="User manually interrupted this job.",
                **extra_context,
            )
        elif isinstance(result, BaseException):
            extra_context = {}
            event = create_tool_complete_event(
                job_id=job_id, content="", error=str(result), **extra_context
            )
        elif hasattr(result, "output"):
            extra_context = {
                "interrupted": bool(getattr(result, "interrupted", False))
                or bool((getattr(result, "metadata", {}) or {}).get("interrupted")),
                "cancelled": bool(getattr(result, "cancelled", False))
                or bool((getattr(result, "metadata", {}) or {}).get("cancelled")),
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
                result_metadata=dict(getattr(result, "metadata", {}) or {}),
                **extra_context,
            )
            if hasattr(result, "turns"):
                if event.context is None:
                    event.context = {}
                result_metadata = getattr(result, "metadata", {})
                event.context["subagent_metadata"] = {
                    "tools_used": result_metadata.get("tools_used", []),
                    "subagent": result_metadata.get("subagent", ""),
                    "llm_name": result_metadata.get("llm_name", ""),
                    "model": result_metadata.get("model", ""),
                    "turns": result.turns,
                    "duration": getattr(result, "duration", 0),
                    "total_tokens": getattr(result, "total_tokens", 0),
                    "prompt_tokens": getattr(result, "prompt_tokens", 0),
                    "completion_tokens": getattr(result, "completion_tokens", 0),
                    "cached_tokens": getattr(result, "cached_tokens", 0),
                    "interrupted": bool(getattr(result, "interrupted", False))
                    or bool((getattr(result, "metadata", {}) or {}).get("interrupted")),
                    "cancelled": bool(getattr(result, "cancelled", False))
                    or bool((getattr(result, "metadata", {}) or {}).get("cancelled")),
                }
        else:
            event = create_tool_complete_event(
                job_id=job_id, content=str(result) if result else ""
            )

        if (self._direct_job_meta.get(job_id) or {}).get("cancellation_requested"):
            event.context["defer_controller"] = True
            event.context["job_branch_path"] = self._direct_job_meta[job_id][
                "branch_path"
            ]
            meta = self._direct_job_meta[job_id]
            status = (
                "interrupted"
                if event.context.get("interrupted")
                else (
                    "cancelled"
                    if event.context.get("cancelled")
                    else (
                        "error"
                        if event.context.get("error")
                        or event.context.get("exit_code") not in (None, 0)
                        else "ok"
                    )
                )
            )
            _emit_completion_metrics(
                meta["kind"] == "subagent",
                meta["name"],
                status,
                (time.monotonic() - meta["started_at"]) * 1000.0,
            )
        self._on_bg_complete(event)
        self._clear_direct_job_tracking(job_id)

    def _emit_direct_completion_activity(self, job_id: str, result: Any) -> None:
        """Emit terminal activity immediately when a direct job finishes."""
        # The post-stop sweep already emitted a terminal state for this job.
        if not self._running:
            return
        # Termination checkers need the recent result tail to evaluate stopping.
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

        # Missing background metadata still contributes a count with zero duration.
        # Use the registry name because job-ID suffixes would create unbounded labels.
        started_at = meta.get("started_at")
        duration_ms = (
            (time.monotonic() - started_at) * 1000.0
            if isinstance(started_at, (int, float))
            else 0.0
        )
        metric_name = meta.get("name") or job_id.rsplit("_", 1)[0]

        if isinstance(result, (Exception, asyncio.CancelledError)):
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
                existing = self.subagent_manager.get_result(job_id)
                if existing is not None:
                    metadata["turns"] = getattr(existing, "turns", 0)
                    metadata["duration"] = getattr(existing, "duration", 0)
                    metadata["total_tokens"] = getattr(existing, "total_tokens", 0)
                    metadata["prompt_tokens"] = getattr(existing, "prompt_tokens", 0)
                    metadata["completion_tokens"] = getattr(
                        existing, "completion_tokens", 0
                    )
                    metadata["cached_tokens"] = getattr(existing, "cached_tokens", 0)
                    metadata["tools_used"] = getattr(existing, "metadata", {}).get(
                        "tools_used", []
                    )
            self.output_router.notify_activity(
                error_activity,
                f"[{label}] {'INTERRUPTED' if interrupted else 'FAILED'}: {error_text}",
                metadata=metadata,
            )
            status = "interrupted" if interrupted else "error"
            _emit_completion_metrics(is_subagent, metric_name, status, duration_ms)
            return

        if result is not None and hasattr(result, "error") and result.error:
            output = render_content_text(result.output or "")
            interrupted = bool(getattr(result, "interrupted", False)) or bool(
                (getattr(result, "metadata", {}) or {}).get("interrupted")
            )
            cancelled = bool(getattr(result, "cancelled", False)) or bool(
                (getattr(result, "metadata", {}) or {}).get("cancelled")
            )
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
                metadata["cached_tokens"] = getattr(result, "cached_tokens", 0)
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
            _emit_completion_metrics(is_subagent, metric_name, final_state, duration_ms)
            return

        output = (
            result.output
            if hasattr(result, "output")
            else str(result) if result else ""
        )
        output = output or ""
        exit_code = getattr(result, "exit_code", 0)
        # ``exit_code=None`` is valid for tools that do not launch a process, so
        # prefer the result's canonical success signal when available.
        succeeded = result.success if hasattr(result, "success") else exit_code == 0
        status = "OK" if succeeded else f"exit={exit_code}"
        preview = (
            result.get_text_output()[:5000]
            if hasattr(result, "get_text_output")
            else str(output)[:5000]
        )
        metadata = {
            "job_id": job_id,
            # Full output for the session event log (recoverable via search_memory);
            # preview stays for UI rendering.
            "output": output,
            "output_preview": preview,
            "exit_code": exit_code,
        }
        if not is_subagent and isinstance(output, list):
            metadata["result"] = content_parts_to_dicts(output)
        if not succeeded:
            metadata["error"] = f"Tool exited with code {exit_code}"
            metadata["final_state"] = "error"
        if is_subagent:
            result_metadata = getattr(result, "metadata", {})
            metadata["result"] = preview
            metadata["subagent"] = result_metadata.get("subagent", metric_name)
            metadata["llm_name"] = result_metadata.get("llm_name", "")
            metadata["model"] = result_metadata.get("model", "")
            metadata["turns"] = getattr(result, "turns", 0)
            metadata["duration"] = getattr(result, "duration", 0)
            metadata["total_tokens"] = getattr(result, "total_tokens", 0)
            metadata["prompt_tokens"] = getattr(result, "prompt_tokens", 0)
            metadata["completion_tokens"] = getattr(result, "completion_tokens", 0)
            metadata["cached_tokens"] = getattr(result, "cached_tokens", 0)
            metadata["tools_used"] = getattr(result, "metadata", {}).get(
                "tools_used", []
            )
        # Preserve preview metadata so event consumers can render modified files.
        tool_metadata = getattr(result, "metadata", None) or {}
        canvas_preview = tool_metadata.get("canvas_preview")
        if canvas_preview:
            metadata["canvas_preview"] = canvas_preview
        session_metadata = tool_metadata.get("session_metadata")
        if isinstance(session_metadata, dict):
            metadata["tool_metadata"] = dict(session_metadata)
        self.output_router.notify_activity(
            done_activity if succeeded else error_activity,
            f"[{label}] {status}",
            metadata=metadata,
        )
        _emit_completion_metrics(
            is_subagent,
            metric_name,
            "ok" if succeeded else "error",
            duration_ms,
        )

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
                continue  # Promoted jobs already have a protocol placeholder.

            result = results[job_id]
            tool_name, _ = _make_job_label(job_id)
            tool_call_id = tool_call_ids.get(job_id, job_id)
            if self._native_result_exists(
                controller.conversation, job_id, tool_call_id
            ):
                continue

            if isinstance(result, (Exception, asyncio.CancelledError)):
                interrupted = isinstance(result, asyncio.CancelledError)
                error_text = (
                    "User manually interrupted this job."
                    if interrupted
                    else str(result)
                )
                prefix = "Interrupted" if interrupted else "Error"
                content = f"{prefix}: {error_text}"
            elif result is not None and hasattr(result, "error") and result.error:
                output = render_content_text(result.output or "")
                interrupted = bool(getattr(result, "interrupted", False)) or bool(
                    (getattr(result, "metadata", {}) or {}).get("interrupted")
                )
                cancelled = bool(getattr(result, "cancelled", False)) or bool(
                    (getattr(result, "metadata", {}) or {}).get("cancelled")
                )
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
                "tool",
                content,
                tool_call_id=tool_call_id,
                name=tool_name,
                metadata={"job_id": job_id, "tool_result_phase": "final"},
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
                continue

            result = results[job_id]

            if isinstance(result, (Exception, asyncio.CancelledError)):
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
                output = render_content_text(
                    result.output if hasattr(result, "output") else str(result)
                )
                error = getattr(result, "error", None)
                if error:
                    interrupted = bool(getattr(result, "interrupted", False)) or bool(
                        (getattr(result, "metadata", {}) or {}).get("interrupted")
                    )
                    cancelled = bool(getattr(result, "cancelled", False)) or bool(
                        (getattr(result, "metadata", {}) or {}).get("cancelled")
                    )
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
