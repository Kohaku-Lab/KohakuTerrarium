"""Per-LLM-call accounting: what each call carried, what went on the wire, what it cost.

``compose`` splits a call's logical context (system prompt by section, tool
schemas per tool, user text, assistant text and tool-call arguments, tool
results per tool and part) into character counts. ``CallLog`` keeps one
entry per call: round, wall time, composition, provider usage (input,
cached input, output, reasoning) and the wire view (full send or delta after
``previous_response_id``). ``instrument`` hooks a Codex provider instance to
feed it.
"""

import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

SECTION_KEYS = {
    "Browser pages": "browser_plugin",
    "Untrusted content": "untrusted_content",
    "Execution model": "execution_model",
    "Skills": "skills_index",
}
_HEADING = re.compile(r"^## (.+?)\s*$")
_ACTION_LINE = re.compile(r"^(Actions: |\d+\. )")
_PAGE_META = (
    "Page changes",
    "Navigated:",
    "Reloaded:",
    "Page now",
    "Tab opened",
    "Dialog pending",
)


def section_key(heading: str) -> str:
    return SECTION_KEYS.get(heading) or re.sub(r"\W+", "_", heading.lower()).strip("_")


def system_sections(text: str) -> dict[str, int]:
    """Characters per system-prompt section: ``base`` before the first ``## `` heading."""
    out: Counter[str] = Counter()
    key = "base"
    for line in (text or "").splitlines(keepends=True):
        if match := _HEADING.match(line):
            key = section_key(match.group(1))
        out[key] += len(line)
    return dict(out)


def _text_parts(content: Any) -> tuple[str, int]:
    """The text of a message content and how many image parts it carries."""
    if isinstance(content, list):
        texts, images = [], 0
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                texts.append(part.get("text", ""))
            elif part.get("type") in ("image_url", "image", "input_image"):
                images += 1
        return "\n".join(texts), images
    return ("" if content is None else str(content)), 0


def tool_result_parts(text: str) -> dict[str, int]:
    """Characters of one tool result by part.

    ``header`` (the ``[tab] url`` line), ``action_report`` (``Actions:`` and
    numbered status lines), ``page_wrapper`` (the ``page_content`` tag lines),
    ``title``, ``page_meta`` (navigation, change and tab notices),
    ``page_body``, ``notes`` (lines after the page) and ``body`` for results
    with no page at all (errors, files).
    """
    lines = (text or "").splitlines(keepends=True)
    if not any(l.startswith("<page_content") for l in lines):
        if lines and lines[0].startswith("[t"):
            rest = sum(len(l) for l in lines[1:])
            out = {"header": len(lines[0])}
            if rest:
                out["body"] = rest
            return out
        return {"body": len(text or "")} if text else {}
    out: Counter[str] = Counter()
    where = "before"
    for i, line in enumerate(lines):
        stripped = line.strip()
        if where == "before":
            if line.startswith("<page_content"):
                out["page_wrapper"] += len(line)
                where = "inside"
            elif i == 0 and line.startswith("[t"):
                out["header"] += len(line)
            elif _ACTION_LINE.match(stripped):
                out["action_report"] += len(line)
            else:
                out["notes"] += len(line)
        elif where == "inside":
            if stripped == "</page_content>":
                out["page_wrapper"] += len(line)
                where = "after"
            elif stripped.startswith("Title:"):
                out["title"] += len(line)
            elif stripped.startswith(_PAGE_META):
                out["page_meta"] += len(line)
            else:
                out["page_body"] += len(line)
        else:
            out["notes"] += len(line)
    return dict(out)


def schema_chars(tool: Any) -> tuple[str, int]:
    """The name of a tool schema and the characters of its wire form."""
    if isinstance(tool, Mapping):
        name = tool.get("name") or tool.get("function", {}).get("name", "?")
        body = tool
    else:
        name = getattr(tool, "name", "?")
        body = {
            "type": "function",
            "name": name,
            "description": getattr(tool, "description", ""),
            "parameters": getattr(tool, "parameters", {}),
        }
    return name, len(json.dumps(body, ensure_ascii=False, separators=(",", ":")))


def _call_names(messages: list[Mapping[str, Any]]) -> dict[str, str]:
    names = {}
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", call)
            names[call.get("id", "")] = fn.get("name", "?")
    return names


def compose(
    messages: Iterable[Mapping[str, Any]], tools: Iterable[Any] | None = None
) -> dict[str, int]:
    """Characters of a call's logical context, keyed ``area.component[.part]``."""
    messages = [m if isinstance(m, Mapping) else m.to_dict() for m in messages]
    names = _call_names(messages)
    out: Counter[str] = Counter()
    for msg in messages:
        role = msg.get("role")
        text, images = _text_parts(msg.get("content"))
        if images:
            out["image_parts"] += images
        if role == "system":
            for key, chars in system_sections(text).items():
                out[f"system.{key}"] += chars
        elif role == "user":
            out["user"] += len(text)
        elif role == "assistant":
            out["assistant.text"] += len(text)
            for call in msg.get("tool_calls") or []:
                fn = call.get("function", call)
                args = fn.get("arguments") or ""
                if not isinstance(args, str):
                    args = json.dumps(args, ensure_ascii=False)
                out[f"assistant.tool_args.{fn.get('name', '?')}"] += len(args)
        elif role == "tool":
            tool = msg.get("name") or names.get(msg.get("tool_call_id", ""), "?")
            for part, chars in tool_result_parts(text).items():
                out[f"tool_result.{tool}.{part}"] += chars
        else:
            out[f"other.{role}"] += len(text)
    for tool in tools or []:
        name, chars = schema_chars(tool)
        out[f"tools.{name}"] += chars
    return {k: v for k, v in out.items() if v}


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def usage_of(event: Any) -> dict[str, int | None] | None:
    """Provider usage of a ``response.completed`` event, else ``None``."""
    if _field(event, "type") != "response.completed":
        return None
    usage = _field(_field(event, "response"), "usage")
    if usage is None:
        return None
    reasoning = _field(_field(usage, "output_tokens_details"), "reasoning_tokens")
    return {
        "input": int(_field(usage, "input_tokens") or 0),
        "cached_input": int(
            _field(_field(usage, "input_tokens_details"), "cached_tokens") or 0
        ),
        "output": int(_field(usage, "output_tokens") or 0),
        "reasoning": None if reasoning is None else int(reasoning),
    }


