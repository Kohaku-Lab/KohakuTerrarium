"""Anthropic Messages API format conversion helpers."""

import json
import re
from copy import deepcopy
from typing import Any

from kohakuterrarium.llm.base import NativeToolCall, ToolSchema

KT_CONTENT_KEY = "_kt_anthropic_content"
DATA_IMAGE_RE = re.compile(r"^data:(?P<mime>[^;,]+);base64,(?P<data>.*)$", re.S)
INTERNAL_EXTRA_KEYS = {"auth_as_bearer", "disable_prompt_caching"}
ANTHROPIC_KNOWN_BODY_FIELDS = {
    "metadata",
    "service_tier",
    "stop_sequences",
    "thinking",
    "tool_choice",
    "top_k",
    "top_p",
}


def looks_like_bearer_endpoint(base_url: str) -> bool:
    lowered = (base_url or "").lower()
    return "minimax" in lowered or "minimaxi" in lowered


def is_anthropic_api_endpoint(base_url: str) -> bool:
    return "api.anthropic.com" in (base_url or "").lower()


def anthropic_tools(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters or {"type": "object", "properties": {}},
        }
        for tool in tools
    ]


def prepare_messages(
    messages: list[dict[str, Any]],
) -> tuple[str | list[dict[str, Any]], list[dict[str, Any]]]:
    system_parts: list[str] = []
    body: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            text = content_text(msg.get("content", ""))
            if text:
                system_parts.append(text)
            continue
        if role == "user":
            body.append(
                {"role": "user", "content": user_content(msg.get("content", ""))}
            )
            continue
        if role == "assistant":
            body.append(assistant_message(msg))
            continue
        if role == "tool":
            append_tool_result(body, msg)
    return "\n\n".join(system_parts), body


def assistant_message(msg: dict[str, Any]) -> dict[str, Any]:
    native_content = sanitized_native_content(msg)
    if native_content:
        return {"role": "assistant", "content": native_content}

    content = msg.get("content", "")
    parts: list[dict[str, Any]] = []
    text = content_text(content, assistant=True)
    if text:
        parts.append({"type": "text", "text": text})
    for call in msg.get("tool_calls") or []:
        func = call.get("function") or {}
        parts.append(
            {
                "type": "tool_use",
                "id": str(call.get("id") or ""),
                "name": str(func.get("name") or ""),
                "input": parse_tool_arguments(func.get("arguments", "{}")),
            }
        )
    return {"role": "assistant", "content": parts or text}


def sanitized_native_content(msg: dict[str, Any]) -> list[dict[str, Any]]:
    native_content = msg.get(KT_CONTENT_KEY)
    if not isinstance(native_content, list) or not native_content:
        return []

    tool_calls = msg.get("tool_calls")
    if tool_calls is None:
        return deepcopy(native_content)

    valid_ids = {
        str(call.get("id") or "")
        for call in tool_calls
        if isinstance(call, dict) and call.get("id")
    }
    return [
        deepcopy(block)
        for block in native_content
        if not isinstance(block, dict)
        or block.get("type") != "tool_use"
        or str(block.get("id") or "") in valid_ids
    ]


def append_tool_result(body: list[dict[str, Any]], msg: dict[str, Any]) -> None:
    block = {
        "type": "tool_result",
        "tool_use_id": str(msg.get("tool_call_id") or ""),
        "content": content_text(msg.get("content", "")),
    }
    if (
        body
        and body[-1].get("role") == "user"
        and isinstance(body[-1].get("content"), list)
    ):
        content = body[-1]["content"]
        if all(
            isinstance(part, dict) and part.get("type") == "tool_result"
            for part in content
        ):
            content.append(block)
            return
    body.append({"role": "user", "content": [block]})


def user_content(content: Any) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content) if content is not None else ""
    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            parts.append({"type": "text", "text": str(part.get("text") or "")})
        elif ptype == "image_url":
            parts.append(image_part(part))
        elif ptype == "file":
            text = file_part_text(part)
            if text:
                parts.append({"type": "text", "text": text})
    return parts or ""


def image_part(part: dict[str, Any]) -> dict[str, Any]:
    image = part.get("image_url") if isinstance(part.get("image_url"), dict) else {}
    url = str(image.get("url") or part.get("url") or "")
    if url.startswith("data:"):
        match = DATA_IMAGE_RE.match(url)
        if match:
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": match.group("mime"),
                    "data": match.group("data"),
                },
            }
    if url.startswith("http://") or url.startswith("https://"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return {"type": "text", "text": "[image omitted: unsupported image URL]"}


def file_part_text(part: dict[str, Any]) -> str:
    file_data = part.get("file") if isinstance(part.get("file"), dict) else {}
    name = file_data.get("name") or file_data.get("path") or "file"
    content = file_data.get("content")
    if content:
        return f"[file: {name}]\n{content}"
    return f"[file omitted: {name}]"


def content_text(content: Any, *, assistant: bool = False) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)
    text_parts: list[str] = []
    image_count = 0
    file_count = 0
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            text_parts.append(str(part.get("text") or ""))
        elif ptype in {"image_url", "image"}:
            image_count += 1
        elif ptype == "file":
            file_count += 1
    text = "\n".join(piece for piece in text_parts if piece)
    if not text and (image_count or file_count):
        label = "assistant" if assistant else "message"
        return f"[{label} multimodal content: {image_count} image(s), {file_count} file(s)]"
    return text


def parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        return {}
    try:
        data = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {"_raw": arguments}
    return data if isinstance(data, dict) else {"value": data}


