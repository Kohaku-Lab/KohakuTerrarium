"""In-memory store for large pastes represented by placeholder tokens.

When a user pastes a chunk of text that's large enough to overwhelm the
composer visually (too many lines, too many chars), we substitute a
placeholder token of the form ``[#pasted-42 · 87 lines]`` into the
visible buffer and keep the real content in this store. On submit, the
composer replaces each placeholder with its stashed content so the
agent sees the actual paste text.

Design goals:
  - Placeholders are a fixed-width visual token so the composer doesn't
    reflow wildly as the user edits around them.
  - Ids are short integers so the token stays compact.
  - Store is per-process, in memory, never persisted. Dropped on exit.

This mirrors the "[Pasted text #N]" pattern in claude-code and
"[pasted N lines]" in gemini-cli.
"""

import re
from dataclasses import dataclass, field

# Thresholds at or above which a paste is substituted with a placeholder.
# Tuned so short multi-line pastes (3-5 lines of code) render verbatim
# while configs / logs / long docs collapse.
PLACEHOLDER_MIN_LINES = 8
PLACEHOLDER_MIN_CHARS = 500

# Placeholder token shape: [#pasted-<id> · <N> lines]
#
# The <id> is a monotonically-increasing integer scoped to the process.
# <N> lines is the line count at stash time. We show line-count rather
# than char-count because users count lines visually.
_PLACEHOLDER_RE = re.compile(r"\[#pasted-(\d+) · \d+ lines\]")


@dataclass
class PasteEntry:
    paste_id: int
    content: str
    line_count: int


@dataclass
class PasteStore:
    """Process-wide, in-memory paste cache."""

    _next_id: int = 1
    _entries: dict[int, PasteEntry] = field(default_factory=dict)

    def stash(self, content: str) -> str:
        """Store *content* and return a placeholder token to insert in the buffer."""
        paste_id = self._next_id
        self._next_id += 1
        lines = content.count("\n") + (0 if content.endswith("\n") else 1)
        self._entries[paste_id] = PasteEntry(
            paste_id=paste_id, content=content, line_count=lines
        )
        return f"[#pasted-{paste_id} · {lines} lines]"

    def resolve(self, text: str) -> str:
        """Replace every placeholder token in *text* with its stashed content.

        Unknown placeholders (ids we never stashed) are left alone — they
        might be something the user typed that happens to look like our
        token shape.
        """
        if "[#pasted-" not in text:
            return text

        def _sub(match: re.Match) -> str:
            paste_id = int(match.group(1))
            entry = self._entries.get(paste_id)
            return entry.content if entry is not None else match.group(0)

        return _PLACEHOLDER_RE.sub(_sub, text)

    def get(self, paste_id: int) -> PasteEntry | None:
        return self._entries.get(paste_id)

    def all_ids(self) -> list[int]:
        return sorted(self._entries.keys())

    def clear(self) -> None:
        self._entries.clear()
        self._next_id = 1


def should_placeholderize(content: str) -> bool:
    """Return True if *content* is large enough to warrant placeholder substitution."""
    if len(content) >= PLACEHOLDER_MIN_CHARS:
        return True
    if content.count("\n") + 1 >= PLACEHOLDER_MIN_LINES:
        return True
    return False
