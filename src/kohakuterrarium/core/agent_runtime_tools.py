import asyncio

from kohakuterrarium.core.backgroundify import BackgroundifyHandle
from kohakuterrarium.core.controller import Controller
from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.core.job_label import make_job_label
from kohakuterrarium.core.tool_output import render_content_text
from kohakuterrarium.llm.message import content_parts_to_dicts
from kohakuterrarium.parsing import CommandResultEvent, SubAgentCallEvent, ToolCallEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


_make_job_label = make_job_label


class AgentRuntimeToolsMixin:
    def _should_notify_controller_on_background_complete(self, job_id: str) -> bool:
        meta = self._direct_job_meta.get(job_id)
        if meta and "notify_controller_on_background_complete" in meta:
            return bool(meta["notify_controller_on_background_complete"])
        return bool(self._bg_controller_notify.get(job_id, True))

    def _notify_command_result(self, parse_event: CommandResultEvent) -> None:
        """Route command results to activity log (not user-facing output)."""
        activity = "command_error" if parse_event.error else "command_done"
        detail = (
            f"[{parse_event.command}] {parse_event.error}"
            if parse_event.error
            else f"[{parse_event.command}] OK"
        )
        self.output_router.notify_activity(activity, detail)

    def _notify_tool_start(
        self,
        parse_event: ToolCallEvent,
        job_id: str,
        is_direct: bool,
        *,
        tool_call_id: str | None = None,
    ) -> None:
        """Notify output of a tool start with a human-readable preview."""
        _, label = _make_job_label(job_id)
        full_args, arg_parts = {}, []
        for k, v in (parse_event.args or {}).items():
            if k.startswith("_"):
                continue
            full_args[k] = v
            arg_parts.append(f"{k}={str(v)[:40]}")
        bg_tag = " (bg)" if not is_direct else ""
        metadata = {
            "job_id": job_id,
            "tool_name": parse_event.name,
            "args": full_args,
            "background": not is_direct,
        }
        if tool_call_id:
            metadata.update(
                tool_call_id=tool_call_id, tool_call_arguments=parse_event.raw
            )
        self.output_router.notify_activity(
            "tool_start",
            f"[{label}]{bg_tag} {' '.join(arg_parts)[:80]}",
            metadata=metadata,
        )

    def _emit_token_usage(self, controller: Controller) -> None:
        """Emit and accumulate usage from the last LLM turn.

        The accumulated usage supports one turn-level event, while provider
        cache counts are emitted separately when available.
        """
        usage = getattr(controller, "_last_usage", {})
        if not usage:
            return
        self.output_router.notify_activity(
            "token_usage",
            f"tokens: {usage.get('prompt_tokens', 0)} in, "
            f"{usage.get('completion_tokens', 0)} out",
            metadata=usage,
        )
        accum = getattr(self, "_turn_usage_accum", None)
        if isinstance(accum, dict):
            for k in (
                "prompt_tokens",
                "completion_tokens",
                "cached_tokens",
                "total_tokens",
            ):
                accum[k] = accum.get(k, 0) + int(usage.get(k, 0) or 0)

        cache_write = int(usage.get("cache_creation_input_tokens", 0) or 0)
        cache_read = int(
            usage.get("cached_tokens", 0)
            or usage.get("cache_read_input_tokens", 0)
            or 0
        )
        if cache_write or cache_read:
            prompt = int(usage.get("prompt_tokens", 0) or 0)
            ratio = (cache_read / prompt) if prompt else 0.0
            self.output_router.notify_activity(
                "cache_stats",
                f"cache: r={cache_read} w={cache_write}",
                metadata={
                    "agent": getattr(self.config, "name", ""),
                    "cache_write": cache_write,
                    "cache_read": cache_read,
                    "cache_hit_ratio": ratio,
                },
            )

    def _cancel_handles(self, handles: dict[str, BackgroundifyHandle]) -> None:
        """Cancel all non-promoted handles (on interrupt)."""
        for job_id, handle in handles.items():
            if handle.promoted:
                logger.debug("Skipping promoted handle", job_id=job_id)
                continue
            if not handle.done:
                handle.task.cancel()
                logger.debug("Cancelled direct handle", job_id=job_id)

    def _reset_output_state(self) -> None:
        """Reset output router and default output for a new iteration."""
        self.output_router.reset()
        if hasattr(self.output_router.default_output, "reset"):
            self.output_router.default_output.reset()

    async def _flush_output(self) -> None:
        """Flush buffered output and reset default output."""
        await self.output_router.flush()
        if hasattr(self.output_router.default_output, "reset"):
            self.output_router.default_output.reset()

    async def _start_subagent_async(self, event: SubAgentCallEvent) -> tuple[str, bool]:
        """Start a sub-agent execution."""
        logger.info(
            "Starting sub-agent",
            subagent_type=event.name,
            task=event.args.get("task", "")[:50],
        )
        try:
            return await self.subagent_manager.spawn_from_event(event)
        except ValueError as e:
            logger.error(
                "Sub-agent not registered", subagent_name=event.name, error=str(e)
            )
            return f"error_{event.name}", True

    def _on_bg_complete(self, event: TriggerEvent) -> None:
        """Handle background tool/sub-agent completion."""
        if not self._running:
            return
        job_id = getattr(event, "job_id", "")
        is_subagent = job_id.startswith("agent_")
        context = event.context or {}
        error = context.get("error")
        exit_code = context.get("exit_code")
        try:
            failed_exit = exit_code is not None and int(exit_code) != 0
        except (TypeError, ValueError):
            failed_exit = False
        if failed_exit and not error:
            error = f"Tool exited with code {exit_code}"
        content = render_content_text(event.content)
        _, label = _make_job_label(job_id)
        activity_done, activity_error = (
            ("subagent_done", "subagent_error")
            if is_subagent
            else ("tool_done", "tool_error")
        )
        sa_meta = event.context.get("subagent_metadata", {}) if event.context else {}
        tools_used = sa_meta.get("tools_used", [])

        if error:
            interrupted = (
                bool(event.context.get("interrupted")) if event.context else False
            )
            cancelled = bool(event.context.get("cancelled")) if event.context else False
            final_state = (
                "interrupted" if interrupted else "cancelled" if cancelled else "error"
            )
            state_label = (
                "INTERRUPTED" if interrupted else "CANCELLED" if cancelled else "ERROR"
            )
            self.output_router.notify_activity(
                activity_error,
                f"[{label}] {state_label}: {error}",
                metadata={
                    "job_id": job_id,
                    "subagent": sa_meta.get("subagent", ""),
                    "llm_name": sa_meta.get("llm_name", ""),
                    "model": sa_meta.get("model", ""),
                    "error": error,
                    "exit_code": exit_code,
                    "interrupted": interrupted,
                    "cancelled": cancelled,
                    "final_state": final_state,
                    "result": content,
                    "tools_used": tools_used,
                    "turns": sa_meta.get("turns", 0),
                    "duration": sa_meta.get("duration", 0),
                    "total_tokens": sa_meta.get("total_tokens", 0),
                    "prompt_tokens": sa_meta.get("prompt_tokens", 0),
                    "completion_tokens": sa_meta.get("completion_tokens", 0),
                    "cached_tokens": sa_meta.get("cached_tokens", 0),
                },
            )
        elif is_subagent:
            tools_summary = ", ".join(tools_used[:10]) if tools_used else "none"
            self.output_router.notify_activity(
                activity_done,
                f"[{label}] tools: {tools_summary}",
                metadata={
                    "job_id": job_id,
                    "subagent": sa_meta.get("subagent", ""),
                    "llm_name": sa_meta.get("llm_name", ""),
                    "model": sa_meta.get("model", ""),
                    "tools_used": tools_used,
                    "result": content,
                    "turns": sa_meta.get("turns", 0),
                    "duration": sa_meta.get("duration", 0),
                    "total_tokens": sa_meta.get("total_tokens", 0),
                    "prompt_tokens": sa_meta.get("prompt_tokens", 0),
                    "completion_tokens": sa_meta.get("completion_tokens", 0),
                    "cached_tokens": sa_meta.get("cached_tokens", 0),
                },
            )
        else:
            result_metadata = context.get("result_metadata", {})
            structured_result = (
                content_parts_to_dicts(event.content)
                if isinstance(event.content, list)
                else content
            )
            metadata = {
                "job_id": job_id,
                "result": structured_result,
                "output": event.content,
                "output_preview": content[:5000],
                "exit_code": exit_code,
            }
            session_metadata = (
                result_metadata.get("session_metadata")
                if isinstance(result_metadata, dict)
                else None
            )
            if isinstance(session_metadata, dict):
                metadata["tool_metadata"] = dict(session_metadata)
            self.output_router.notify_activity(
                activity_done,
                f"[{label}] DONE",
                metadata=metadata,
            )

        logger.info("Background job completed", job_id=job_id)
        if self._should_notify_controller_on_background_complete(job_id):
            asyncio.create_task(self._process_event(event))
        else:
            self._bg_controller_notify.pop(job_id, None)
