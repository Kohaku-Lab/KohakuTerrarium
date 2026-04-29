"""Codex Responses API message-shape helpers."""

import json as _json
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Chat Completions messages to Responses API flat input."""
    items: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "user":
            item = _user_item(content)
            if item:
                items.append(item)
        elif role == "assistant":
            items.extend(_assistant_items(content, msg.get("tool_calls", [])))
        elif role == "tool":
            items.append(_tool_item(content, msg.get("tool_call_id", "")))
    return items


def fix_tool_call_pairing(api_input: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure each function_call is followed by matching output."""
    output_by_id: dict[str, dict[str, Any]] = {}
    other_items: list[dict[str, Any]] = []
    for item in api_input:
        if item.get("type") == "function_call_output":
            output_by_id[item["call_id"]] = item
        else:
            other_items.append(item)

    result: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for item in other_items:
        result.append(item)
        if item.get("type") != "function_call":
            continue
        call_id = item["call_id"]
        if call_id in output_by_id:
            result.append(output_by_id[call_id])
        else:
            result.append(_missing_output_item(item))
            logger.warning("Added missing function_call_output", call_id=call_id)
        used_ids.add(call_id)

    orphan_ids = set(output_by_id) - used_ids
    if orphan_ids:
        logger.warning(
            "Dropped orphan function_call_outputs", orphan_count=len(orphan_ids)
        )
    return result


def maybe_capture_stream_rate_limit(
    event: Any, parse_rate_limit_event, usage_snapshot_cls, set_cached
) -> None:
    """Capture a codex.rate_limits event if the SDK surfaces one."""
    try:
        payload: Any = (
            getattr(event, "data", None)
            or getattr(event, "event", None)
            or getattr(event, "raw", None)
        )
        if payload is None:
            return
        if hasattr(payload, "model_dump"):
            payload_dict: Any = payload.model_dump()
        elif isinstance(payload, dict):
            payload_dict = payload
        else:
            return
        if not isinstance(payload_dict, dict):
            return
        if payload_dict.get("type") != "codex.rate_limits":
            return
        snap = parse_rate_limit_event(_json.dumps(payload_dict))
        if snap is not None and snap.has_data():
            set_cached(usage_snapshot_cls(snapshots=[snap]))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "Codex rate-limit event capture failed",
            error=str(exc),
            exc_info=True,
        )


def _user_item(content: Any) -> dict[str, Any] | None:
    if isinstance(content, str):
        return {"role": "user", "content": [{"type": "input_text", "text": content}]}
    if not isinstance(content, list):
        return None
    input_content: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            input_content.append({"type": "input_text", "text": part.get("text", "")})
        elif isinstance(part, dict) and part.get("type") == "image_url":
            input_content.append(
                {"type": "input_image", "image_url": part["image_url"]["url"]}
            )
    return {"role": "user", "content": input_content} if input_content else None


def _assistant_items(
    content: Any, tool_calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    text = _content_text(content, assistant=True)
    if text:
        items.append(
            {"role": "assistant", "content": [{"type": "output_text", "text": text}]}
        )
    for tc in tool_calls:
        func = tc.get("function", {})
        items.append(
            {
                "type": "function_call",
                "call_id": tc.get("id", ""),
                "name": func.get("name", ""),
                "arguments": func.get("arguments", "{}"),
            }
        )
    return items


def _tool_item(content: Any, call_id: str) -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": _content_text(content),
    }


def _content_text(content: Any, *, assistant: bool = False) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    text_parts = []
    image_count = 0
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            text_parts.append(part.get("text", ""))
        elif part.get("type") == "image_url":
            image_count += 1
    text = "\n".join(text_parts)
    if image_count and not text:
        label = "assistant" if assistant else "tool"
        text = f"[{label} multimodal content: {image_count} image(s)]"
    return text


def _missing_output_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": item["call_id"],
        "output": f"[{item.get('name', '')}] Result unavailable (removed by context compaction).",
    }
