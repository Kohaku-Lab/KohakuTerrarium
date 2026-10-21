"""Formatting of trigger events into provider-facing user context."""

import re
from typing import Any

from kohakuterrarium.core.conversation_elide import TOOL_FEEDBACK_KIND
from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.core.tool_output import render_content_text
from kohakuterrarium.llm.message import (
    ContentPart,
    FilePart,
    ImagePart,
    TextPart,
    normalize_content_parts,
)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ERROR_SUMMARY_LIMIT = 512
JOB_CONTEXT_METADATA_KEY = "job_context"


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


def tool_feedback_metadata(events: list[TriggerEvent]) -> dict:
    """Identify the completed jobs represented by a pure tool-feedback message."""
    return {
        "kind": TOOL_FEEDBACK_KIND,
        "job_ids": [event.job_id for event in events if event.job_id],
        "tool_result_phase": "final",
    }


def message_origin_metadata(controller: Any) -> dict:
    """Consume the branch provenance declared for one pushed batch.

    The controller has no branch state of its own, so the agent declares the
    ``(turn, branch)`` a user message is written on before pushing a batch.
    The value is taken once: an undeclared origin stays absent, never
    guessed, and a later message in the same push gets none.
    """
    origin = getattr(controller, "_message_origin", None)
    controller._message_origin = None
    return dict(origin) if isinstance(origin, dict) else {}


def declare_message_origin(controller: Any, origin: dict[str, Any] | None) -> None:
    """Record the branch the next user message will be written on."""
    controller._message_origin = dict(origin) if origin else None


def format_events_for_context(
    events: list[TriggerEvent],
) -> "str | list[ContentPart]":
    """Format events as text or multimodal user message content."""
    parts: list[ContentPart] = []

    def append_multimodal(prefix: str, content: list[ContentPart]) -> None:
        part_text = render_content_text(content)
        parts.append(TextPart(text=f"{prefix}\n{part_text}" if part_text else prefix))
        parts.extend(
            part for part in content if isinstance(part, (ImagePart, FilePart))
        )

    for event in events:
        if event.type == "user_input":
            if isinstance(event.content, list):
                parts.extend(normalize_content_parts(event.content))
            elif isinstance(event.content, str):
                parts.append(TextPart(text=event.content))
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
                parts.append(
                    TextPart(
                        text=f"{prefix}\n{content_text}" if content_text else prefix
                    )
                )
        elif event.type == "subagent_output":
            content_text = event.get_text_content()
            parts.append(
                TextPart(text=f"[Sub-agent {event.job_id} output]\n{content_text}")
            )
        elif event.prompt_override:
            if isinstance(event.content, list):
                append_multimodal(event.prompt_override, event.content)
            else:
                parts.append(TextPart(text=event.prompt_override))
        else:
            content_text = event.get_text_content()
            parts.append(TextPart(text=f"[{event.type}] {content_text}"))

    if any(
        isinstance(event.content, list) and event.type == "user_input"
        for event in events
    ) or any(isinstance(part, (ImagePart, FilePart)) for part in parts):
        return parts
    return "\n\n".join(part.text for part in parts if isinstance(part, TextPart))


def prepend_job_context(
    content: str | list[ContentPart | dict], job: str
) -> str | list[ContentPart | dict]:
    """Prepend the job-status text, preserving the shape of ``content``.

    ``content`` may be a string, typed content parts (the live conversation
    shape) or serialized part dicts (the persisted/wire shape). The returned
    value keeps that representation so the producer can store it and the
    binder can compare it without re-normalizing.
    """
    if not job:
        return content
    if isinstance(content, str):
        return f"{job}\n\n{content}"
    if content and all(isinstance(part, dict) for part in content):
        return [{"type": "text", "text": job}, *content]
    return [TextPart(text=job), *content]


def recorded_content_matches(
    metadata: Any, live_content: Any, recorded_content: Any
) -> bool:
    """Whether a recorded raw input restates this live user message.

    The live content may carry a job-status banner that the raw persisted
    input never stored; the recorded content is re-decorated with the exact
    banner captured on the message before comparing the full value.
    """
    job = metadata.get(JOB_CONTEXT_METADATA_KEY, "")
    if not isinstance(job, str) or not isinstance(recorded_content, (str, list)):
        return False
    decorated = prepend_job_context(recorded_content, job)
    if isinstance(decorated, list):
        decorated = [p if isinstance(p, dict) else p.to_dict() for p in decorated]
    return decorated == live_content
