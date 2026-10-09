"""The three ways a session's one-line summary is written.

``heuristic_text`` cleans the first user prompt, ``compaction_text`` takes the
first sentence of a compaction summary, ``llm_text`` asks a model for one line
over the opening prompt, the latest exchanges and the compaction summary.
"""

import re
from typing import Any

from kohakuterrarium.studio.persistence.session_index.exchange import (
    flatten_text,
    is_user_prompt,
    recent_exchanges,
)

HEURISTIC_LIMIT = 80
SUMMARY_LIMIT = 140
LLM_MAX_TOKENS = 120
LLM_MARKER = "Write the one-line summary of this session."

SUMMARY_PROMPT = (
    "You title chat sessions for a history list. Reply with ONE line of at "
    "most 12 words saying what the session is about or doing, in the language "
    "the user wrote in. No quotes, no trailing period, no preamble."
)

_SPACE = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s|(?<=[。！？])")


def one_line(text: str, limit: int = SUMMARY_LIMIT) -> str:
    """Collapse whitespace, strip wrapping quotes, cut at ``limit`` with an ellipsis."""
    text = _SPACE.sub(" ", text or "").strip().strip("\"'`").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0] or text[: limit - 1]
    return cut.rstrip(" ,;:") + "…"


def first_prompt(messages: list | None) -> str:
    for message in messages or []:
        if is_user_prompt(message):
            text = flatten_text(message.get("content"), 2000).strip()
            if text:
                return text
    return ""


def heuristic_text(messages: list | None) -> str:
    return one_line(first_prompt(messages), HEURISTIC_LIMIT)


def compaction_text(summary: str) -> str:
    text = _SPACE.sub(" ", summary or "").strip().lstrip("#*- ").strip()
    if not text:
        return ""
    return one_line(_SENTENCE_END.split(text, 1)[0])


def llm_request(messages: list | None, compaction: str = "") -> list[dict]:
    """The chat request ``llm_text`` sends: opening prompt, latest exchanges, compaction."""
    parts = [f"Opening request:\n{one_line(first_prompt(messages), 600)}"]
    if compaction:
        parts.append(f"Summary of earlier context:\n{one_line(compaction, 1200)}")
    for item in recent_exchanges(messages, 3, 600):
        parts.append(
            f"User: {item['user']}\nAssistant: {item['reply'] or '(no reply)'}"
        )
    parts.append(LLM_MARKER)
    return [
        {"role": "system", "content": SUMMARY_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


async def llm_text(llm: Any, messages: list | None, compaction: str = "") -> str:
    """One line from ``llm`` (anything with the provider ``chat`` stream)."""
    if not first_prompt(messages):
        return ""
    out = ""
    async for chunk in llm.chat(
        llm_request(messages, compaction), stream=True, max_tokens=LLM_MAX_TOKENS
    ):
        out += chunk
    lines = [line for line in out.strip().splitlines() if line.strip()]
    return one_line(lines[0]).rstrip("。.") if lines else ""
