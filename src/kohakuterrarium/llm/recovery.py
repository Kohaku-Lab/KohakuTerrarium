"""Provider-boundary recovery helpers for LLM calls.

This module is intentionally provider-agnostic except for the small
``classify_openai_error`` adapter. Providers can share the retry policy,
backoff calculation, and emergency context-drop reducer while implementing
provider-specific classification at their boundary.
"""

import asyncio
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class ErrorClass(Enum):
    """Normalised provider error classes used by KT-side recovery."""

    USER_ERROR = "user_error"
    OVERFLOW = "overflow"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RetryPolicy:
    """KT-side retry policy layered on top of provider SDK retries."""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.25
    retry_classes: frozenset[ErrorClass] = field(
        default_factory=lambda: frozenset(
            {
                ErrorClass.RATE_LIMIT,
                ErrorClass.SERVER,
                ErrorClass.TRANSIENT,
            }
        )
    )

    @classmethod
    def from_value(cls, value: "RetryPolicy | dict[str, Any] | None") -> "RetryPolicy":
        """Build a policy from config data or return defaults."""
        if isinstance(value, RetryPolicy):
            return value
        if not isinstance(value, dict):
            return cls()
        retry_classes = value.get("retry_classes")
        converted: frozenset[ErrorClass] | None = None
        if retry_classes is not None:
            classes: set[ErrorClass] = set()
            for item in retry_classes:
                try:
                    classes.add(
                        item if isinstance(item, ErrorClass) else ErrorClass(str(item))
                    )
                except ValueError:
                    logger.warning(
                        "Unknown retry error class ignored", error_class=str(item)
                    )
            converted = frozenset(classes)
        kwargs = {
            "max_retries": int(value.get("max_retries", cls.max_retries)),
            "base_delay": float(value.get("base_delay", cls.base_delay)),
            "max_delay": float(value.get("max_delay", cls.max_delay)),
            "jitter": float(value.get("jitter", cls.jitter)),
        }
        if converted is not None:
            kwargs["retry_classes"] = converted
        return cls(**kwargs)


_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "context length",
    "maximum context length",
    "context window",
    "token limit",
    "too many tokens",
    "input is too long",
    "request too large",
)
_RATE_LIMIT_MARKERS = (
    "rate_limit",
    "rate limit",
    "too many requests",
    "quota_exceeded",
)
_TRANSIENT_MARKERS = (
    "connection reset",
    "connection error",
    "peer disconnected",
    "server disconnected",
    "timeout",
    "timed out",
    "temporarily unavailable",
)


def classify_openai_error(exc: BaseException) -> ErrorClass:
    """Classify an exception raised by the OpenAI Python SDK.

    The SDK exposes a mix of typed exceptions, HTTP status codes, and
    provider-specific JSON payloads. We avoid depending on exact classes so
    compatible providers and tests with light-weight fakes classify cleanly.
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ErrorClass.TRANSIENT

    status = getattr(exc, "status_code", None)
    body = _extract_error_payload(exc)
    code = str(body.get("code") or body.get("type") or "").lower()
    message = _stringify_error(exc, body).lower()

    if (
        status == 413
        or _contains_any(code, _OVERFLOW_MARKERS)
        or _contains_any(message, _OVERFLOW_MARKERS)
    ):
        return ErrorClass.OVERFLOW
    if (
        status == 429
        or _contains_any(code, _RATE_LIMIT_MARKERS)
        or _contains_any(message, _RATE_LIMIT_MARKERS)
    ):
        return ErrorClass.RATE_LIMIT
    if isinstance(status, int) and 500 <= status <= 599:
        return ErrorClass.SERVER
    if isinstance(status, int) and status in {400, 401, 403, 404}:
        return ErrorClass.USER_ERROR
    if _contains_any(message, _TRANSIENT_MARKERS):
        return ErrorClass.TRANSIENT
    return ErrorClass.UNKNOWN


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _extract_error_payload(exc: BaseException) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        return err if isinstance(err, dict) else body
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            data = response.json()
        except Exception:
            data = None
        if isinstance(data, dict):
            err = data.get("error")
            return err if isinstance(err, dict) else data
    return {}


def _stringify_error(exc: BaseException, body: dict[str, Any]) -> str:
    pieces = [str(exc)]
    for key in ("message", "code", "type"):
        value = body.get(key)
        if value:
            pieces.append(str(value))
    return " ".join(pieces)


def backoff_delay(attempt: int, policy: RetryPolicy) -> float:
    """Return capped exponential backoff with optional jitter."""
    base = min(policy.max_delay, policy.base_delay * (2 ** max(attempt - 1, 0)))
    if policy.jitter <= 0:
        return base
    spread = base * policy.jitter
    return max(0.0, base + random.uniform(-spread, spread))


def _message_text_bytes(message: dict[str, Any]) -> int:
    content = message.get("content", "")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict):
                total += len(str(part.get("text") or part.get("content") or part))
            else:
                total += len(str(part))
        return total
    return len(str(content))


def _tool_names_from_calls(calls: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for call in calls:
        fn = call.get("function") if isinstance(call, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            names.append(str(fn["name"]))
        elif isinstance(call, dict) and call.get("name"):
            names.append(str(call["name"]))
    return names


def format_drop_placeholder(count: int, bytes_count: int, tool_names: list[str]) -> str:
    """Create the synthetic user message inserted after emergency drop."""
    tools = ", ".join(f"`{name}`" for name in tool_names) or "(unknown)"
    return (
        "[tool-result truncated]\n\n"
        "The previous tool round returned content too large to fit in the "
        "model's context. "
        f"{count} tool call(s) producing roughly {bytes_count} characters "
        "were dropped to recover.\n\n"
        "If you still need that information, retry with a more targeted "
        "query — for example, a smaller `glob` pattern, a `grep` to narrow "
        "content, a paginated `read` with offset/limit, or a tool parameter "
        "that returns a summary.\n\n"
        f"Tools that were dropped: {tools}"
    )


def drop_last_tool_round(
    messages: list[dict[str, Any]],
) -> tuple[int, list[dict[str, Any]]]:
    """Splice out the most recent assistant tool-call round.

    Returns ``(dropped_count, recovered_messages)``. The input list and
    message dicts are not mutated.
    """
    if not messages:
        return 0, messages

    assistant_idx = -1
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            assistant_idx = idx
            break
    if assistant_idx < 0:
        return 0, messages

    end = assistant_idx + 1
    while end < len(messages) and messages[end].get("role") == "tool":
        end += 1
    tool_messages = messages[assistant_idx + 1 : end]
    calls = list(messages[assistant_idx].get("tool_calls") or [])
    dropped_count = max(len(calls), len(tool_messages))
    if dropped_count <= 0:
        return 0, messages

    bytes_count = sum(_message_text_bytes(m) for m in tool_messages)
    tool_names = _tool_names_from_calls(calls)
    if not tool_names:
        tool_names = [str(m.get("name")) for m in tool_messages if m.get("name")]

    placeholder = {
        "role": "user",
        "content": format_drop_placeholder(dropped_count, bytes_count, tool_names),
    }
    recovered = [dict(m) for m in messages[:assistant_idx]]
    recovered.append(placeholder)
    recovered.extend(dict(m) for m in messages[end:])
    return dropped_count, recovered