def _digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, sort_keys=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def prefix_fingerprint(
    messages: Iterable[Any], tools: Iterable[Any] | None
) -> dict[str, str]:
    """Digests of the parts every call should repeat unchanged: system text, tool list."""
    messages = [m if isinstance(m, Mapping) else m.to_dict() for m in messages]
    system = [m.get("content") for m in messages if m.get("role") == "system"]
    schemas = []
    for tool in tools or []:
        if isinstance(tool, Mapping):
            schemas.append(tool)
        else:
            schemas.append(
                [
                    getattr(tool, "name", "?"),
                    getattr(tool, "description", ""),
                    getattr(tool, "parameters", {}),
                ]
            )
    return {"system": _digest(system), "tools": _digest(schemas)}


def _chars(items: Any) -> int:
    return len(
        json.dumps(items, ensure_ascii=False, default=str, separators=(",", ":"))
    )


class CallLog:
    """One entry per LLM call of a run, filled from the plugin hooks and the provider."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._started: float | None = None

    def begin(self, messages: list[Any], tools: list[Any] | None) -> None:
        self._started = time.monotonic()
        self.calls.append(
            {
                "round": len(self.calls),
                "messages": len(messages),
                "context": compose(messages, tools),
                "prefix": prefix_fingerprint(messages, tools),
                "usage": None,
                "wire": {"mode": "unknown", "attempts": 0},
                "wall_s": None,
            }
        )

    def _current(self) -> dict[str, Any] | None:
        return self.calls[-1] if self.calls else None

    def on_wire(self, items: list[Any], delta: list[Any] | None) -> None:
        call = self._current()
        if call is None:
            return
        wire = call["wire"]
        wire["attempts"] += 1
        wire["mode"] = "ws_full" if delta is None else "ws_delta"
        wire["items_total"] = len(items)
        wire["items_sent"] = len(items) if delta is None else len(delta)
        wire["input_chars_total"] = _chars(items)
        wire["input_chars_sent"] = (
            wire["input_chars_total"] if delta is None else _chars(delta)
        )

    def on_usage(self, usage: dict[str, Any]) -> None:
        call = self._current()
        if call is not None:
            call["usage"] = usage
            if call["wire"]["mode"] == "unknown":
                call["wire"]["mode"] = "http"

    def end(self, fallback_usage: Mapping[str, Any] | None) -> None:
        call = self._current()
        if call is None:
            return
        if self._started is not None:
            call["wall_s"] = round(time.monotonic() - self._started, 3)
        if call["usage"] is None and fallback_usage:
            call["usage"] = {
                "input": int(fallback_usage.get("prompt_tokens") or 0),
                "cached_input": int(fallback_usage.get("cached_tokens") or 0),
                "output": int(fallback_usage.get("completion_tokens") or 0),
                "reasoning": fallback_usage.get("reasoning_tokens"),
                "source": "controller",
            }


def instrument(provider: Any, log: CallLog) -> bool:
    """Feed ``log`` from a Codex provider instance; ``False`` when it is not one."""
    process = getattr(provider, "_process_stream_event", None)
    session_for = getattr(provider, "_ws_session_for_turn", None)
    if process is None:
        return False

    def process_wrapped(event: Any, *args: Any, **kwargs: Any) -> Any:
        usage = usage_of(event)
        if usage is not None:
            log.on_usage(usage)
        return process(event, *args, **kwargs)

    provider._process_stream_event = process_wrapped
    if session_for is not None:

        def session_wrapped(*args: Any, **kwargs: Any) -> Any:
            session = session_for(*args, **kwargs)
            if session is not None and not getattr(session, "_bench_wrapped", False):
                compute = session._compute_delta

                def compute_wrapped(items: list[Any]) -> list[Any] | None:
                    delta = compute(items)
                    log.on_wire(items, delta)
                    return delta

                session._compute_delta = compute_wrapped
                session._bench_wrapped = True
            return session

        provider._ws_session_for_turn = session_wrapped
    return True


def run_totals(calls: Iterable[Mapping[str, Any]]) -> dict[str, int | None]:
    """Token totals over the calls that carry provider usage."""
    usages = [c["usage"] for c in calls if c.get("usage")]
    reasoning = [u["reasoning"] for u in usages if u.get("reasoning") is not None]
    return {
        "input": sum(u["input"] for u in usages),
        "cached_input": sum(u["cached_input"] for u in usages),
        "output": sum(u["output"] for u in usages),
        "reasoning": sum(reasoning) if reasoning else None,
    }
