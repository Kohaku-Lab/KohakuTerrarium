"""Formatting of trigger events into provider-facing user context."""

import re

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.core.tool_output import render_content_text
from kohakuterrarium.llm.message import ContentPart, FilePart, ImagePart, TextPart

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ERROR_SUMMARY_LIMIT = 512


def _summarize_tool_error(error: str) -> str:
    """Return a bounded diagnostic without provider-irrelevant traceback frames."""
    cleaned = _ANSI_ESCAPE_RE.sub("", error).strip()
    if "Traceback (most recent call last):" in cleaned:
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        cleaned = lines[-1] if lines else ""
    else:
        cleaned = " ".join(cleaned.split())
    if len(cleaned) <= _ERROR_SUMMARY_LIMIT:
        return cleaned
    return cleaned[: _ERROR_SUMMARY_LIMIT - 1] + "…"


def _tool_completion_prefix(event: TriggerEvent) -> str:
    """Describe the actionable completion state of one tool event."""
    context = event.context or {}
    final_state = str(context.get("final_state", "")).lower()
    cancelled = (
        bool(context.get("cancelled"))
        or bool(context.get("interrupted"))
        or final_state in {"cancelled", "interrupted"}
    )
    if cancelled:
        return f"[Tool {event.job_id} cancelled]"

    exit_code = context.get("exit_code")
    error = context.get("error")
    if error or (exit_code is not None and exit_code != 0):
        exit_detail = f", exit {exit_code}" if exit_code not in (None, 0) else ""
        prefix = f"[Tool {event.job_id} failed{exit_detail}]"
        if error:
            summary = _summarize_tool_error(str(error))
            if summary:
                prefix = f"{prefix}\nError: {summary}"
        return prefix

    return f"[Tool {event.job_id} completed]"


def format_events_for_context(
    events: list[TriggerEvent],
) -> "str | list[ContentPart]":
    """Format events as text or multimodal user message content."""
    text_parts: list[str] = []
    image_parts: list[ImagePart] = []
    file_parts: list[FilePart] = []

    def append_multimodal(prefix: str, parts: list[ContentPart]) -> None:
        part_text = render_content_text(parts)
        text_parts.append(f"{prefix}\n{part_text}" if part_text else prefix)
        for part in parts:
            if isinstance(part, ImagePart):
                image_parts.append(part)
            elif isinstance(part, FilePart):
                file_parts.append(part)

    for event in events:
        if event.type == "user_input":
            if isinstance(event.content, list):
                for part in event.content:
                    if isinstance(part, TextPart):
                        text_parts.append(part.text)
                    elif isinstance(part, ImagePart):
                        image_parts.append(part)
                    elif isinstance(part, FilePart):
                        file_parts.append(part)
            elif isinstance(event.content, str):
                text_parts.append(event.content)
        elif event.type == "tool_complete":
            if not event.job_id and not event.get_text_content():
                # Internal wakes only resume processing; their results already
                # exist in conversation and must not become fabricated tool output.
                continue
            prefix = _tool_completion_prefix(event)
            if isinstance(event.content, list):
                append_multimodal(prefix, event.content)
            else:
                content_text = event.get_text_content()
                text_parts.append(
                    f"{prefix}\n{content_text}" if content_text else prefix
                )
        elif event.type == "subagent_output":
            content_text = event.get_text_content()
            text_parts.append(f"[Sub-agent {event.job_id} output]\n{content_text}")
        elif event.prompt_override:
            if isinstance(event.content, list):
                append_multimodal(event.prompt_override, event.content)
            else:
                text_parts.append(event.prompt_override)
        else:
            content_text = event.get_text_content()
            text_parts.append(f"[{event.type}] {content_text}")

    combined_text = "\n\n".join(text_parts)

    if image_parts or file_parts:
        result: list[ContentPart] = [TextPart(text=combined_text)]
        result.extend(image_parts)
        result.extend(file_parts)
        return result

    return combined_text
