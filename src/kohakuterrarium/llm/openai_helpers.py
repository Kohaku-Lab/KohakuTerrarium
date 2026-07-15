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


def delta_field_present(obj: Any, name: str) -> bool:
    """Detect explicit field presence, including empty stateful reasoning values."""
    extra = getattr(obj, "model_extra", None)
    if isinstance(extra, dict) and name in extra:
        return True
    if isinstance(obj, dict):
        return name in obj
    fields_set = getattr(obj, "model_fields_set", None)
    if fields_set is not None and name in fields_set:
        return True
    fields_set = getattr(obj, "__fields_set__", None)
    if fields_set is not None and name in fields_set:
        return True
    return getattr(obj, name, None) is not None


def merge_reasoning_detail_stream(
    accumulator: list[dict[str, Any]],
    piece: dict[str, Any],
) -> None:
    """Merge streamed reasoning fragments by type and index for lossless replay."""
    if not isinstance(piece, dict):
        return
    idx = piece.get("index")
    ptype = piece.get("type")
    for existing in accumulator:
        if existing.get("index") == idx and existing.get("type") == ptype:
            for field in ("text", "thinking"):
                value = piece.get(field)
                if isinstance(value, str) and value:
                    existing[field] = (existing.get(field, "") or "") + value
                elif field in piece and field not in existing:
                    existing[field] = piece[field]
            for field in ("signature", "data"):
                value = piece.get(field)
                if value:
                    existing[field] = value
            for field in ("format", "id"):
                if field in piece and field not in existing:
                    existing[field] = piece[field]
            return
    # Copy new entries so later merges never mutate the provider-owned delta.
    accumulator.append(dict(piece))


def pack_reasoning_fields(
    text: str,
    details: list[Any],
    extra: dict[str, Any],
    *,
    include_text: bool = False,
    include_details: bool = False,
) -> dict[str, Any]:
    """Pack reasoning fields while preserving explicitly emitted empty values."""
    packed: dict[str, Any] = {}
    if include_text or text:
        packed["reasoning_content"] = text
    if include_details or details:
        packed["reasoning_details"] = details
    for k, v in (extra or {}).items():
        packed[k] = v
    return packed


_STATEFUL_ASSISTANT_FIELD_DEFAULTS: dict[str, Any] = {
    "reasoning_content": "",
    "reasoning_details": [],
    "reasoning": "",
}


def normalize_stateful_assistant_fields(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fill known stateful assistant fields after a conversation begins using them."""
    seen = {
        field
        for msg in messages
        if msg.get("role") == "assistant"
        for field in _STATEFUL_ASSISTANT_FIELD_DEFAULTS
        if field in msg
    }
    if not seen:
        return messages

    changed = False
    normalized: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            normalized.append(msg)
            continue
        missing = [field for field in seen if field not in msg]
        if not missing:
            normalized.append(msg)
            continue
        updated = dict(msg)
        for field in missing:
            default = _STATEFUL_ASSISTANT_FIELD_DEFAULTS[field]
            updated[field] = list(default) if isinstance(default, list) else default
        normalized.append(updated)
        changed = True
    return normalized if changed else messages


def tool_call_from_pending(call: dict[str, str]) -> NativeToolCall:
    """Convert a streaming tool-call accumulator to a native call."""
    return NativeToolCall(
        id=call["id"],
        name=call["name"],
        arguments=call["arguments"],
    )


def tool_calls_from_message(tool_calls: Any) -> list[NativeToolCall]:
    """Convert SDK tool calls into framework-native calls."""
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
