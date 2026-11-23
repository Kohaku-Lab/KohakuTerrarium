"""Per-tab memory of which refs the model has been shown, and what they named.

The session records the ``(role, name)`` fingerprint of every ref in each
result it returns, then judges a ref the model passes back against a fresh
snapshot before acting on it.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass

Fingerprint = tuple[str, str]
MAX_REFS_PER_TAB = 5000


@dataclass(frozen=True)
class RefVerdict:
    """Whether a ref may be acted on: ``ok``, ``stale`` or ``unknown``.

    ``message`` is empty for ``ok`` and otherwise ready to show the model.
    """

    status: str
    ref: str
    message: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _describe(fingerprint: Fingerprint) -> str:
    role, name = fingerprint
    return f"{role} {json.dumps(name, ensure_ascii=False)}" if name else role


class RefBook:
    """Fingerprints of the refs returned to the model, kept per tab id.

    Each tab holds at most ``max_refs`` entries; the least recently recorded
    ref is forgotten first.
    """

    def __init__(self, max_refs: int = MAX_REFS_PER_TAB) -> None:
        self.max_refs = max_refs
        self._tabs: dict[str, dict[str, Fingerprint]] = {}

    def record(self, tab_id: str, shown: Mapping[str, Fingerprint]) -> None:
        """Remember the refs a result just showed; called at the end of each tool call."""
        book = self._tabs.setdefault(tab_id, {})
        for ref, fingerprint in shown.items():
            book.pop(ref, None)
            book[ref] = fingerprint
        while len(book) > self.max_refs:
            del book[next(iter(book))]

    def reset(self, tab_id: str | None = None) -> None:
        """Forget one tab (navigation, close) or every tab when ``tab_id`` is None."""
        if tab_id is None:
            self._tabs.clear()
        else:
            self._tabs.pop(tab_id, None)

    def known(self, tab_id: str) -> bool:
        """Whether any ref has been recorded for the tab since its last reset."""
        return bool(self._tabs.get(tab_id))

    def judge(
        self, tab_id: str, ref: str, fresh: Mapping[str, Fingerprint]
    ) -> RefVerdict:
        """Judge ``ref`` against ``fresh``, the fingerprints of a new full snapshot."""
        seen = self._tabs.get(tab_id, {}).get(ref)
        now = fresh.get(ref)
        if now is None:
            if seen is None:
                message = f"ref {ref} is not on the page. Take a snapshot to get current refs."
            else:
                message = (
                    f"ref {ref} ({_describe(seen)}) is no longer on the page. "
                    "Take a snapshot to get current refs."
                )
            return RefVerdict("stale", ref, message)
        if seen is None:
            return RefVerdict(
                "unknown",
                ref,
                f"ref {ref} was not in any snapshot returned to you. "
                "Take a snapshot and use a ref from it.",
            )
        if seen != now:
            return RefVerdict(
                "stale",
                ref,
                f"ref {ref} was {_describe(seen)} but now points at {_describe(now)}. "
                "Take a snapshot to get current refs.",
            )
        return RefVerdict("ok", ref, "")