def block_to_dict(block: Any) -> dict[str, Any]:
    if block is None:
        return {}
    if isinstance(block, dict):
        return {k: v for k, v in block.items() if v is not None}
    if hasattr(block, "model_dump"):
        try:
            return block.model_dump(exclude_none=True)
        except TypeError:
            return block.model_dump()
    data = getattr(block, "__dict__", {}) or {}
    if data:
        return {
            k: v for k, v in data.items() if not k.startswith("_") and v is not None
        }
    result: dict[str, Any] = {}
    keys = ("type", "text", "thinking", "signature", "data", "id", "name", "input")
    for key in keys:
        value = getattr(block, key, None)
        if value is not None:
            result[key] = value
    return result


def normalise_started_block(block: dict[str, Any]) -> dict[str, Any]:
    btype = block.get("type")
    if btype == "text":
        block.setdefault("text", "")
    elif btype == "thinking":
        block.setdefault("thinking", "")
        block.setdefault("signature", "")
    elif btype == "tool_use":
        block.setdefault("input", {})
        block["_partial_json"] = ""
    return block


def apply_delta(block: dict[str, Any], delta: Any) -> str:
    dtype = getattr(delta, "type", "")
    if dtype == "text_delta":
        text = getattr(delta, "text", "") or ""
        block["type"] = "text"
        block["text"] = block.get("text", "") + text
        return text
    if dtype == "thinking_delta":
        block["type"] = "thinking"
        block["thinking"] = block.get("thinking", "") + (
            getattr(delta, "thinking", "") or ""
        )
    elif dtype == "signature_delta":
        block["type"] = block.get("type") or "thinking"
        block["signature"] = getattr(delta, "signature", "") or ""
    elif dtype == "input_json_delta":
        block["type"] = block.get("type") or "tool_use"
        block["_partial_json"] = block.get("_partial_json", "") + (
            getattr(delta, "partial_json", "") or ""
        )
    return ""


def finalize_block(block: dict[str, Any]) -> None:
    if block.get("type") != "tool_use":
        block.pop("_partial_json", None)
        return
    partial = block.pop("_partial_json", "")
    if partial:
        block["input"] = parse_tool_arguments(partial)


def ordered_blocks(blocks: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for _, block in sorted(blocks.items()):
        finalize_block(block)
        clean = {k: v for k, v in block.items() if not k.startswith("_")}
        if clean.get("type"):
            ordered.append(clean)
    return ordered


def tool_calls_from_blocks(blocks: list[dict[str, Any]]) -> list[NativeToolCall]:
    calls: list[NativeToolCall] = []
    for block in blocks:
        if block.get("type") != "tool_use":
            continue
        args = block.get("input", {})
        arguments = (
            args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
        )
        calls.append(
            NativeToolCall(
                id=str(block.get("id") or ""),
                name=str(block.get("name") or ""),
                arguments=arguments,
            )
        )
    return calls


def usage_to_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    data = block_to_dict(usage)
    input_tokens = int(data.get("input_tokens") or 0)
    output_tokens = int(data.get("output_tokens") or 0)
    cache_creation = int(data.get("cache_creation_input_tokens") or 0)
    cache_read = int(data.get("cache_read_input_tokens") or 0)
    return _usage_dict(input_tokens, output_tokens, cache_creation, cache_read)


def merge_usage(existing: dict[str, int], usage: Any) -> dict[str, int]:
    """Merge a partial Anthropic stream usage object into accumulated usage.

    Anthropic streaming reports input/cache tokens at ``message_start`` and
    output tokens later on ``message_delta``. Some fields are omitted from a
    given event rather than repeated, so a blind ``dict.update`` can zero out
    the prompt side when the final event only includes output tokens.
    """
    data = block_to_dict(usage)
    if not data:
        return dict(existing)
    input_tokens = int(data.get("input_tokens", existing.get("input_tokens", 0)) or 0)
    output_tokens = int(
        data.get("output_tokens", existing.get("output_tokens", 0)) or 0
    )
    cache_creation = int(
        data.get(
            "cache_creation_input_tokens",
            existing.get("cache_creation_input_tokens", 0),
        )
        or 0
    )
    cache_read = int(
        data.get("cache_read_input_tokens", existing.get("cache_read_input_tokens", 0))
        or 0
    )
    return _usage_dict(input_tokens, output_tokens, cache_creation, cache_read)


def _usage_dict(
    input_tokens: int,
    output_tokens: int,
    cache_creation: int,
    cache_read: int,
) -> dict[str, int]:
    prompt_tokens = input_tokens + cache_creation + cache_read
    result = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": prompt_tokens + output_tokens,
    }
    if cache_creation:
        result["cache_creation_input_tokens"] = cache_creation
    if cache_read:
        result["cache_read_input_tokens"] = cache_read
    if input_tokens:
        result["input_tokens"] = input_tokens
    if output_tokens:
        result["output_tokens"] = output_tokens
    return result


def mark_system_cache(system: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks = system if isinstance(system, list) else [{"type": "text", "text": system}]
    result = deepcopy(blocks)
    mark_last_cacheable_block(result)
    return result


def mark_tail_cache(messages: list[dict[str, Any]], slots: int) -> list[dict[str, Any]]:
    if slots <= 0:
        return messages
    result = deepcopy(messages)
    body_indices = [
        idx
        for idx, msg in enumerate(result)
        if msg.get("role") in {"user", "assistant"} and msg.get("content")
    ]
    for idx in body_indices[-slots:]:
        content = result[idx].get("content")
        if isinstance(content, str):
            result[idx]["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif isinstance(content, list):
            mark_last_cacheable_block(content)
    return result


def mark_last_cacheable_block(blocks: list[dict[str, Any]]) -> bool:
    for block in reversed(blocks):
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"text", "tool_use", "tool_result"}:
            block["cache_control"] = {"type": "ephemeral"}
            return True
    return False
