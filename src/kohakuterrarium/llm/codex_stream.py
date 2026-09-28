"""Disposable Codex response attempts on the shared WebSocket recovery path."""

from contextlib import aclosing
from typing import Any, AsyncIterator

from kohakuterrarium.llm.codex_format import (
    fix_tool_call_pairing,
    maybe_capture_stream_rate_limit,
)
from kohakuterrarium.llm.base import NativeToolCall
from kohakuterrarium.llm.codex_image_gen import build_image_part
from kohakuterrarium.llm.codex_rate_limits import (
    parse_rate_limit_event,
    UsageSnapshot,
    set_cached,
)
from kohakuterrarium.llm.openai_sanitize import strip_surrogates
from kohakuterrarium.llm.openai_ws import record_ws_assistant_echo
from kohakuterrarium.llm.responses_reasoning import ResponsesReasoningCollector
from kohakuterrarium.llm.responses_ws import ResponsesWSSession
from kohakuterrarium.llm.responses_ws_recovery import WSRecovery


async def stream_codex_ws_turn(
    provider: Any,
    session: ResponsesWSSession,
    base_event: dict,
    items: list[dict],
    echo_options: dict,
    recovery: WSRecovery,
) -> AsyncIterator[str]:
    """Publish result state only after the successful attempt completes."""
    calls = []
    text = []
    image_parts = []

    def reset_attempt() -> None:
        calls.clear()
        text.clear()
        image_parts.clear()
        provider._reasoning = ResponsesReasoningCollector()
        provider._last_usage = {}
        provider._last_tool_calls = []
        provider._last_assistant_parts = []
        provider._last_assistant_extra_fields = {}

    completed = False
    try:
        async with aclosing(
            session.stream_turn(
                base_event,
                items,
                fix_tool_call_pairing,
                recovery=recovery,
                reset_attempt=reset_attempt,
            )
        ) as stream:
            async for event in stream:
                piece = provider._process_stream_event(event, calls, image_parts)
                if piece is not None:
                    text.append(piece)
                    if piece:
                        recovery.delivered = True
                    yield piece
        provider._last_assistant_extra_fields = provider._reasoning.fields()
        provider._last_tool_calls = calls
        provider._last_assistant_parts = list(image_parts)
        record_ws_assistant_echo(session, provider, "".join(text), echo_options)
        completed = True
    finally:
        if not completed:
            reset_attempt()


def process_codex_event(
    provider: Any,
    event: Any,
    collected_tool_calls: list[NativeToolCall],
    image_parts: list | None = None,
) -> str | None:
    """Fold one Responses stream event into provider state; return text."""
    # Generic SDK events may carry fresher inline rate-limit payloads.
    maybe_capture_stream_rate_limit(
        event, parse_rate_limit_event, UsageSnapshot, set_cached
    )
    provider._reasoning.consume(event)

    match getattr(event, "type", ""):
        case "response.output_text.delta":
            piece = strip_surrogates(event.delta)
            provider._reasoning.consume_output_text(piece)
            return piece
        case "response.output_item.done":
            item = event.item
            itype = getattr(item, "type", "")
            if itype == "function_call":
                call_id = getattr(item, "call_id", "")
                provider._reasoning.consume_function_call(call_id)
                collected_tool_calls.append(
                    NativeToolCall(
                        id=call_id,
                        name=getattr(item, "name", "") or "",
                        arguments=getattr(item, "arguments", ""),
                    )
                )
            elif itype == "image_generation_call":
                # Image bytes are available before the item status completes.
                part = build_image_part(item, provider._image_gen_output_format)
                if part is not None:
                    target = (
                        provider._last_assistant_parts
                        if image_parts is None
                        else image_parts
                    )
                    target.append(part)
        case "response.completed":
            resp = getattr(event, "response", None)
            if resp:
                u = getattr(resp, "usage", None)
                if u:
                    cached = 0
                    details = getattr(u, "input_tokens_details", None)
                    if details:
                        cached = getattr(details, "cached_tokens", 0) or 0
                    provider._last_usage = {
                        "prompt_tokens": getattr(u, "input_tokens", 0),
                        "completion_tokens": getattr(u, "output_tokens", 0),
                        "total_tokens": getattr(u, "total_tokens", 0),
                        "cached_tokens": cached,
                    }
    return None
