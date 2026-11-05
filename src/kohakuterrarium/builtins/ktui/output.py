"""KT output module that feeds one creature's events to ktui.

Each creature attached to the shell gets one :class:`KtuiOutput` as its default output. Chat
events become :class:`CreatureView` calls; interactive events (``confirm``, ``ask_text``,
``selection``, replyable ``card``) go to the shell's shared :class:`Interactions` queue with the
router that waits for their reply. Assistant text goes through ``inline_ui``, which draws
``<ui>`` blocks; the class reports the ``kt.ui`` renderer so the model is taught them.
"""

import time
from typing import TYPE_CHECKING, Any

from kohakuterrarium.builtins.ktui.format import (
    args_preview,
    duration,
    one_line,
    parse_detail,
)
from kohakuterrarium.builtins.ktui.replay import replay
from kohakuterrarium.builtins.ktui.view import CreatureView, Job
from kohakuterrarium.llm.message import content_display_text
from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.output.widgets.legacy import render_widget_as_legacy
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

logger = get_logger(__name__)

INTERACTIVE = {"confirm", "ask_text", "selection"}
LEVEL_TONE = {"error": "error", "warning": "warning", "success": "success"}


class KtuiOutput(BaseOutputModule):
    """The default output of one creature while ktui runs."""

    def __init__(self, shell: "KtuiShell", view: CreatureView):
        super().__init__()
        self.shell = shell
        self.view = view
        self._router: Any = None

    # -- base contract --------------------------------------------------

    def ui_renderers(self) -> dict[str, int]:
        """``kt.ui`` (inline ``<ui>`` blocks, drawn by ``inline_ui``); no ``kt.app``: ktui runs
        no HTML apps, so the model is not offered ``show_app`` or ``html`` blocks."""
        return {"kt.ui": 1}

    async def write(self, content: str) -> None:
        if content:
            self.view.render("text", content)

    async def write_stream(self, chunk: str) -> None:
        if chunk:
            self.view.render("text", chunk)

    async def on_processing_start(self) -> None:
        self.view.processing = True
        self.view.render("status_on")
        self.shell.refresh()

    async def on_processing_end(self) -> None:
        self.view.processing = False
        self.view.render("status_off")
        # direct calls finish inside the turn, so a cell still open now never will
        self.view.render("close_open", "no result")
        self.shell.refresh()
        self.shell.turn_finished(self.view)

    async def on_widget(self, event: OutputEvent) -> None:
        """kt.ui widgets a tool opened are drawn by ``tool_widgets``; the legacy kinds (cards,
        questions, selections) come back through ``emit`` as their original events."""
        if self.shell.tool_widgets.handles(event):
            self.shell.tool_widgets.handle(event, self._router, self.view.ui, self)
            self.shell.refresh()
            return
        await render_widget_as_legacy(self, event)

    def on_widget_close(self, event: OutputEvent) -> None:
        """A legacy widget closed with no legacy event: answered elsewhere or timed out."""
        self.shell.interactions.supersede(event.update_target or event.id)

    def on_supersede(self, event_id: str | None) -> None:
        """The router's close signal: answered elsewhere, timed out or cancelled."""
        self.shell.interactions.supersede(event_id)

    async def on_user_input(self, text: str) -> None:
        self.view.render("user_message", text, None)

    async def on_resume(self, events: list[dict]) -> None:
        replay(self.view, events)
        self.shell.refresh()

    def on_assistant_image(self, url: str, **_: Any) -> None:
        self.view.render("notice", f"🖼 image · {one_line(url, 90)}", "accent", None)

    async def emit(self, event: OutputEvent) -> None:
        payload = event.payload or {}
        match event.type:
            case "user_input":
                text = content_display_text(event.content)
                self.view.render("user_message", text, payload.get("pending_id"))
                self.shell.refresh()
            case kind if kind in INTERACTIVE:
                self.shell.interactions.add(event, self._router, self.view)
            case "card" if event.interactive and _replyable(payload):
                self.shell.interactions.add(event, self._router, self.view)
            case "card":
                self.view.render("card", payload.get("title", ""), _card_body(payload))
            case "progress":
                self._progress(event, payload)
            case "notification":
                level = payload.get("level", "info")
                self.shell.app.notify(
                    one_line(payload.get("text", ""), 120),
                    LEVEL_TONE.get(level, "info"),
                )
            case "ui_supersede":
                self.shell.interactions.supersede(payload.get("event_id") or event.id)
            case _:
                await super().emit(event)

    def on_activity(self, activity_type: str, detail: str) -> None:
        self.on_activity_with_metadata(activity_type, detail, {})

    def on_activity_with_metadata(
        self, activity_type: str, detail: str, metadata: dict
    ) -> None:
        try:
            self._activity(activity_type, detail or "", metadata or {})
        except Exception as exc:  # a rendering bug must never break the agent
            logger.warning(
                "ktui activity render failed",
                activity=activity_type,
                error=str(exc),
                exc_info=True,
            )

    # -- activity -------------------------------------------------------

    def _activity(self, kind: str, detail: str, md: dict) -> None:
        view = self.view
        name, rest = parse_detail(detail)
        job_id = str(md.get("job_id", "") or name)
        match kind:
            case "tool_start":
                self._job_start(
                    job_id, name, "tool", args_preview(name, md.get("args")), md
                )
            case "tool_done" | "tool_error":
                error = rest if kind == "tool_error" else ""
                output = str(md.get("output_preview") or md.get("output") or rest)
                job = view.jobs.pop(job_id, None)
                view.render("tool_end", job_id, name, output, error, _took(job))
            case "subagent_start":
                self._job_start(job_id, name, "subagent", str(md.get("task", rest)), md)
            case "subagent_done" | "subagent_error":
                error = rest if kind == "subagent_error" else ""
                view.jobs.pop(job_id, None)
                result = str(md.get("result", rest) or "")
                view.traces.end(job_id, result, error, _stats(md))
                view.render("subagent_end", job_id, name, result, error, _stats(md))
            case s if s.startswith("subagent_tool_"):
                activity = s.removeprefix("subagent_tool_")
                tool = str(md.get("tool", ""))
                sub_detail = args_preview(tool, md.get("args")) or one_line(
                    md.get("detail", rest), 60
                )
                view.traces.tool(job_id, activity, tool, sub_detail)
                view.render("subagent_tool", job_id, activity, tool, sub_detail)
            case "job_cancelled":
                # KT follows up with tool_error / subagent_error; the view drops those
                job = view.jobs.pop(job_id, None)
                label = md.get("job_name") or (job.name if job else job_id)
                kind = job.kind if job else "tool"
                view.traces.cancelled(job_id)
                view.render("cancelled", job_id, str(label), kind, _took(job))
            case "task_promoted":
                job = view.jobs.get(job_id)
                if job is not None:
                    job.background = True
                view.traces.promoted(job_id)
                view.render("promote", job_id, job.name if job else (name or job_id))
            case "background_result":
                label = str(md.get("label") or job_id).split("[", 1)[0]
                view.jobs.pop(job_id, None)
                view.render(
                    "notice", f"↙ background result delivered · {label}", "accent", None
                )
            case "token_usage":
                self._usage(md)
            case "session_info":
                view.usage.model = (
                    md.get("llm_name") or md.get("model") or view.usage.model
                )
                view.usage.max_context = int(
                    md.get("max_context") or view.usage.max_context
                )
            case "compact_start":
                view.render("compact_start", int(md.get("round", 0) or 0))
            case "compact_complete" | "compact_skipped":
                skipped = (
                    (md.get("reason") or detail or "skipped")
                    if kind == "compact_skipped"
                    else ""
                )
                view.render(
                    "compact_end",
                    int(md.get("round", 0) or 0),
                    str(md.get("summary", "") or ""),
                    skipped,
                )
            case "context_cleared":
                view.render(
                    "notice",
                    f"context cleared · {md.get('messages_cleared', 0)} messages",
                    "accent",
                    None,
                )
            case "trigger_fired":
                self._trigger(name, md)
            case "drive_turn":
                label = f"⟳ drive turn · {md.get('drive_kind') or 'drive'} {md.get('drive_id', '')}"
                view.render(
                    "notice",
                    f"{label} · {one_line(md.get('objective', ''), 60)}",
                    "attention",
                    None,
                )
            case "processing_error":
                error = md.get("error", rest)
                view.render("error", f"{md.get('error_type', 'Error')}: {error}")
            case "interrupt":
                view.traces.interrupted()
                view.render("close_open", "interrupted")
                view.render("notice", "○ interrupted", "attention", None)
            case "command_result":
                view.render("markdown_notice", f"/{_command(md)}", detail, False)
            case "command_error":
                view.render("error", f"/{_command(md)}: {detail}")
            case "btw_started":
                view.render("notice", f"btw · {one_line(detail, 90)}", "accent", None)
            case "btw_result":
                view.render(
                    "markdown_notice", "btw", str(md.get("error") or detail), False
                )
            case "widget":
                view.render("notice", one_line(detail, 160), "muted", None)
            case "user_input_injected":
                content = content_display_text(md.get("content", ""))
                view.render("user_message", content, md.get("pending_id"))
            case "model_recovery":
                view.render(
                    "notice",
                    f"model recovered · {one_line(detail, 90)}",
                    "attention",
                    None,
                )
            case _:
                logger.debug("ktui ignores activity", activity=kind)
        self.shell.refresh()

    def _job_start(
        self, job_id: str, name: str, kind: str, detail: str, md: dict
    ) -> None:
        background = bool(md.get("background", False))
        self.view.jobs[job_id] = Job(job_id, name, kind, detail, background)
        if kind == "subagent":
            self.view.traces.start(job_id, name, detail, background)
        method = "tool_start" if kind == "tool" else "subagent_start"
        self.view.render(method, job_id, name, detail, background)

    def _usage(self, md: dict) -> None:
        usage = self.view.usage
        prompt = int(md.get("prompt_tokens", 0) or 0)
        usage.tokens_in += prompt
        usage.tokens_out += int(md.get("completion_tokens", 0) or 0)
        usage.cached += int(md.get("cached_tokens", 0) or 0)
        if prompt:
            usage.last_prompt = prompt

    def _trigger(self, name: str, md: dict) -> None:
        channel = md.get("channel", "")
        sender = md.get("sender", "")
        content = one_line(content_display_text(md.get("content", "")), 100)
        source = f"#{channel} · {sender}" if channel else (name or "trigger")
        self.view.render("notice", f"⚡ {source}: {content}", "attention", None)

    def _progress(self, event: OutputEvent, payload: dict) -> None:
        key = str(event.update_target or event.id or payload.get("label", "progress"))
        done = bool(payload.get("complete", False))
        value = None if payload.get("indeterminate") else payload.get("value")
        label = str(payload.get("label", "progress"))
        self.view.render("progress_update", key, label, value, payload.get("max"), done)


def _replyable(payload: dict) -> bool:
    return any(a.get("style") != "link" for a in payload.get("actions") or [])


def _card_body(payload: dict) -> str:
    body = str(payload.get("body") or payload.get("text") or "")
    links = [a for a in payload.get("actions") or [] if a.get("url")]
    for link in links:
        body += f"\n\n[{link.get('label', link['url'])}]({link['url']})"
    return body


def _took(job: Job | None) -> float:
    return time.monotonic() - job.started if job is not None else 0.0


def _stats(md: dict) -> str:
    parts = []
    if md.get("turns"):
        parts.append(f"{md['turns']} turns")
    tools = md.get("tools_used")
    if tools:
        parts.append(f"{len(tools)} tools")
    if md.get("duration"):
        parts.append(duration(md["duration"]))
    return " · ".join(parts) or "done"


def _command(md: dict) -> str:
    raw = str(md.get("command", "") or "").strip().lstrip("/")
    return raw.split(None, 1)[0] if raw else "command"
