"""Text-extraction helpers for the auto-compact summary builder.

Lives in its own module so ``core/compact.py`` stays under the 600-line
soft cap. Callers should treat this as private compact-side glue —
do not depend on it from elsewhere.
"""

from typing import Any


def extract_message_text(msg: Any) -> str:
    """Pull a plain-text representation out of any message-content shape.

    ``msg.content`` arrives in three shapes from upstream code:

    * ``str`` — plain text, the easy case.
    * ``list[ContentPart]`` — multimodal parts produced by the
      framework's own message helpers; each part has a ``.text``
      attribute (or is non-textual, in which case it carries no
      summarisable content).
    * ``list[dict]`` — the raw shape the web frontend POSTs and
      ``conversation.append`` stores verbatim. Each dict looks like
      ``{"type": "text", "text": "..."}`` for text and similar for
      other modalities. **This used to silently drop user messages**
      because the prior code asked for ``.text`` via
      ``hasattr(p, "text")`` which is ``False`` for dicts — so a
      conversation built from web POSTs had no user instructions reach
      the compact LLM, producing summaries claiming "no user
      instructions" when the conversation was full of them.

    Returns the joined text, or ``""`` if there's nothing extractable.
    """
    raw = getattr(msg, "content", "")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        chunks: list[str] = []
        for part in raw:
            text: str | None = None
            # Framework ContentPart has ``.text`` (TextPart) — no
            # ``.text`` for ImagePart / AudioPart, those have nothing
            # to feed a text summariser.
            if hasattr(part, "text"):
                text = getattr(part, "text") or ""
            elif isinstance(part, dict):
                # Raw web payload. Tolerate a few shapes the various
                # providers + frontend pickers emit.
                if isinstance(part.get("text"), str):
                    text = part["text"]
                elif isinstance(part.get("content"), str):
                    text = part["content"]
            if text:
                chunks.append(text)
        return " ".join(chunks)
    return ""


COMPACT_PROMPT = """You are summarizing a conversation between an AI agent and a user (or between agents in a team).

Create a structured summary with these exact sections:

### Current Goal
What is the agent currently trying to achieve?

### Key Decisions
What important choices were made, and why? Include approximate position (early/mid/late in conversation).

### Progress
List completed, in-progress, and pending tasks. Use [DONE], [IN PROGRESS], [PENDING] markers.

### Files Modified
List files that were read, written, or edited with brief context.

### Key Facts
Important details that the agent needs to remember to continue working.

### Keywords
Comma-separated list of important terms for keyword search.

### Key Sentences
Exact quotes from the conversation that should be preserved verbatim.

Rules:
- Preserve decision rationale ("X because Y", not just "decided X")
- Keep exact file paths, line numbers, error codes verbatim
- Use relative temporal markers ("early in session", "after fixing X")
- Do NOT include raw tool output (it's searchable in session history)
- Focus on what the agent needs to CONTINUE working, not a narrative
- If there is a previous summary included, merge its information with new content
"""


def format_messages_for_summary(messages: list) -> str:
    """Format messages into text for the summarization prompt.

    Handles every ``msg.content`` shape :func:`extract_message_text`
    does; very long tool results are truncated.
    """
    parts = []
    for msg in messages:
        role = msg.role
        content = extract_message_text(msg)

        # Truncate very long tool results
        if role == "tool" and len(content) > 500:
            content = content[:500] + f"... ({len(content)} chars total)"

        if content:
            parts.append(f"[{role}]: {content}")

    return "\n\n".join(parts)
