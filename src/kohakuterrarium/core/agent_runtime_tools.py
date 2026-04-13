import asyncio

from kohakuterrarium.core.backgroundify import BackgroundifyHandle
from kohakuterrarium.core.controller import Controller
from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.parsing import CommandResultEvent, SubAgentCallEvent, ToolCallEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _make_job_label(job_id: str) -> tuple[str, str]:
    """Extract (tool_name, label) from a job_id."""
    tool_name = job_id.rsplit("_", 1)[0] if "_" in job_id else job_id
    short_id = job_id.rsplit("_", 1)[-1][:6] if "_" in job_id else ""
    label = f"{tool_name}[{short_id}]" if short_id else tool_name
    return tool_name, label


class AgentRuntimeToolsMixin:
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
        self, parse_event: ToolCallEvent, job_id: str, is_direct: bool
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
        self.output_router.notify_activity(
            "tool_start",
            f"[{label}]{bg_tag} {' '.join(arg_parts)[:80]}",
            metadata={"job_id": job_id, "args": full_args, "background": not is_direct},
        )

    def _emit_token_usage(self, controller: Controller) -> None:
        """Emit token usage from the last LLM turn to output."""
        usage = getattr(controller, "_last_usage", {})
        if usage:
            self.output_router.notify_activity(
                "token_usage",
                f"tokens: {usage.get('prompt_tokens', 0)} in, "
                f"{usage.get('completion_tokens', 0)} out",
                metadata=usage,
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
        error = event.context.get("error") if event.context else None
        content = (
            event.content if isinstance(event.content, str) else str(event.content)
        )
        _, label = _make_job_label(job_id)
        activity_done, activity_error = (
            ("subagent_done", "subagent_error")
            if is_subagent
            else ("tool_done", "tool_error")
        )
        sa_meta = event.context.get("subagent_metadata", {}) if event.context else {}
        tools_used = sa_meta.get("tools_used", [])

        if error:
            self.output_router.notify_activity(
                activity_error,
                f"[{label}] ERROR: {error}",
                metadata={"job_id": job_id},
            )
        elif is_subagent:
            tools_summary = ", ".join(tools_used[:10]) if tools_used else "none"
            self.output_router.notify_activity(
                activity_done,
                f"[{label}] tools: {tools_summary}",
                metadata={
                    "job_id": job_id,
                    "tools_used": tools_used,
                    "result": content,
                    "turns": sa_meta.get("turns", 0),
                    "duration": sa_meta.get("duration", 0),
                    "total_tokens": sa_meta.get("total_tokens", 0),
                    "prompt_tokens": sa_meta.get("prompt_tokens", 0),
                    "completion_tokens": sa_meta.get("completion_tokens", 0),
                },
            )
        else:
            self.output_router.notify_activity(
                activity_done,
                f"[{label}] DONE",
                metadata={"job_id": job_id, "result": content},
            )

        logger.info("Background job completed", job_id=job_id)
        asyncio.create_task(self._process_event(event))
