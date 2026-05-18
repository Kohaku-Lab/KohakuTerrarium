"""Focus state for multi-creature ``cli_rich``.

The focus controller is a small pure-state machine: it knows which
creatures exist (by ``creature_id``) and which one is currently
focused. Tab cycles forward; Shift+Tab cycles backward; ``set`` jumps
to a specific id.

Side-effects (swapping the LiveRegion render source, repainting the
prompt prefix, saving / restoring drafts) live in ``RichCLIApp``;
this module is the data layer they coordinate around.

Also includes the ``@name`` retargeting parser used by the composer's
submit handler — it lives here because both pieces are about
"which creature does this input go to."
"""

import re
from dataclasses import dataclass, field
from typing import Iterable

# Match ``@name <message>`` at the start of input. ``name`` allows
# alphanumerics, dashes, dots, underscores — the union of every
# legal creature name shape used by the recipe / config layer. The
# message body is everything after the first whitespace block, dotall
# so the user can paste multi-line content after the redirect.
_AT_NAME_RE = re.compile(r"^\s*@([\w.\-]+)\s+(.*)$", re.DOTALL)


@dataclass
class AtNameTarget:
    """Parsed shape of an ``@name msg`` redirect."""

    name: str
    payload: str
    is_broadcast: bool = False


def parse_at_name(text: str) -> AtNameTarget | None:
    """Return ``AtNameTarget`` if ``text`` starts with ``@name``, else ``None``.

    ``@all msg`` sets ``is_broadcast=True``. The caller decides
    whether to honour broadcast based on the focused creature's
    privilege bit (broadcast is a privileged op).
    """
    match = _AT_NAME_RE.match(text)
    if match is None:
        return None
    name, payload = match.group(1), match.group(2).strip()
    if not payload:
        return None  # `@name` alone with no body is treated as plain text
    return AtNameTarget(
        name=name,
        payload=payload,
        is_broadcast=(name.lower() == "all"),
    )


@dataclass
class FocusController:
    """Pure focus state — ordered list of creature ids + cursor.

    Construction:

        ctrl = FocusController(creature_ids=["c1", "c2", "c3"], focus_id="c2")

    Mutations all return the new focus id so callers can act on the
    change without re-reading state.
    """

    creature_ids: list[str] = field(default_factory=list)
    focus_id: str = ""

    def __post_init__(self) -> None:
        if self.creature_ids and self.focus_id not in self.creature_ids:
            self.focus_id = self.creature_ids[0]

    @property
    def count(self) -> int:
        return len(self.creature_ids)

    def index(self) -> int:
        """Index of the current focus, or ``-1`` when empty / not present."""
        if not self.creature_ids or self.focus_id not in self.creature_ids:
            return -1
        return self.creature_ids.index(self.focus_id)

    def next(self) -> str:
        """Move focus forward (wraps). Returns the new focus id."""
        if not self.creature_ids:
            return ""
        idx = self.index()
        if idx < 0:
            self.focus_id = self.creature_ids[0]
            return self.focus_id
        self.focus_id = self.creature_ids[(idx + 1) % len(self.creature_ids)]
        return self.focus_id

    def prev(self) -> str:
        """Move focus backward (wraps). Returns the new focus id."""
        if not self.creature_ids:
            return ""
        idx = self.index()
        if idx < 0:
            self.focus_id = self.creature_ids[-1]
            return self.focus_id
        self.focus_id = self.creature_ids[(idx - 1) % len(self.creature_ids)]
        return self.focus_id

    def set(self, creature_id: str) -> bool:
        """Jump focus to ``creature_id``. Returns True if the id is known."""
        if creature_id not in self.creature_ids:
            return False
        self.focus_id = creature_id
        return True

    def add(self, creature_id: str) -> None:
        """Track a newly-spawned creature. No-op if already present."""
        if creature_id in self.creature_ids:
            return
        self.creature_ids.append(creature_id)
        if not self.focus_id:
            self.focus_id = creature_id

    def remove(self, creature_id: str) -> str:
        """Drop a creature; if it was focused, pick a sibling. Returns new focus."""
        if creature_id not in self.creature_ids:
            return self.focus_id
        idx = self.creature_ids.index(creature_id)
        self.creature_ids.pop(idx)
        if self.focus_id != creature_id:
            return self.focus_id
        if not self.creature_ids:
            self.focus_id = ""
            return self.focus_id
        # Prefer the next sibling, falling back to the previous (or 0).
        new_idx = min(idx, len(self.creature_ids) - 1)
        self.focus_id = self.creature_ids[new_idx]
        return self.focus_id

    def replace(self, creature_ids: Iterable[str]) -> None:
        """Reset the tracked id list (e.g. after engine topology change).

        Preserves the current focus_id if it still exists; otherwise
        falls back to the first id (or empty).
        """
        ids = list(creature_ids)
        self.creature_ids = ids
        if not ids:
            self.focus_id = ""
        elif self.focus_id not in ids:
            self.focus_id = ids[0]


__all__ = ["FocusController", "AtNameTarget", "parse_at_name"]
