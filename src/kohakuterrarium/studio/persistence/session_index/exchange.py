"""The latest exchange of a session's primary conversation, for listings.

The primary conversation is the viewer's default agent, else the privileged
``root``, else the first agent. The exchange is the latest user prompt (tool
feedback skipped), the last reply text after it, and the number of prompts.
"""

import json
from typing import Any

from kohakuterrarium.core.conversation_elide import TOOL_FEEDBACK_KIND

QUOTE_LIMIT = 400
EMPTY_EXCHANGE = {"last_user": "", "last_reply": "", "turn_count": 0}


def flatten_text(content: Any, limit: int = 200) -> str:
    """Flatten message content to bounded text.

    Multimodal and unknown parts become bracketed markers (``[image]``,
    ``[file]``) so the text never embeds binary or base64 payloads.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content[:limit]
    if isinstance(content, list):
        bits: list[str] = []
        for part in content:
            if isinstance(part, str):
                bits.append(part)
            elif isinstance(part, dict):
                kind = part.get("type") or ""
                if kind == "text":
                    bits.append(str(part.get("text") or ""))
                elif kind in ("image_url", "image"):
                    bits.append("[image]")
                elif kind == "file":
                    bits.append("[file]")
                else:
                    bits.append(f"[{kind or 'attachment'}]")
        return " ".join(b for b in bits if b)[:limit]
    if isinstance(content, dict):
        return flatten_text([content], limit)
    return str(content)[:limit]


def primary_agent(meta: dict) -> str:
    agents = list(meta.get("agents") or [])
    preferred = meta.get("viewer_default_agent")
    if preferred in agents:
        return preferred
    if "root" in agents:
        return "root"
    return agents[0] if agents else ""


def messages_of(snapshot: Any) -> list | None:
    """A conversation snapshot as a message list; None for a legacy or unreadable one."""
    if isinstance(snapshot, (str, bytes)):
        try:
            snapshot = json.loads(snapshot)
        except (ValueError, UnicodeDecodeError):
            return None
    if isinstance(snapshot, dict):
        snapshot = snapshot.get("messages")
    return snapshot if isinstance(snapshot, list) else None


def is_user_prompt(message: Any) -> bool:
    if not isinstance(message, dict) or message.get("role") != "user":
        return False
    metadata = message.get("metadata")
    return not (
        isinstance(metadata, dict) and metadata.get("kind") == TOOL_FEEDBACK_KIND
    )


def _prompt_indexes(messages: list) -> list[int]:
    return [
        i
        for i, m in enumerate(messages)
        if is_user_prompt(m) and flatten_text(m.get("content")).strip()
    ]


def _reply_after(messages: list, start: int, stop: int, limit: int) -> str:
    """The last nonempty assistant text in ``messages[start:stop]``."""
    reply = ""
    for message in messages[start:stop]:
        if isinstance(message, dict) and message.get("role") == "assistant":
            text = flatten_text(message.get("content"), limit).strip()
            if text:
                reply = text
    return reply


def recent_exchanges(
    messages: list | None, count: int = 3, limit: int = QUOTE_LIMIT
) -> list[dict]:
    """The last ``count`` exchanges, oldest first: ``{turn, user, reply}``.

    ``turn`` is the 1-based prompt number in the conversation.
    """
    if not messages or count <= 0:
        return []
    prompts = _prompt_indexes(messages)
    bounds = prompts[1:] + [len(messages)]
    out = []
    for turn, (start, stop) in enumerate(zip(prompts, bounds), start=1):
        if turn <= len(prompts) - count:
            continue
        out.append(
            {
                "turn": turn,
                "user": flatten_text(messages[start].get("content"), limit).strip(),
                "reply": _reply_after(messages, start + 1, stop, limit),
            }
        )
    return out


def latest_exchange(messages: list | None, limit: int = QUOTE_LIMIT) -> dict:
    """``{last_user, last_reply, turn_count}`` of a message list."""
    last = recent_exchanges(messages, 1, limit)
    if not last:
        return dict(EMPTY_EXCHANGE)
    return {
        "last_user": last[0]["user"],
        "last_reply": last[0]["reply"],
        "turn_count": last[0]["turn"],
    }
