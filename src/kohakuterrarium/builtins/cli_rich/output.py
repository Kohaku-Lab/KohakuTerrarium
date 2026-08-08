"""RichCLIOutput — translates agent output events to RichCLIApp callbacks.

The agent's output router calls into this module:
  - write_stream(chunk)            → app.on_text_chunk(chunk)
  - on_processing_start()          → app.on_processing_start()
  - on_processing_end()            → app.on_processing_end()
  - on_activity_with_metadata(...) → routed to tool/subagent callbacks
"""

from typing import Any

from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _make_label(job_id: str, name: str) -> str:
    """Build display name for a tool/sub-agent block."""
    if not job_id:
        return name
    short = job_id.rsplit("_", 1)[-1][:6] if "_" in job_id else job_id[:6]
    return f"{name}[{short}]"


class RichCLIOutput(BaseOutputModule):
    """Output module that routes agent events into RichCLIApp."""

    def __init__(self, app: Any, *, reply_router: Any = None):
        super().__init__()
        self.app = app
        self.reply_router = reply_router

    async def write(self, content: str) -> None:
        if not content or self.app is None:
            return
        try:
            self.app.on_text_chunk(content)
        except Exception as e:
            logger.exception("write failed", error=str(e))

    async def write_stream(self, chunk: str) -> None:
        if not chunk or self.app is None:
            return
        try:
            self.app.on_text_chunk(chunk)
        except Exception as e:
            logger.exception("write_stream failed", error=str(e))

    async def flush(self) -> None:
        pass

    async def on_processing_start(self) -> None:
        if self.app is None:
            return
        try:
            self.app.on_processing_start()
        except Exception as e:
            logger.exception("on_processing_start failed", error=str(e))

    async def on_processing_end(self) -> None:
        if self.app is None:
            return
        try:
            self.app.on_processing_end()
        except Exception as e:
            logger.exception("on_processing_end failed", error=str(e))

    async def on_user_input(self, text: str) -> None:
        # Composer submissions are already rendered by the app.
        pass

    def on_activity(self, activity_type: str, detail: str) -> None:
        self.on_activity_with_metadata(activity_type, detail, {})

    def on_activity_with_metadata(
        self, activity_type: str, detail: str, metadata: dict[str, Any]
    ) -> None:
        """Dispatch activity events to the appropriate app callback."""
        try:
            self._dispatch(activity_type, detail, metadata)
        except Exception as e:
            logger.exception(
                "Activity dispatch failed",
                activity_type=activity_type,
                error=str(e),
            )

    async def emit(self, event: OutputEvent) -> None:
        """Route output events to interactive overlays or scrollback panels."""
        match event.type:
            case "text":
                content = event.content
                if isinstance(content, str):
                    await self.write_stream(content)
            case "processing_start":
                await self.on_processing_start()
            case "processing_end":
                await self.on_processing_end()
            case "user_input":
                # Composer submissions are already rendered by the app.
                pass
            case "assistant_image":
                payload = event.payload
                self.on_assistant_image(
                    payload["url"],
                    detail=payload.get("detail", "auto"),
                    source_type=payload.get("source_type"),
                    source_name=payload.get("source_name"),
                    revised_prompt=payload.get("revised_prompt"),
                )
            case "resume_batch":
                await self.on_resume(event.payload.get("events", []))
            case "ask_text" | "confirm" | "selection":
                self._open_bus_overlay(event)
            case "progress":
                self._render_progress(event)
            case "notification":
                self._render_notification(event)
            case "card":
                # Only non-link actions require keyboard input from the overlay.
                actions = (event.payload or {}).get("actions") or []
                has_replyable_action = any(a.get("style") != "link" for a in actions)
                if has_replyable_action and event.interactive:
                    self._open_bus_overlay(event)
                else:
                    self._render_card(event)
            case "ui_supersede":
                # Committed scrollback cannot retract superseded panels.
                pass
            case _:
                detail = event.content if isinstance(event.content, str) else ""
                metadata = event.payload or {}
                try:
                    self._dispatch(event.type, detail, metadata)
                except Exception as e:
                    logger.exception(
                        "Activity dispatch failed",
                        activity_type=event.type,
                        error=str(e),
                    )

    def _open_bus_overlay(self, event: OutputEvent) -> None:
        """Queue an interactive event for keyboard input in the live region."""
        if self.app is None:
            return
        overlay = getattr(self.app, "bus_overlay", None)
        if overlay is None:
            # App variants without an overlay must still expose the event.
            handler = getattr(self.app, "on_ui_event_panel", None)
            if handler is not None:
                try:
                    handler(event_type=event.type, payload=dict(event.payload))
                except Exception as e:
                    logger.exception("CLI panel fallback failed", error=str(e))
            return
        try:
            overlay.open(event, router=self.reply_router)
            self.app._invalidate()
        except Exception as e:
            logger.exception(
                "CLI bus overlay open failed",
                event_type=event.type,
                error=str(e),
            )

    def _render_progress(self, event: OutputEvent) -> None:
        if self.app is None:
            return
        try:
            handler = getattr(self.app, "on_progress_event", None)
            if handler is None:
                return
            handler(
                event_id=event.id,
                update_target=event.update_target,
                payload=dict(event.payload),
            )
        except Exception as e:
            logger.exception("CLI progress render failed", error=str(e))

    def _render_notification(self, event: OutputEvent) -> None:
        if self.app is None:
            return
        try:
            handler = getattr(self.app, "on_notification_event", None)
            if handler is None:
                return
            handler(payload=dict(event.payload))
        except Exception as e:
            logger.exception("CLI notification render failed", error=str(e))

    def _render_card(self, event: OutputEvent) -> None:
        if self.app is None:
            return
        try:
            handler = getattr(self.app, "on_card_event", None)
            if handler is None:
                return
            handler(payload=dict(event.payload))
        except Exception as e:
            logger.exception("CLI card render failed", error=str(e))

    def _dispatch(
        self, activity_type: str, detail: str, metadata: dict[str, Any]
    ) -> None:
        job_id = metadata.get("job_id", "")
        name_from_label = self._extract_name(detail)
        args_preview = self._extract_args_preview(metadata)

        if activity_type == "command_result":
            self.app.on_notification_event(
                {
                    "title": self._command_name(metadata),
                    "text": detail,
                    "level": "info",
                }
            )
            return

        if activity_type == "command_error":
            self.app.on_processing_error(
                error_type=self._command_name(metadata),
                error=detail,
            )
            return

        # Nested tool events identify their parent sub-agent block by job_id.
        if activity_type == "subagent_tool_start":
            tool_name = metadata.get("tool", "") or name_from_label
            child_args = metadata.get("detail", "") or ""
            self.app.on_subagent_tool_start(
                parent_id=job_id,
                tool_name=tool_name,
                args_preview=child_args,
            )
            return

        if activity_type == "subagent_tool_done":
            tool_name = metadata.get("tool", "") or name_from_label
            output = metadata.get("detail", "") or ""
            self.app.on_subagent_tool_done(
                parent_id=job_id, tool_name=tool_name, output=output
            )
            return

        if activity_type == "subagent_tool_error":
            tool_name = metadata.get("tool", "") or name_from_label
            error_text = metadata.get("detail", "") or ""
            self.app.on_subagent_tool_error(
                parent_id=job_id, tool_name=tool_name, error=error_text
            )
            return

        if activity_type == "subagent_token_update":
            self.app.on_subagent_tokens(
                parent_id=job_id,
                prompt=metadata.get("prompt_tokens", 0) or 0,
                completion=metadata.get("completion_tokens", 0) or 0,
                total=metadata.get("total_tokens", 0) or 0,
            )
            return

        if activity_type == "tool_start":
            self.app.on_tool_start(
                job_id=job_id,
                name=name_from_label,
                args_preview=args_preview,
                kind="tool",
                parent_job_id=metadata.get("parent_job_id", ""),
                background=bool(metadata.get("background", False)),
            )
            return

        if activity_type == "subagent_start":
            task_text = metadata.get("task", "")[:80]
            self.app.on_tool_start(
                job_id=job_id,
                name=name_from_label,
                args_preview=task_text,
                kind="subagent",
                parent_job_id=metadata.get("parent_job_id", ""),
                background=bool(metadata.get("background", False)),
            )
            return

        if activity_type in ("tool_promoted", "task_promoted"):
            self.app.on_tool_promoted(job_id=job_id)
            return

        if activity_type == "job_cancelled":
            self.app.on_job_cancelled(
                job_id=job_id, job_name=metadata.get("job_name", "")
            )
            return

        if activity_type == "tool_done":
            output = (
                metadata.get("output_preview")
                or metadata.get("output")
                or metadata.get("result")
                or ""
            )
            self.app.on_tool_done(job_id=job_id, output=str(output))
            return

        if activity_type == "subagent_done":
            output = metadata.get("result") or metadata.get("output") or ""
            self.app.on_tool_done(
                job_id=job_id,
                output=str(output),
                tools_used=metadata.get("tools_used", []),
                turns=metadata.get("turns", 0),
                total_tokens=metadata.get("total_tokens", 0),
                prompt_tokens=metadata.get("prompt_tokens", 0),
                completion_tokens=metadata.get("completion_tokens", 0),
            )
            return

        if activity_type in ("tool_error", "subagent_error"):
            error_text = metadata.get("error") or detail
            self.app.on_tool_error(job_id=job_id, error=str(error_text))
            return

        if activity_type == "token_usage":
            # Cached input is reported separately from uncached prompt usage.
            prompt = metadata.get("prompt_tokens", 0)
            completion = metadata.get("completion_tokens", 0)
            max_ctx = metadata.get("max_context", 0)
            cached = metadata.get("cached_tokens", 0) or 0
            self.app.on_token_update(prompt, completion, max_ctx, cached=cached)
            return

        if activity_type == "compact_start":
            self.app.on_compact_start()
            return

        if activity_type in ("compact_complete", "compact_done", "compact_skipped"):
            self.app.on_compact_end()
            return

        if activity_type == "processing_error":
            error_type = metadata.get("error_type", "Error")
            error_msg = metadata.get("error", detail)
            self.app.on_processing_error(error_type=error_type, error=str(error_msg))
            return

        if activity_type == "interrupt":
            self.app.on_interrupt_notice(detail)
            return

        if activity_type == "background_result":
            labels = metadata.get("labels")
            label = (
                ", ".join(labels)
                if labels
                else (metadata.get("label") or metadata.get("job_id", ""))
            )
            kind = metadata.get("kind", "tool")
            count = metadata.get("count", 1)
            self.app.on_background_result(kind, label, count)
            return

        if activity_type == "session_info":
            # The canonical profile name keeps the footer consistent with /model.
            display_model = metadata.get("llm_name") or metadata.get("model", "")
            self.app.on_session_info(
                model=display_model,
                max_ctx=metadata.get("max_context", 0),
            )
            return

        if activity_type == "user_input_injected":
            # Move injected input from the pending indicator into the transcript.
            text = self._extract_injected_text(metadata.get("content", ""))
            if text:
                live_region = getattr(self.app, "live_region", None)
                if live_region is not None:
                    live_region.remove_queued_input(text)
                self.app._commit_user_message(text)
                invalidate = getattr(self.app, "_invalidate", None)
                if callable(invalidate):
                    invalidate()
            return

    @staticmethod
    def _extract_injected_text(content: Any) -> str:
        """Extract injected text from a string or structured content parts."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(p.get("text", ""))
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        return ""

    @staticmethod
    def _command_name(metadata: dict[str, Any]) -> str:
        """Return a compact slash-command label for command notices."""
        raw = str(metadata.get("command", "") or "").strip()
        if not raw:
            return "command"
        return raw.lstrip("/").split(None, 1)[0] or "command"

    @staticmethod
    def _extract_name(detail: str) -> str:
        """Extract the tool name from a label like '[bash[abc123]] arg=...'."""
        if detail.startswith("["):
            try:
                end = detail.index("] ", 1)
                inner = detail[1:end]
                if "[" in inner:
                    return inner[: inner.index("[")]
                return inner
            except ValueError:
                pass
        return detail.split()[0] if detail else ""

    @staticmethod
    def _extract_args_preview(metadata: dict[str, Any]) -> str:
        args = metadata.get("args") or {}
        if not isinstance(args, dict):
            return ""
        parts = []
        for k, v in args.items():
            if k.startswith("_"):
                continue
            parts.append(f"{k}={str(v)[:40]}")
        return " ".join(parts)[:80]
