"""Harness-side changes to the tool list the Codex provider puts on the wire.

``install`` wraps the two places the provider hands its function-tool dicts
to the transport (``prepare_request_tools`` for HTTP, ``stream_codex_ws_turn``
for WebSocket) in the provider module, so an experiment can send an explicit
``strict`` or a reworded ``browser_act`` hint without touching product code.
"""

import json
from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from kohakuterrarium.llm.codex import provider as codex_provider

OMIT_HINT = "Give only the fields an action uses; leave the others out."
DEFAULTS = ("", 0, False, None, [], {})

Rewrite = Callable[[list[Any]], list[Any]]


def tool_rewriter(strict: bool | None = None, act_hint: str | None = None) -> Rewrite:
    """A function that returns a rewritten copy of an outgoing tool list."""

    def rewrite(tools: list[Any]) -> list[Any]:
        if strict is None and act_hint is None:
            return tools
        out = []
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("type") != "function":
                out.append(tool)
                continue
            tool = deepcopy(tool)
            if strict is not None:
                tool["strict"] = strict
            if act_hint is not None and tool.get("name") == "browser_act":
                actions = tool["parameters"]["properties"]["actions"]
                actions["description"] = act_hint
            out.append(tool)
        return out

    return rewrite


def install(rewrite: Rewrite, module: Any = None) -> Callable[[], None]:
    """Wrap the provider module's tool hand-offs; returns a function that undoes it."""
    module = codex_provider if module is None else module
    prepare = module.prepare_request_tools
    stream = module.stream_codex_ws_turn

    def prepare_request_tools(payload: Any, *args: Any, **kwargs: Any) -> Any:
        result = prepare(payload, *args, **kwargs)
        body = result[0]
        if isinstance(body, dict) and isinstance(body.get("tools"), list):
            body = {**body, "tools": rewrite(body["tools"])}
            result = (body, *result[1:])
        return result

    def stream_codex_ws_turn(provider: Any, session: Any, base_event: Any, *rest: Any):
        if isinstance(base_event, dict) and isinstance(base_event.get("tools"), list):
            base_event = {**base_event, "tools": rewrite(base_event["tools"])}
        return stream(provider, session, base_event, *rest)

    module.prepare_request_tools = prepare_request_tools
    module.stream_codex_ws_turn = stream_codex_ws_turn

    def undo() -> None:
        module.prepare_request_tools = prepare
        module.stream_codex_ws_turn = stream

    return undo


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if v not in DEFAULTS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def argument_padding(messages: list[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """Per tool: calls, argument characters, and characters of default-valued fields."""
    out: dict[str, Counter] = {}
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", call)
            name, text = fn.get("name", "?"), fn.get("arguments") or ""
            row = out.setdefault(name, Counter())
            row["calls"] += 1
            row["chars"] += len(text)
            try:
                parsed = json.loads(text)
            except ValueError:
                continue
            compact = json.dumps(
                _strip(parsed), separators=(",", ":"), ensure_ascii=False
            )
            full = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
            row["default_chars"] += (
                max(0, len(full) - len(compact)) * len(text) // max(1, len(full))
            )
    return {k: dict(v) for k, v in out.items()}
