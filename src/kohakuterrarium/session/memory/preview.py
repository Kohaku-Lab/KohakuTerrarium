"""Bounded memory excerpts that retain the matching span when possible."""

import re
from dataclasses import dataclass

MAX_FOCUS_CHARS = 256


@dataclass(frozen=True)
class TextSlice:
    content: str
    start: int
    end: int
    total: int

    @property
    def truncated(self) -> bool:
        return self.start > 0 or self.end < self.total


def _focus_position(text: str, query: str) -> int:
    """Return the hit position in original text, not casefolded coordinates."""
    if not query:
        return 0
    haystack = text.casefold()
    needle = query[:MAX_FOCUS_CHARS].strip().casefold()
    found = haystack.find(needle) if needle else -1
    if found < 0:
        # FTS queries often contain several tokens rather than one literal span.
        for token in sorted(re.findall(r"\w+", needle), key=len, reverse=True):
            if len(token) < 3:
                continue
            found = haystack.find(token)
            if found >= 0:
                break
    if found < 0:
        return 0
    if len(haystack) == len(text):
        return found

    # Unicode casefold can expand characters (e.g. ß -> ss, İ -> i + dot).
    # Map just the chosen offset back rather than materializing an O(n)-size
    # per-character offset table for long tool outputs.
    folded = 0
    for original, char in enumerate(text):
        folded += len(char.casefold())
        if folded > found:
            return original
    return len(text)


def preview_text(
    text: str,
    max_chars: int,
    *,
    focus: str = "",
    offset: int | None = None,
) -> TextSlice:
    """Return a bounded text window with explicit source offsets.

    With no offset, prefer context near the query phrase. Explicit offsets
    navigate the event itself, without repeating an arbitrary prefix.
    """
    total = len(text)
    if offset is not None:
        if type(offset) is not int or not 0 <= offset <= total:
            raise ValueError(f"offset must be an integer from 0 to {total}")
        start = offset
    else:
        found = _focus_position(text, focus)
        start = max(0, min(found - max_chars // 3, max(0, total - max_chars)))
    end = min(total, start + max_chars)
    body = text[start:end]
    if start > 0:
        body = f"… [offset {start}] " + body
    if end < total:
        body += f"… ({total} chars total)"
    return TextSlice(body, start, end, total)
