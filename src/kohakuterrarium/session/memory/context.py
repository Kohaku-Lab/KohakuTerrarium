"""Opaque, store-local anchors and bounded chronological memory navigation.

A token is an unguessable process-local handle: callers never supply storage
keys. All lookups and reads run on the SessionStore's affinity executor.
"""

import secrets
from array import array
from bisect import bisect_left
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.session.history.branch_paths import (
    index_eligible_parent_paths_by_position,
)
from kohakuterrarium.session.history.semantics import (
    BranchScope,
    classify_branch_scope,
    strict_parent_branch_path,
)
from kohakuterrarium.session.history.paging import _ordered_event_refs
from kohakuterrarium.session.memory.blocks import _anchor, _content_to_text
from kohakuterrarium.session.memory.preview import (
    MAX_FOCUS_CHARS,
    TextSlice,
    preview_text,
)
from kohakuterrarium.session.memory.visibility import BranchVisibility

MAX_ANCHORS = 8192
MAX_VIEW_PAIRS = 120_000
MAX_CACHED_VIEWS = 32
MAX_WINDOW_SIDE = 10
MAX_SCANNED_PER_SIDE = 8192
MAX_EVENT_CHARS = 900

# Activity and bookkeeping records still exist in the physical log but are
# not useful as context steps. Keep tool activity and errors even if their
# bodies do not happen to appear in the search index.
CONTEXT_TYPES = frozenset(
    {
        "user_input",
        "user_input_injected",
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


class _PhysicalEventRefs:
    """Compact numeric physical locators, not logical event IDs."""

    def __init__(self, agent: str, numbers: array):
        self.agent = agent
        self.numbers = numbers

    def __len__(self) -> int:
        return len(self.numbers)

    def __getitem__(self, position: int) -> str:
        return f"{self.agent}:e{self.numbers[position]:06d}"

    def index(self, ref: str) -> int:
        prefix = f"{self.agent}:e"
        suffix = ref[len(prefix) :] if ref.startswith(prefix) else ""
        if not suffix.isascii() or not suffix.isdecimal():
            raise ValueError("unknown physical event")
        number = int(suffix)
        at = bisect_left(self.numbers, number)
        if at >= len(self.numbers) or self.numbers[at] != number:
            raise ValueError("unknown physical event")
        return at


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
    # None means a whole event (for neighbor navigation), while zero is the
    # first paragraph of a block returned by search.
    part_index: int | None = None
    focus: str = ""


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
    content_start: int = 0
    content_end: int = 0
    content_total: int = 0


@dataclass(frozen=True)
class ContextWindow:
    """Chronological window with continuity relative to its outer boundaries."""

    events: list[ContextEvent]
    has_before: bool
    has_after: bool


def _preview(
    event: dict,
    *,
    part_index: int | None = None,
    focus: str = "",
    offset: int | None = None,
) -> TextSlice:
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
    return preview_text(content, MAX_EVENT_CHARS, focus=focus, offset=offset)


class MemoryContextNavigator:
    """Source-validated anchors over one store; no additional database handles."""

    def __init__(self, store):
        self._store = store
        self._anchors: OrderedDict[str, _Location] = OrderedDict()
        self._view_cache: OrderedDict[str, tuple[tuple[int, int], ...]] = OrderedDict()
        # Track unique snapshots, not the number of anchors referring to them.
        self._view_refs: dict[int, tuple[tuple, int]] = {}
        self._view_pairs = 0
        # One per-store, per-agent physical-key catalogue. Only KT-managed
        # append-only event keys are assumed; arbitrary in-place SQL edits
        # are outside the source-freshness contract.
        # Cache proofs use the Store's existing accepted-append notifications:
        # observed local writes must account for *all* global row growth
        # before a peer-only change can be ignored. Foreign Store writers or
        # failed/gapped appends always fall back to physical enumeration.
        self._observed_appends = 0
        self._observed_agent_appends: dict[str, int] = {}
        self._append_subscriber = self._observe_append
        subscribers = getattr(store, "_event_subscribers", None)
        if isinstance(subscribers, list):
            subscribers.append(self._append_subscriber)
        self._physical_refs: dict[str, tuple[int, _PhysicalEventRefs, int, int]] = {}
        self._legacy_cache: dict[str, tuple[int, dict[str, tuple]]] = {}

    def _observe_append(self, key: str, _data: dict) -> None:
        # Called only after SessionStore has accepted the event write.
        agent, marker, suffix = key.rpartition(":e")
        if marker and agent and suffix.isascii() and suffix.isdecimal():
            self._observed_appends += 1
            self._observed_agent_appends[agent] = (
                self._observed_agent_appends.get(agent, 0) + 1
            )

    def close(self) -> None:
        subscribers = getattr(self._store, "_event_subscribers", None)
        if isinstance(subscribers, list):
            try:
                subscribers.remove(self._append_subscriber)
            except ValueError:
                pass
        self._physical_refs.clear()
        self._legacy_cache.clear()
        self._anchors.clear()
        self._view_cache.clear()
        self._view_refs.clear()
        self._view_pairs = 0

    def _freeze_view(
        self, agent: str, selected: dict[int, int]
    ) -> tuple[tuple[int, int], ...]:
        view = tuple(sorted(selected.items()))
        cached = self._view_cache.get(agent)
        if cached is not None and cached == view:
            self._view_cache.move_to_end(agent)
            return cached
        self._view_cache[agent] = view
        self._view_cache.move_to_end(agent)
        if len(self._view_cache) > MAX_CACHED_VIEWS:
            self._view_cache.popitem(last=False)
        return view

    def _evict_oldest(self) -> None:
        _, loc = self._anchors.popitem(last=False)
        key = id(loc.view)
        view, count = self._view_refs[key]
        if count > 1:
            self._view_refs[key] = (view, count - 1)
        else:
            self._view_pairs -= len(view)
            del self._view_refs[key]
            if self._view_cache.get(loc.agent) is view:
                self._view_cache.pop(loc.agent, None)

    def _remember(self, loc: _Location) -> str:
        if len(loc.view) > MAX_VIEW_PAIRS:
            raise MemoryContextError(
                "ANCHOR_BUDGET",
                "Selected lineage exceeds the memory navigation budget.",
            )
        token = secrets.token_urlsafe(24)
        self._anchors[token] = loc
        key = id(loc.view)
        if key in self._view_refs:
            view, count = self._view_refs[key]
            self._view_refs[key] = (view, count + 1)
        else:
            self._view_refs[key] = (loc.view, 1)
            self._view_pairs += len(loc.view)
        while len(self._anchors) > MAX_ANCHORS or self._view_pairs > MAX_VIEW_PAIRS:
            self._evict_oldest()
        return token

    def register_results(
        self,
        results: list,
        branch_views: dict[str, dict[int, int]],
        query: str = "",
    ) -> list:
        """Bind search hits to verified physical source records.

        Every hit carries a physical ref plus a fingerprint. Neither logical
        event IDs nor ordinal positions are needed, so unreadable neighbors
        cannot shift the source location.
        """
        prepared: list[tuple[Any, _Location]] = []
        views: dict[str, tuple[tuple[int, int], ...]] = {}
        for result in results:
            if result.agent not in branch_views:
                raise MemoryContextError(
                    "ANCHOR_SOURCE_MISMATCH",
                    "Search result belongs to an agent outside the selected scope.",
                )
            ref = result.source_ref
            prefix = f"{result.agent}:e"
            suffix = (
                ref[len(prefix) :]
                if isinstance(ref, str) and ref.startswith(prefix)
                else ""
            )
            if (
                not suffix
                or not suffix.isascii()
                or not suffix.isdecimal()
                or not result.source_fingerprint
            ):
                raise MemoryContextError(
                    "ANCHOR_SOURCE_MISMATCH",
                    "Search index lacks a verifiable source reference. Reindex and retry.",
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
            if result.agent not in views:
                views[result.agent] = self._freeze_view(
                    result.agent, branch_views[result.agent]
                )
            view = views[result.agent]
            prepared.append(
                (
                    result,
                    _Location(
                        result.agent,
                        ref,
                        result.source_fingerprint,
                        view,
                        result.part_index,
                        query[:MAX_FOCUS_CHARS],
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
        ref: str,
        visibility: BranchVisibility,
        legacy_paths: dict[str, tuple] | None,
    ) -> bool:
        if event.get("type") not in CONTEXT_TYPES:
            return False
        record = event
        turn, branch = event.get("turn_index"), event.get("branch_id")
        if classify_branch_scope(turn, branch) is BranchScope.INVALID:
            return False
        raw_parent = event.get("parent_branch_path")
        if (
            raw_parent is not None
            and strict_parent_branch_path(raw_parent, turn=turn) is None
        ):
            return False
        if (
            type(turn) is int
            and turn > 1
            and type(branch) is int
            and event.get("parent_branch_path") is None
        ):
            if (
                legacy_paths is None
                or ref not in legacy_paths
                or legacy_paths[ref] is None
            ):
                raise MemoryContextError(
                    "LINEAGE_UNAVAILABLE",
                    "Cannot establish the ancestry of this historical event.",
                )
            record = {**event, "parent_branch_path": legacy_paths[ref]}
        return visibility.matches(record)

    def _legacy_paths(self, agent: str) -> dict[str, tuple]:
        """Cache old-history ancestry until the source's physical count changes."""
        total = len(self._store.events)
        cached = self._legacy_cache.get(agent)
        if cached is not None and cached[0] == total:
            return cached[1]
        events = self._store.get_memory_events(agent)
        paths = index_eligible_parent_paths_by_position(events)
        found = {
            event.source_ref: paths[position]
            for position, event in enumerate(events)
            if position in paths
        }
        self._legacy_cache[agent] = (total, found)
        return found

    def _refs_for(self, agent: str) -> _PhysicalEventRefs:
        """Reuse proven local append suffixes, else enumerate physical keys.

        Accepted KT appends are observed through SessionStore's existing
        notification hook. Only when those observed appends explain *all*
        global row growth is it safe to conclude no other writer affected
        this namespace. Failed sequence allocations never send notifications,
        so any resulting hole triggers enumeration rather than false closure.
        """
        self._store._flush_events_cache()
        total = len(self._store.events)
        observed = self._observed_appends
        observed_agent = self._observed_agent_appends.get(agent, 0)
        cached = self._physical_refs.get(agent)
        if cached is not None:
            old_count, refs, old_observed, old_observed_agent = cached
            if total == old_count:
                return refs
            if total > old_count and observed - old_observed == total - old_count:
                # The Store itself saw every accepted physical append since
                # this catalogue. No Alice changes means Bob-only appends
                # cannot force a full Alice history scan.
                agent_growth = observed_agent - old_observed_agent
                if agent_growth >= 0:
                    next_seq = (refs.numbers[-1] + 1) if refs.numbers else 0
                    for seq in range(next_seq, next_seq + agent_growth):
                        ref = f"{agent}:e{seq:06d}"
                        if ref not in self._store.events:
                            # A failed allocation left a hole; this source is
                            # not a consecutive suffix, so enumerate instead.
                            break
                        refs.numbers.append(seq)
                    else:
                        self._physical_refs[agent] = (
                            total,
                            refs,
                            observed,
                            observed_agent,
                        )
                        if agent_growth:
                            self._legacy_cache.pop(agent, None)
                        return refs
        numbers = array("Q")
        prefix = f"{agent}:e"
        for ref in _ordered_event_refs(self._store, agent):
            numbers.append(int(ref[len(prefix) :]))
        refs = _PhysicalEventRefs(agent, numbers)
        self._physical_refs[agent] = (total, refs, observed, observed_agent)
        self._legacy_cache.pop(agent, None)
        return refs

    def expand(
        self,
        anchor: str,
        before: int = 3,
        after: int = 3,
        offset: int | None = None,
    ) -> ContextWindow:
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
        if offset is not None and (type(offset) is not int or offset < 0):
            raise MemoryContextError(
                "INVALID_CONTENT_OFFSET", "offset must be a nonnegative integer"
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
        refs = self._refs_for(loc.agent)
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
                if self._visible(event, ref, visibility, legacy_paths):
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
            try:
                preview = _preview(
                    event,
                    part_index=loc.part_index if role == "match" else None,
                    focus=loc.focus if role == "match" and offset is None else "",
                    offset=offset if role == "match" else None,
                )
            except ValueError as exc:
                raise MemoryContextError("INVALID_CONTENT_OFFSET", str(exc)) from exc
            turn = event.get("turn_index")
            branch = event.get("branch_id")
            items.append(
                ContextEvent(
                    anchor=token,
                    kind=str(event.get("type", "unknown")),
                    content=preview.content,
                    turn_index=turn if type(turn) is int else None,
                    branch_id=branch if type(branch) is int else None,
                    truncated=preview.truncated,
                    role=role,
                    part_index=(
                        loc.part_index
                        if role == "match" and loc.part_index is not None
                        else 0
                    ),
                    content_start=preview.start,
                    content_end=preview.end,
                    content_total=preview.total,
                )
            )
        return ContextWindow(items, has_before, has_after)
