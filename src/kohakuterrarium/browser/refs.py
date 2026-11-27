"""Per-tab memory of which refs the model has been shown, and what they named.

The session records the ``(role, name)`` fingerprint of every ref in each
result it returns, then judges a ref the model passes back against a fresh
snapshot before acting on it.
"""

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

Fingerprint = tuple[str, str]
Redact = Callable[[str], str]
MAX_REFS_PER_TAB = 5000
NAME_MAX = 60

_FRAME_PREFIX = re.compile(r"f\d+(?=e\d+$)")
_SPACE = re.compile(r"[\x00-\x20\x7f-\x9f\u2028\u2029]+")


@dataclass(frozen=True)
class RefVerdict:
    """Whether a ref may be acted on: ``ok``, ``stale`` or ``unknown``.

    ``message`` is empty for ``ok`` and otherwise ready to show the model;
    it names only the ref. ``detail`` names what the ref pointed at, which
    is page text and belongs inside the untrusted wrapper.
    """

    status: str
    ref: str
    message: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _describe(fingerprint: Fingerprint, redact: Redact | None) -> str:
    role, name = fingerprint
    name = _SPACE.sub(" ", redact(name) if redact else name).strip()
    if len(name) > NAME_MAX:
        head = (NAME_MAX - 1) // 2
        name = name[:head] + "…" + name[len(name) - (NAME_MAX - 1 - head) :]
    return f"{role} {json.dumps(name, ensure_ascii=False)}" if name else role


class RefBook:
    """Fingerprints of the refs returned to the model, kept per tab id.

    A ref inside an iframe also keeps the document id its frame showed.
    Each tab holds at most ``max_refs`` entries; the least recently recorded
    ref is forgotten first.
    """

    def __init__(self, max_refs: int = MAX_REFS_PER_TAB) -> None:
        self.max_refs = max_refs
        self._tabs: dict[str, dict[str, tuple[Fingerprint, Any]]] = {}

    def record(
        self,
        tab_id: str,
        shown: Mapping[str, Fingerprint],
        frames: Mapping[str, Any] | None = None,
    ) -> None:
        """Remember the refs a result just showed; called at the end of each tool call.

        ``frames`` maps a ref prefix (``f2``) to the document id that frame showed.
        """
        book = self._tabs.setdefault(tab_id, {})
        frames = frames or {}
        for ref, fingerprint in shown.items():
            book.pop(ref, None)
            book[ref] = (fingerprint, frames.get(frame_prefix(ref)))
        while len(book) > self.max_refs:
            del book[next(iter(book))]

    def document(self, tab_id: str, ref: str) -> Any:
        """The document id of the frame ``ref`` was shown in; ``None`` when not known."""
        entry = self._tabs.get(tab_id, {}).get(ref)
        return entry[1] if entry else None

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
        self,
        tab_id: str,
        ref: str,
        fresh: Mapping[str, Fingerprint],
        redact: Redact | None = None,
    ) -> RefVerdict:
        """Judge ``ref`` against ``fresh``, the fingerprints of a new full snapshot.

        ``redact`` is applied to the names a ``detail`` shows before they are cut.
        """
        entry = self._tabs.get(tab_id, {}).get(ref)
        seen = entry[0] if entry else None
        now = fresh.get(ref)
        if now is None:
            if seen is None:
                message = f"ref {ref} is not on the page. Take a snapshot to get current refs."
                return RefVerdict("stale", ref, message)
            return RefVerdict(
                "stale",
                ref,
                f"ref {ref} is no longer on the page; take a snapshot to get current refs",
                f"ref {ref} was {_describe(seen, redact)}",
            )
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
                f"ref {ref} now points at a different element; take a snapshot to "
                "get current refs",
                f"ref {ref} was {_describe(seen, redact)} but now points at "
                f"{_describe(now, redact)}",
            )
        return RefVerdict("ok", ref, "")

    def judge_document(self, tab_id: str, ref: str, document: Any) -> RefVerdict:
        """Stale when ``ref``'s frame now shows another ``document`` than when shown."""
        shown = self.document(tab_id, ref)
        if shown is None or shown == document:
            return RefVerdict("ok", ref, "")
        return RefVerdict(
            "stale",
            ref,
            f"ref {ref} is from a page its frame has since left; take a snapshot to "
            "get current refs",
        )


def frame_prefix(ref: str) -> str:
    """The frame part of ``ref`` (``f2`` of ``f2e7``); empty for a plain ``e7``."""
    match = _FRAME_PREFIX.match(ref)
    return match.group(0) if match else ""
