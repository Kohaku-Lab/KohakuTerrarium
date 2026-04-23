"""
Anthropic prompt-caching helper (system + last-3-message strategy).

Anthropic supports up to 4 ``cache_control`` breakpoints per request. The
accepted recipe — and the one Hermes-agent and the Anthropic cookbook both
use — is:

1. One breakpoint on the system prompt (long, stable prefix).
2. Up to 3 breakpoints on the tail of the user/assistant dialog, so the
   rolling conversation prefix keeps getting re-used turn over turn.

When the endpoint is Anthropic's OpenAI-compat layer (``api.anthropic.com``),
``cache_control`` rides on the last *content part* of each marked message,
not on the message envelope. If a message's ``content`` is a plain string
we convert it into the structured form so we have somewhere to attach the
marker; lists of parts get tagged on their last text-shaped part.

This module is pure — no network IO, no state — and only called from
:mod:`kohakuterrarium.llm.openai` after confirming the endpoint is
Anthropic. For every other provider, messages are passed through unchanged
by the caller.

Reference: the working idea comes from Anthropic's published prompt-caching
docs and was validated against Hermes-agent's ``apply_anthropic_cache_control``
implementation. This is an independent re-write for this project.
"""

from copy import deepcopy
from typing import Any

_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}


def _wrap_string_content_with_marker(msg: dict[str, Any]) -> None:
    """Convert ``content: str`` into a single text content-part and mark it."""
    text = msg.get("content") or ""
    msg["content"] = [
        {
            "type": "text",
            "text": text,
            "cache_control": dict(_EPHEMERAL),
        }
    ]


def _mark_last_text_part(parts: list[Any]) -> bool:
    """Attach a ``cache_control`` marker to the last text-shaped dict part.

    Returns True if a marker was placed, False when no suitable part was
    found (e.g. list of ImagePart-only content). The caller is expected to
    fall through without complaint — we don't want to break a turn just
    because the last message was an image-only response.
    """
    for part in reversed(parts):
        if isinstance(part, dict):
            part_type = part.get("type", "text")
            # ``text`` is the common case; tool_use / tool_result / image
            # can also carry cache_control in Anthropic's format, but we
            # deliberately limit ourselves to text to stay conservative —
            # the tail of a turn is almost always a text message anyway.
            if part_type == "text":
                part["cache_control"] = dict(_EPHEMERAL)
                return True
    return False


def _apply_marker(msg: dict[str, Any]) -> None:
    """Place a ``cache_control`` marker on the message's content.

    - ``content`` is a string → wrap into a one-part list, mark that part.
    - ``content`` is a list of parts → mark the last text-shaped part.
    - ``content`` is None / empty → leave the message alone (nothing to
      cache against).
    """
    content = msg.get("content")
    if content is None or content == "":
        return
    if isinstance(content, str):
        _wrap_string_content_with_marker(msg)
        return
    if isinstance(content, list) and content:
        _mark_last_text_part(content)


def apply_anthropic_cache_markers(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a copy of ``messages`` with Anthropic prompt-caching markers.

    The input list is never mutated — we deep-copy so the caller can keep
    using their original messages for logging / session persistence.

    Strategy (system_and_3):
      * If the first message is a system message, mark it (one breakpoint).
      * Walk the remaining messages; skip ``role=="tool"`` (tool results
        slot between user/assistant turns but aren't part of the caching
        tail per Anthropic's guidance) and mark the last 3 non-tool
        messages (up to 3 breakpoints).

    Total breakpoints: at most 4 — matches Anthropic's documented cap.
    """
    if not messages:
        return messages

    result = deepcopy(messages)

    used = 0
    first = result[0]
    if first.get("role") == "system":
        _apply_marker(first)
        used += 1

    # Collect indices of non-system, non-tool messages, preserving order.
    body_indices: list[int] = []
    for idx in range(len(result)):
        msg = result[idx]
        role = msg.get("role", "")
        if role == "system":
            continue
        if role == "tool":
            # Skip tool messages — they intercalate but don't count as
            # anchor points for the rolling breakpoints.
            continue
        body_indices.append(idx)

    # Mark the last (up to) N body messages — respecting the 4-breakpoint
    # cap. With system already taking one slot, we have 3 left.
    remaining_slots = max(0, 4 - used)
    for idx in body_indices[-remaining_slots:]:
        _apply_marker(result[idx])

    return result


def is_anthropic_endpoint(base_url: str | None, provider_name: str | None) -> bool:
    """Heuristic: does this LLM profile target Anthropic's compat endpoint?

    True when either:
      * the provider's base_url host contains ``anthropic.com``, or
      * the LLM profile's provider name is literally ``anthropic``.
    """
    if base_url and "anthropic.com" in base_url.lower():
        return True
    if provider_name and provider_name.lower() == "anthropic":
        return True
    return False
