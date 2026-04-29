"""Helper functions for OpenAI-compatible providers."""

from typing import Any

from kohakuterrarium.llm.base import NativeToolCall
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def extract_usage(usage: Any) -> dict[str, int]:
    """Extract KT's standard token-usage dict from SDK usage objects."""
    if not usage:
        return {}
    cached = 0
    cache_write = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details:
        cached = getattr(details, "cached_tokens", 0) or 0
        cache_write = getattr(details, "cache_write_tokens", 0) or 0
    return {
        "prompt_tokens": usage.prompt_tokens or 0,
        "completion_tokens": usage.completion_tokens or 0,
        "total_tokens": usage.total_tokens or 0,
        "cached_tokens": cached,
        "cache_write_tokens": cache_write,
    }


def delta_field(obj: Any, name: str) -> Any:
    """Fetch provider-specific fields off SDK objects or test dicts."""
    extra = getattr(obj, "model_extra", None)
    if isinstance(extra, dict) and name in extra:
        return extra[name]
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def pack_reasoning_fields(
    text: str, details: list[Any], extra: dict[str, Any]
) -> dict[str, Any]:
    """Assemble captured reasoning fields into one extras dict."""
    packed: dict[str, Any] = {}
    if text:
        packed["reasoning_content"] = text
    if details:
        packed["reasoning_details"] = details
    for k, v in (extra or {}).items():
        if v:
            packed[k] = v
    return packed


def tool_call_from_pending(call: dict[str, str]) -> NativeToolCall:
    """Convert a streaming pending-call accumulator to NativeToolCall."""
    return NativeToolCall(
        id=call["id"],
        name=call["name"],
        arguments=call["arguments"],
    )


def tool_calls_from_message(tool_calls: Any) -> list[NativeToolCall]:
    """Convert SDK message tool calls into KT NativeToolCall objects."""
    return [
        NativeToolCall(
            id=tc.id,
            name=tc.function.name,
            arguments=tc.function.arguments,
        )
        for tc in tool_calls or []
    ]


def log_token_usage(usage: dict[str, int]) -> None:
    if usage:
        logger.info(
            "Token usage",
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )
