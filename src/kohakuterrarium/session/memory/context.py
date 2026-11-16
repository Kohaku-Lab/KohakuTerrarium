"""Opaque, store-local anchors and bounded chronological memory navigation.

A token is an unguessable process-local handle: callers never supply storage
keys. All lookups and reads run on the SessionStore's affinity executor.
"""

import secrets
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.session.history.branch_paths import index_parent_paths
from kohakuterrarium.session.history.paging import _ordered_event_refs
from kohakuterrarium.session.memory.blocks import _anchor, _content_to_text
from kohakuterrarium.session.memory.visibility import BranchVisibility
from kohakuterrarium.session.vault.io import key_text, scan_keys

MAX_ANCHORS = 8192
MAX_WINDOW_SIDE = 10
DEFAULT_WINDOW_SIDE = 3
MAX_SCANNED_PER_SIDE = 8192
MAX_EVENT_CHARS = 900

# Activity and bookkeeping records still exist in the physical log but are
# not useful as context steps. Keep tool activity and errors even if their
# bodies do not happen to appear in the search index.
CONTEXT_TYPES = frozenset(
    {
        "user_input",
        "user_message",
        "trigger_fired",
        "text",
        "text_chunk",
        "tool_call",
        "tool_result",
        "processing_error",
        "compact_complete",
    }
)


class MemoryContextError(ValueError):
    """A recoverable, caller-visible memory navigation error."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class _Location:
    agent: str
    ref: str
    fingerprint: str
    view: tuple[tuple[int, int], ...]
    part_index: int = 0


@dataclass(frozen=True)
class ContextEvent:
    """Detached, bounded event returned to the Creature."""

    anchor: str
    kind: str
    content: str
    turn_index: int | None
    branch_id: int | None
    truncated: bool
    role: str
    part_index: int = 0


@dataclass(frozen=True)
class ContextWindow:
    """Chronological window with continuity relative to its outer boundaries."""

    events: list[ContextEvent]
    has_before: bool
    has_after: bool


def _preview(event: dict, *, part_index: int | None = None) -> tuple[str, bool]:
    kind = event.get("type")
    if kind in ("text", "text_chunk") and part_index is not None:
        text = _content_to_text(event.get("content"))
        paragraphs = text.split("\n\n") if len(text) > 300 else [text]
        nonblank = [p.strip() for p in paragraphs if p.strip()]
        if 0 <= part_index < len(nonblank):
            text = nonblank[part_index]
        content = text
    elif kind == "tool_call":
        args = event.get("args", {})
        if isinstance(args, dict):
            args = {k: v for k, v in args.items() if k != "_tool_call_id"}
        content = f"{event.get('name', '')} {args}"
    elif kind == "tool_result":
        content = _content_to_text(event.get("error") or event.get("output"))
    else:
        content = _content_to_text(
            event.get("content")
            or event.get("error")
            or event.get("summary")
            or event.get("message")
        )
    if not content.strip():
        content = "(no text payload)"
    truncated = len(content) > MAX_EVENT_CHARS
    if truncated:
        content = content[:MAX_EVENT_CHARS] + f"… ({len(content)} chars total)"
    return content, truncated


class MemoryContextNavigator:
    """Source-validated anchors over one store; no additional database handles."""

    def __init__(self, store):
        self._store = store
        self._anchors: OrderedDict[str, _Location] = OrderedDict()

    def close(self) -> None:
        self._anchors.clear()

    def _remember(self, loc: _Location) -> str:
        token = secrets.token_urlsafe(24)
        self._anchors[token] = loc
        if len(self._anchors) > MAX_ANCHORS:
            self._anchors.popitem(last=False)
        return token

    def register_results(
        self,
        results: list,
        branch_views: dict[str, dict[int, int]],
    ) -> list:
        """Bind search hits to verified physical source records.

        The index stores logical event positions in get_events() order, which
        can differ from numeric physical order on long or imported sessions.
        Reuse get_events()' lexical key order here; the source fingerprint
        guards against unreadable holes and stale index positions.
        """
        index_refs: dict[str, list[str]] = {}
        prepared: list[tuple[Any, _Location]] = []
        for result in results:
            if result.agent not in branch_views:
                raise MemoryContextError(
                    "ANCHOR_SOURCE_MISMATCH",
                    "Search result belongs to an agent outside the selected scope.",
                )
            if (
                type(result.event_position) is not int
                or result.event_position < 0
                or not result.source_fingerprint
            ):
                raise MemoryContextError(
                    "ANCHOR_SOURCE_MISMATCH",
                    "Search index lacks a verifiable source. Reindex and retry.",
                )
            if result.agent not in index_refs:
                prefix = f"{result.agent}:e"
                index_refs[result.agent] = [
                    key_text(key) for key in scan_keys(self._store.events, prefix)
                ]
            refs = index_refs[result.agent]
            pos = result.event_position
            if pos >= len(refs):
                raise MemoryContextError(
                    "ANCHOR_SOURCE_MISMATCH", "Source event is no longer available."
                )
            ref = refs[pos]
            # Reject unusual keys: a valid search match must resolve to one
            # physical event record, not a nested or similarly prefixed key.
            suffix = ref.removeprefix(f"{result.agent}:e")
            if (
                not ref.startswith(f"{result.agent}:e")
                or not suffix
                or not suffix.isascii()
                or not suffix.isdecimal()
            ):
                raise MemoryContextError(
                    "ANCHOR_SOURCE_MISMATCH", "Source event key is invalid."
                )
            try:
                event = self._store.events[ref]
            except Exception as exc:
                raise MemoryContextError(
                    "ANCHOR_SOURCE_MISMATCH", "Source event is unreadable."
                ) from exc
            if (
                not isinstance(event, dict)
                or _anchor(event) != result.source_fingerprint
            ):
                raise MemoryContextError(
                    "ANCHOR_SOURCE_MISMATCH",
                    "Search index and source history disagree. Reindex and retry.",
                )
            view = tuple(sorted(branch_views[result.agent].items()))
            prepared.append(
                (
                    result,
                    _Location(
                        result.agent,
                        ref,
                        result.source_fingerprint,
                        view,
                        result.part_index,
                    ),
                )
            )
        # Avoid issuing partially usable anchors if a later hit was stale.
        for result, location in prepared:
            result.anchor = self._remember(location)
        return results

    def _visible(
        self,
        event: dict,
        visibility: BranchVisibility,
        legacy_paths: dict[int, tuple | None] | None,
    ) -> bool:
        if event.get("type") not in CONTEXT_TYPES:
            return False
        record = event
        turn, branch = event.get("turn_index"), event.get("branch_id")
        if (
            type(turn) is int
            and turn > 1
            and type(branch) is int
            and event.get("parent_branch_path") is None
        ):
            eid = event.get("event_id")
            if (
                legacy_paths is None
                or type(eid) is not int
                or eid not in legacy_paths
                or legacy_paths[eid] is None
            ):
                raise MemoryContextError(
                    "LINEAGE_UNAVAILABLE",
                    "Cannot establish the ancestry of this historical event.",
                )
            record = {**event, "parent_branch_path": legacy_paths[eid]}
        return visibility.matches(record)

    def _legacy_paths(self, agent: str) -> dict[int, tuple | None]:
        """Resolve historical ancestry, refusing ambiguous mirrored IDs."""
        events = self._store.get_events(agent)
        paths = index_parent_paths(events)
        seen: set[int] = set()
        for event in events:
            eid = event.get("event_id")
            if type(eid) is not int:
                continue
            if eid in seen:
                paths[eid] = None
            seen.add(eid)
        return paths

    def expand(self, anchor: str, before: int = 3, after: int = 3) -> ContextWindow:
        if type(before) is not int or not 0 <= before <= MAX_WINDOW_SIDE:
            raise MemoryContextError(
                "INVALID_WINDOW",
                f"before must be an integer from 0 to {MAX_WINDOW_SIDE}",
            )
        if type(after) is not int or not 0 <= after <= MAX_WINDOW_SIDE:
            raise MemoryContextError(
                "INVALID_WINDOW",
                f"after must be an integer from 0 to {MAX_WINDOW_SIDE}",
            )
        if (
            not isinstance(anchor, str)
            or len(anchor) > 128
            or anchor not in self._anchors
        ):
            raise MemoryContextError(
                "INVALID_ANCHOR",
                "Unknown or expired memory anchor. Run search_memory(query=...) again.",
            )
        loc = self._anchors[anchor]
        self._anchors.move_to_end(anchor)
        refs = _ordered_event_refs(self._store, loc.agent)
        try:
            index = refs.index(loc.ref)
            match = self._store.events[loc.ref]
        except (ValueError, KeyError, OSError) as exc:
            raise MemoryContextError(
                "ANCHOR_EXPIRED", "Source event was removed or is unreadable."
            ) from exc
        if not isinstance(match, dict) or _anchor(match) != loc.fingerprint:
            raise MemoryContextError(
                "ANCHOR_EXPIRED",
                "Source event changed since the anchor was issued. Search again.",
            )

        visibility = BranchVisibility(dict(loc.view))
        # Replay-derived ancestry is needed only for unstamped legacy rows.
        legacy_paths = None

        def walk(step: int, n: int) -> tuple[list[tuple[str, dict]], bool]:
            nonlocal legacy_paths
            found: list[tuple[str, dict]] = []
            examined = 0
            for ix in range(index + step, len(refs) if step > 0 else -1, step):
                if examined >= MAX_SCANNED_PER_SIDE:
                    raise MemoryContextError(
                        "CONTEXT_SCAN_LIMIT",
                        "Too many filtered history records; choose a closer anchor.",
                    )
                examined += 1
                ref = refs[ix]
                try:
                    event = self._store.events[ref]
                except (KeyError, OSError):
                    continue
                if not isinstance(event, dict):
                    continue
                if (
                    event.get("type") in CONTEXT_TYPES
                    and type(event.get("turn_index")) is int
                    and event["turn_index"] > 1
                    and type(event.get("branch_id")) is int
                    and event.get("parent_branch_path") is None
                    and legacy_paths is None
                ):
                    legacy_paths = self._legacy_paths(loc.agent)
                if self._visible(event, visibility, legacy_paths):
                    found.append((ref, event))
                    if len(found) > n:
                        return found[:n], True
            return found, False

        old, has_before = walk(-1, before)
        new, has_after = walk(1, after)
        ordered = [
            *[(ref, evt, "before") for ref, evt in reversed(old)],
            (loc.ref, match, "match"),
            *[(ref, evt, "after") for ref, evt in new],
        ]
        items: list[ContextEvent] = []
        for ref, event, role in ordered:
            token = (
                anchor
                if role == "match"
                else self._remember(_Location(loc.agent, ref, _anchor(event), loc.view))
            )
            content, truncated = _preview(
                event,
                part_index=loc.part_index if role == "match" else None,
            )
            turn = event.get("turn_index")
            branch = event.get("branch_id")
            items.append(
                ContextEvent(
                    anchor=token,
                    kind=str(event.get("type", "unknown")),
                    content=content,
                    turn_index=turn if type(turn) is int else None,
                    branch_id=branch if type(branch) is int else None,
                    truncated=truncated,
                    role=role,
                    part_index=loc.part_index if role == "match" else 0,
                )
            )
        return ContextWindow(items, has_before, has_after)
