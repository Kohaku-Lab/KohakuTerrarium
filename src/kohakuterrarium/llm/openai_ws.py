"""Responses-API WebSocket turn driver for the OpenAI-compatible provider.

Translates Chat-Completions-shaped conversations into Responses input items
and drives a persistent WebSocket session; the provider falls back to the
HTTP Chat Completions path when a turn cannot start over the socket.
"""

from contextlib import aclosing
from copy import deepcopy
from typing import Any, AsyncIterator

from kohakuterrarium.llm.base import NativeToolCall, ToolSchema
from kohakuterrarium.llm.codex_format import fix_tool_call_pairing, to_responses_input
from kohakuterrarium.llm.openai_sanitize import strip_kt_extras, strip_surrogates
from kohakuterrarium.llm.responses_reasoning import ResponsesReasoningCollector
from kohakuterrarium.llm.responses_ws import ResponsesWSSession
from kohakuterrarium.llm.responses_ws_options import FRAMEWORK_KNOBS


def build_ws_request(
    provider: Any,
    messages: list[dict[str, Any]],
    tools: list[ToolSchema] | None,
    kwargs: dict[str, Any],
    *,
    extra_body: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build the ``response.create`` base body and the full input item list."""
    instructions = ""
    input_messages: list[dict[str, Any]] = []
    for msg in strip_kt_extras(messages):
        if msg.get("role") == "system":
            instructions = msg.get("content", "")
        else:
            input_messages.append(msg)
    model = kwargs.get("model", provider.config.model)
    merged_extra = (
        extra_body
        if extra_body is not None
        else {**provider.extra_body, **(kwargs.get("extra_body") or {})}
    )
    items = to_responses_input(
        input_messages,
        model=model,
        replay_reasoning=merged_extra.get("responses_reasoning_replay"),
    )

    event: dict[str, Any] = {
        "model": model,
        "store": False,
    }
    if instructions:
        event["instructions"] = instructions
    if tools:
        event["tools"] = [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in tools
        ]
    temperature = kwargs.get("temperature", provider.config.temperature)
    if temperature is not None:
        event["temperature"] = temperature
    max_tokens = kwargs.get("max_tokens", provider.config.max_tokens)
    if max_tokens is not None:
        event["max_output_tokens"] = max_tokens
    if provider.prompt_cache_key:
        event["prompt_cache_key"] = provider.prompt_cache_key

    for key, value in merged_extra.items():
        if key in FRAMEWORK_KNOBS:
            continue
        if key == "reasoning" and isinstance(value, dict):
            # ``enabled`` is OpenRouter's unified knob; the Responses API
            # scale is effort/mode and rejects unknown reasoning fields.
            value = {k: v for k, v in value.items() if k != "enabled"}
            if not value:
                continue
        event[key] = value
    return event, items


def record_ws_assistant_echo(
    session: ResponsesWSSession,
    provider: Any,
    text: str,
    kwargs: dict[str, Any],
) -> None:
    """Record the exact assistant projection shared by Responses providers."""
    assistant = {
        "role": "assistant",
        "content": text,
        **provider.last_assistant_extra_fields,
    }
    assistant["tool_calls"] = [
        {
            "id": c.id,
            "type": "function",
            "function": {
                "name": c.name,
                "arguments": c.arguments,
            },
        }
        for c in provider.last_tool_calls
    ]
    _, items = build_ws_request(
        provider, [assistant], None, kwargs, extra_body=kwargs["extra_body"]
    )
    session.record_assistant_echo(items)


async def stream_ws_turn(
    provider: Any,
    session: ResponsesWSSession,
    messages: list[dict[str, Any]],
    tools: list[ToolSchema] | None,
    kwargs: dict[str, Any],
) -> AsyncIterator[str]:
    """Run one WebSocket turn, folding results into the provider state."""
    model = kwargs.get("model", provider.config.model)
    extra_body = deepcopy({**provider.extra_body, **(kwargs.get("extra_body") or {})})
    base_event, items = build_ws_request(
        provider, messages, tools, kwargs, extra_body=extra_body
    )
    collected: list[NativeToolCall] = []
    output_text: list[str] = []
    reasoning = ResponsesReasoningCollector()
    async with aclosing(
        session.stream_turn(base_event, items, fix_tool_call_pairing)
    ) as stream:
        async for event in stream:
            reasoning.consume(event)
            etype = getattr(event, "type", "")
            if etype == "response.output_text.delta":
                piece = strip_surrogates(event.delta)
                output_text.append(piece)
                reasoning.consume_output_text(piece)
                yield piece
            elif etype == "response.output_item.done":
                item = event.item
                if getattr(item, "type", "") == "function_call":
                    call_id = getattr(item, "call_id", "")
                    reasoning.consume_function_call(call_id)
                    collected.append(
                        NativeToolCall(
                            id=call_id,
                            name=getattr(item, "name", "") or "",
                            arguments=getattr(item, "arguments", ""),
                        )
                    )
            elif etype == "response.completed":
                usage = getattr(getattr(event, "response", None), "usage", None)
                if usage:
                    details = getattr(usage, "input_tokens_details", None)
                    cached = getattr(details, "cached_tokens", 0) or 0 if details else 0
                    provider._last_usage = {
                        "prompt_tokens": getattr(usage, "input_tokens", 0),
                        "completion_tokens": getattr(usage, "output_tokens", 0),
                        "total_tokens": getattr(usage, "total_tokens", 0),
                        "cached_tokens": cached,
                    }
    provider._last_tool_calls = collected
    provider._last_assistant_extra_fields = reasoning.fields()
    record_ws_assistant_echo(
        session,
        provider,
        "".join(output_text),
        {"model": model, "extra_body": extra_body},
    )
