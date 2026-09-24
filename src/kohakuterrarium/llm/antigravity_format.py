"""Translate canonical messages and bind reusable Google parts to their history."""

import base64
import copy
import hashlib
import json
import re

from kohakuterrarium.llm.antigravity_auth import AntigravityError
from kohakuterrarium.llm.base import ToolSchema

STATE_KEY = "_kt_antigravity_content"


def _digest(message: dict) -> str:
    canonical = {
        "content": message.get("content") or "",
        "tool_calls": message.get("tool_calls") or [],
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def matches_message(state: dict, message: dict) -> bool:
    return isinstance(state, dict) and state.get("digest") == _digest(message)


def replay_target(
    messages: list[dict], target: dict | None, state: dict
) -> dict | None:
    """Select a matching assistant or append a signed, empty assistant round."""
    if (
        target is not None
        and matches_message(state, target)
        and STATE_KEY not in target
    ):
        return target
    empty = {"role": "assistant", "content": ""}
    if matches_message(state, empty):
        messages.append(empty)
        return empty
    return None


def signed_state(message: dict, parts: list[dict], model: str, scope: str) -> dict:
    return {
        "version": 1,
        "provider": "google-antigravity",
        "model": model,
        "scope": scope,
        "digest": _digest(message),
        "parts": copy.deepcopy(parts),
    }


def _content_parts(content) -> list[dict]:
    if content is None or content == "":
        return []
    if isinstance(content, str):
        return [{"text": content}]
    if not isinstance(content, list):
        raise AntigravityError("unsupported_content")
    parts = []
    for item in content:
        if not isinstance(item, dict):
            raise AntigravityError("unsupported_content")
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            parts.append({"text": item["text"]})
        elif item.get("type") == "image_url":
            url = item.get("image_url", {}).get("url", "")
            match = re.fullmatch(
                r"data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=]+)", url
            )
            if not match:
                raise AntigravityError("only_inline_images_supported")
            try:
                base64.b64decode(match[2], validate=True)
            except ValueError:
                raise AntigravityError("invalid_image") from None
            parts.append({"inlineData": {"mimeType": match[1], "data": match[2]}})
        else:
            raise AntigravityError("unsupported_content")
    return parts


def _claude_replay_parts(parts: list[dict]) -> list[dict]:
    """Assemble streamed text blocks before replaying Claude's signed history."""
    assembled = []
    for part in parts:
        if isinstance(part.get("text"), str):
            if not part["text"] and not part.get("thoughtSignature"):
                continue
            previous = assembled[-1] if assembled else None
            if (
                previous is not None
                and isinstance(previous.get("text"), str)
                and bool(previous.get("thought")) == bool(part.get("thought"))
                and not previous.get("thoughtSignature")
            ):
                previous["text"] += part["text"]
                if part.get("thoughtSignature"):
                    previous["thoughtSignature"] = part["thoughtSignature"]
                continue
        assembled.append(part)
    result = []
    for part in assembled:
        if part.get("thought"):
            if not part.get("thoughtSignature"):
                continue
            if not part.get("text"):
                raise AntigravityError("history_requires_new_session")
        result.append(part)
    return result


def encode_messages(
    messages: list[dict], model: str, scope: str
) -> tuple[dict, list[dict]]:
    """Serialize text/images and complete tool rounds, retaining bound raw parts."""
    system, contents = [], []
    pending = []
    wire_ids = set()
    results = {}
    for message in messages:
        role = message.get("role")
        if role == "tool":
            key = message.get("tool_call_id")
            if key not in {call["id"] for call in pending} or key in results:
                raise AntigravityError("orphan_tool_result")
            results[key] = message.get("content") or ""
            if len(results) == len(pending):
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": call["function"]["name"],
                                    "response": {"output": results[call["id"]]},
                                    **(
                                        {"id": call["id"]}
                                        if call["id"] in wire_ids
                                        else {}
                                    ),
                                }
                            }
                            for call in pending
                        ],
                    }
                )
                pending, results = [], {}
            continue
        if pending:
            raise AntigravityError("incomplete_tool_round")
        if role in {"system", "developer"}:
            system.extend(_content_parts(message.get("content")))
            continue
        if role not in {"user", "assistant"}:
            raise AntigravityError("unsupported_role")
        parts = _content_parts(message.get("content"))
        if role == "assistant":
            state = message.get(STATE_KEY)
            bound = isinstance(state, dict) and all(
                (
                    state.get("version") == 1,
                    state.get("provider") == "google-antigravity",
                    state.get("model") == model,
                    state.get("scope") == scope,
                    state.get("digest") == _digest(message),
                    isinstance(state.get("parts"), list),
                )
            )
            pending = message.get("tool_calls") or []
            if bound:
                parts = copy.deepcopy(state["parts"])
                if model.startswith("claude-"):
                    parts = _claude_replay_parts(parts)
                wire_ids = {
                    call["id"]
                    for part in parts
                    if isinstance(call := part.get("functionCall"), dict)
                    and isinstance(call.get("id"), str)
                    and call["id"]
                }
            elif pending:
                raise AntigravityError("history_requires_new_session")
            if pending and (
                len({c.get("id") for c in pending}) != len(pending)
                or any(not c.get("id") for c in pending)
            ):
                raise AntigravityError("invalid_tool_ids")
        if parts:
            contents.append(
                {"role": "model" if role == "assistant" else "user", "parts": parts}
            )
    if pending:
        raise AntigravityError("incomplete_tool_round")
    return {"parts": system}, contents


def normalize_schema(schema: dict) -> dict:
    """Translate the supported JSON Schema subset to a Google function schema."""
    if not isinstance(schema, dict):
        raise AntigravityError("unsupported_tool_schema")
    result = {}
    ignored = {"$schema", "additionalProperties", "default", "title"}
    allowed = {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "description",
        "nullable",
        "anyOf",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "format",
    }
    for key, value in schema.items():
        if key in ignored:
            continue
        if key not in allowed:
            raise AntigravityError("unsupported_tool_schema")
        if key == "type":
            if not isinstance(value, str) or value not in {
                "object",
                "array",
                "string",
                "number",
                "integer",
                "boolean",
                "null",
            }:
                raise AntigravityError("unsupported_tool_schema")
            result[key] = value.upper()
        elif key == "properties":
            result[key] = {
                name: normalize_schema(child) for name, child in value.items()
            }
        elif key == "items":
            result[key] = normalize_schema(value)
        elif key == "anyOf":
            result[key] = [normalize_schema(child) for child in value]
        else:
            result[key] = value
    return result


def encode_tools(tools: list[ToolSchema] | None) -> list[dict]:
    if not tools:
        return []
    return [
        {
            "functionDeclarations": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": normalize_schema(tool.parameters),
                }
                for tool in tools
            ]
        }
    ]
