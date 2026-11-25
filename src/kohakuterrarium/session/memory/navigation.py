"""Opaque, source-bound navigation over the selected-lineage Memory index.

Legacy index rows can be searched, but only the explicit offline Session
rebuild can upgrade their derived source-bound Anchors.
"""

import secrets
from collections import OrderedDict
from dataclasses import dataclass

from kohakuterrarium.session.memory.blocks import (
    _anchor,
    _content_to_text,
    _extract_blocks,
)

MAX_ANCHORS = 2048
MAX_WINDOW_SIDE = 10
MAX_PREVIEW = 900
MAX_SCANNED_PER_SIDE = 256
CONTEXT_TYPES = frozenset(
    {
        "user_input",
        "user_message",
        "text",
        "text_chunk",
        "tool_call",
        "tool_result",
        "trigger_fired",
        "processing_error",
        "compact_complete",
    }
)


class MemoryNavigationError(ValueError):
    """A stable, caller-visible source or cursor failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class _Location:
    agent: str
    fingerprint: str
    view: tuple[tuple[int, int], ...]
    ref: str
    focus: str = ""


@dataclass(frozen=True)
class ContextEvent:
    anchor: str
    kind: str
    content: str
    role: str
    truncated: bool
    start: int
    end: int
    total: int


@dataclass(frozen=True)
class ContextWindow:
    events: list[ContextEvent]
    has_before: bool | None
    has_after: bool | None


def _certify(events: list[dict]) -> None:
    """Legacy ID-keyed lineage can be trusted only for ordered unique IDs."""
    previous = 0
    ancestry_by_scope: dict[tuple[int, int], tuple] = {}
    for event in events:
        eid = event.get("event_id")
        if type(eid) is not int or eid <= previous:
            raise MemoryNavigationError(
                "UNVERIFIABLE_SOURCE",
                "Non-monotonic/mirrored event IDs cannot certify a legacy anchor.",
            )
        previous = eid
        turn, branch = event.get("turn_index"), event.get("branch_id")
        raw_path = event.get("parent_branch_path")
        if type(turn) is not int or type(branch) is not int or raw_path is None:
            continue
        if type(raw_path) not in (list, tuple):
            raise MemoryNavigationError(
                "UNVERIFIABLE_SOURCE", "Invalid recorded branch ancestry."
            )
        path: list[tuple[int, int]] = []
        last_turn = 0
        for pair in raw_path:
            if (
                not isinstance(pair, (list, tuple))
                or len(pair) != 2
                or type(pair[0]) is not int
                or type(pair[1]) is not int
                or not last_turn < pair[0] < turn
                or pair[1] <= 0
            ):
                raise MemoryNavigationError(
                    "UNVERIFIABLE_SOURCE", "Invalid recorded branch ancestry."
                )
            path.append(tuple(pair))
            last_turn = pair[0]
        key = turn, branch
        path_tuple = tuple(path)
        if key in ancestry_by_scope and ancestry_by_scope[key] != path_tuple:
            raise MemoryNavigationError(
                "UNVERIFIABLE_SOURCE",
                "Multiple incompatible ancestries reuse one legacy turn/branch.",
            )
        ancestry_by_scope[key] = path_tuple


def _event_text(event: dict) -> str:
    """Show the complete physical event, not one searchable paragraph block."""
    kind = event.get("type")
    if kind == "tool_call":
        return f"{event.get('name', '')} {event.get('args', {})}"
    if kind == "tool_result":
        return _content_to_text(event.get("error") or event.get("output"))
    return _content_to_text(
        event.get("content") or event.get("error") or event.get("summary") or ""
    )


def _source_matches_indexed_block(event: dict, hit, part: int) -> bool:
    """Compare the hit to its exact source event using the index's projection.

    The in-round cursor permits a single event to be replayed as an index
    candidate, without a full conversation replay or a duplicate extractor.
    """
    if part < 0:
        return False
    try:
        blocks = _extract_blocks(
            hit.agent, [event], cursor={"in_round": True, "round": 1}
        )
    except (TypeError, AttributeError, ValueError):
        return False
    return (
        part < len(blocks)
        and blocks[part].block_type == hit.block_type
        and blocks[part].content == hit.content
    )


class MemoryNavigator:
    """Process-local cursors. All calls must run on the store affinity thread."""

    def __init__(self, store, memory=None):
        self._store = store
        self._memory = memory
        # One process-local physical frontier per certified Agent; a later
        # append invalidates trust without scanning that Agent on every query.
        self._certified_sources: dict[str, int | None] = {}
        self._anchors: OrderedDict[str, _Location] = OrderedDict()

    def certify_source(self, agent: str, trusted: bool) -> None:
        """Remember the Store frontier checked during the index refresh."""
        frontier = self._store._event_seq.get(agent)
        self._certified_sources[agent] = (
            frontier if trusted and type(frontier) is int else None
        )

    def source_certified(self, agent: str) -> bool:
        """O(1) trust check; invalidate on new physical event appends."""
        frontier = self._store._event_seq.get(agent)
        return type(frontier) is int and self._certified_sources.get(agent) == frontier

    def close(self) -> None:
        self._anchors.clear()

    def _remember(self, loc: _Location) -> str:
        token = secrets.token_urlsafe(24)
        self._anchors[token] = loc
        while len(self._anchors) > MAX_ANCHORS:
            self._anchors.popitem(last=False)
        return token

    def register_hits(self, hits: list, query: str = "") -> list:
        """Only physically certified hits can receive anchors.

        Each hit is independently validated; an incompatible historical
        index cannot authorize an unverified physical source.
        """
        first_error = None
        for hit in hits:
            if not getattr(hit, "source_ref", "") or not getattr(
                hit, "source_fingerprint", ""
            ):
                if first_error is None:
                    if self.source_certified(hit.agent):
                        first_error = MemoryNavigationError(
                            "ANCHOR_REBUILD_REQUIRED",
                            "Existing index row has no certified source. "
                            "Stop session writers, then run 'kt memory rebuild SESSION'.",
                        )
                    else:
                        first_error = MemoryNavigationError(
                            "UNVERIFIABLE_SOURCE",
                            "Physical source has unsupported ordering or ancestry.",
                        )
                continue
            try:
                location = self._direct_hit(hit, query)
            except MemoryNavigationError as exc:
                if first_error is None:
                    first_error = exc
                continue
            hit.anchor = self._remember(location)
        if first_error is not None:
            raise first_error
        return hits

    def expand(
        self, anchor: str, before: int = 3, after: int = 3, offset: int | None = None
    ) -> ContextWindow:
        if (
            type(before) is not int
            or type(after) is not int
            or not 0 <= before <= MAX_WINDOW_SIDE
            or not 0 <= after <= MAX_WINDOW_SIDE
        ):
            raise MemoryNavigationError("INVALID_WINDOW", "before/after must be 0..10")
        if offset is not None and (type(offset) is not int or offset < 0):
            raise MemoryNavigationError("INVALID_OFFSET", "offset must be nonnegative")
        if not isinstance(anchor, str) or anchor not in self._anchors:
            raise MemoryNavigationError("INVALID_ANCHOR", "Unknown or expired anchor")
        loc = self._anchors[anchor]
        return self._expand_direct(anchor, loc, before, after, offset)

    @staticmethod
    def _ref_sequence(agent: str, ref: str) -> int:
        prefix = f"{agent}:e"
        suffix = (
            ref[len(prefix) :]
            if isinstance(ref, str) and ref.startswith(prefix)
            else ""
        )
        if (
            not suffix
            or not suffix.isascii()
            or not suffix.isdecimal()
            or f"{int(suffix):06d}" != suffix
        ):
            raise MemoryNavigationError(
                "UNVERIFIABLE_SOURCE", "Invalid physical event reference."
            )
        return int(suffix)

    @staticmethod
    def _visible_scoped(event: dict, view: dict[int, int]) -> bool:
        """Conservative physical visibility without Replay's message projection."""
        if event.get("type") not in CONTEXT_TYPES:
            return False
        turn, branch = event.get("turn_index"), event.get("branch_id")
        if turn is None and branch is None:
            return True
        if type(turn) is not int or type(branch) is not int:
            return False
        if (turn, branch) == (0, 0):
            return True
        if turn < 1 or branch < 1 or view.get(turn) != branch:
            return False
        raw = event.get("parent_branch_path")
        if raw is None:
            return turn == 1
        if not isinstance(raw, (tuple, list)):
            return False
        previous = 0
        for pair in raw:
            if (
                not isinstance(pair, (tuple, list))
                or len(pair) != 2
                or type(pair[0]) is not int
                or type(pair[1]) is not int
                or not previous < pair[0] < turn
                or pair[1] <= 0
                or view.get(pair[0]) != pair[1]
            ):
                return False
            previous = pair[0]
        return True

    def _index_view(self, agent: str) -> tuple[tuple[int, int], ...]:
        if self._memory is None:
            raise MemoryNavigationError(
                "UNVERIFIABLE_SOURCE", "Memory index was not attached."
            )
        checkpoint = self._memory._state.get(f"{agent}:cursor:keywords")
        if self._memory._state.get(f"{agent}:cursor:keywords:pending", False):
            raise MemoryNavigationError(
                "UNVERIFIABLE_SOURCE", "Keyword index update is incomplete."
            )
        if not isinstance(checkpoint, dict):
            raise MemoryNavigationError(
                "UNVERIFIABLE_SOURCE", "Missing selected-index checkpoint."
            )
        if checkpoint.get("all_branches"):
            raise MemoryNavigationError(
                "ANCHOR_REBUILD_REQUIRED",
                "This index uses a legacy all-branch projection; "
                "run 'kt memory rebuild SESSION' to restore selected-lineage indexing.",
            )
        raw = checkpoint.get("branches")
        if not isinstance(raw, dict):
            raise MemoryNavigationError(
                "UNVERIFIABLE_SOURCE", "Selected-lineage proof is not available."
            )
        selected: dict[int, int] = {}
        for turn, branch in raw.items():
            # Historical bookkeeping records at (0, 0) are globally visible
            # but do not represent a conversational branch selection.
            if turn == "0" and branch == 0:
                continue
            if (
                not isinstance(turn, str)
                or not turn.isascii()
                or not turn.isdecimal()
                or type(branch) is not int
                or int(turn) < 1
                or branch < 1
            ):
                raise MemoryNavigationError(
                    "UNVERIFIABLE_SOURCE", "Invalid indexed branch selection."
                )
            selected[int(turn)] = branch
        # The baseline selected-lineage index resolves the latest compatible
        # branch, not an explicitly persisted older selection. Do not extend
        # such a mismatched query hit into another branch's raw context.
        requested = self._store.state.get(f"{agent}:branch_view")
        if requested:
            if not isinstance(requested, dict):
                raise MemoryNavigationError(
                    "UNVERIFIABLE_SOURCE", "Selected branch marker is invalid."
                )
            for raw_turn, branch in requested.items():
                if (
                    not str(raw_turn).isascii()
                    or not str(raw_turn).isdecimal()
                    or type(branch) is not int
                    or selected.get(int(raw_turn)) != branch
                ):
                    raise MemoryNavigationError(
                        "UNVERIFIABLE_SOURCE",
                        "Indexed lineage differs from the active selected branch.",
                    )
        return tuple(sorted(selected.items()))

    def _direct_hit(self, hit, query: str) -> _Location:
        if not self.source_certified(hit.agent):
            raise MemoryNavigationError(
                "UNVERIFIABLE_SOURCE", "Current event source is not certifiable."
            )
        ref = hit.source_ref
        self._ref_sequence(hit.agent, ref)
        self._store._flush_events_cache()
        try:
            event = self._store.events[ref]
        except (KeyError, OSError) as exc:
            raise MemoryNavigationError(
                "ANCHOR_EXPIRED", "Indexed source event is unavailable."
            ) from exc
        if not isinstance(event, dict) or _anchor(event) != hit.source_fingerprint:
            raise MemoryNavigationError(
                "STALE_INDEX", "Indexed source and physical event do not agree."
            )
        view = self._index_view(hit.agent)
        if not self._visible_scoped(event, dict(view)):
            raise MemoryNavigationError(
                "UNVERIFIABLE_SOURCE", "Hit is outside certified selected lineage."
            )
        try:
            part = int(hit.block_id.rsplit(":p", 1)[1])
        except (ValueError, IndexError) as exc:
            raise MemoryNavigationError(
                "STALE_INDEX", "Indexed paragraph part is invalid."
            ) from exc
        if not _source_matches_indexed_block(event, hit, part):
            raise MemoryNavigationError(
                "STALE_INDEX", "Indexed block is not derived from its physical source."
            )
        # Paragraph index only proves the hit belongs to the event. The
        # resulting Anchor identifies the whole physical event, not a part.
        return _Location(hit.agent, hit.source_fingerprint, view, ref, query[:100])

    def _expand_direct(
        self, anchor: str, loc: _Location, before: int, after: int, offset: int | None
    ) -> ContextWindow:
        """Use exact-key probes with strict bounds on keys examined."""
        start_seq = self._ref_sequence(loc.agent, loc.ref)
        self._store._flush_events_cache()
        try:
            raw = self._store.events[loc.ref]
        except (OSError, KeyError) as exc:
            raise MemoryNavigationError(
                "ANCHOR_EXPIRED", "Physical event was deleted."
            ) from exc
        view = dict(loc.view)
        if (
            not isinstance(raw, dict)
            or _anchor(raw) != loc.fingerprint
            or not self._visible_scoped(raw, view)
        ):
            raise MemoryNavigationError(
                "ANCHOR_EXPIRED", "Anchor source or visibility changed."
            )
        frontier = self._store._event_seq.get(loc.agent)
        if type(frontier) is not int or frontier <= start_seq:
            raise MemoryNavigationError(
                "UNVERIFIABLE_SOURCE", "Physical event high-watermark unavailable."
            )

        def side(direction: int, count: int):
            found = []
            seq = start_seq + direction
            examined = 0
            while 0 <= seq < frontier and len(found) <= count:
                examined += 1
                if examined > MAX_SCANNED_PER_SIDE:
                    if len(found) < count:
                        raise MemoryNavigationError(
                            "CONTEXT_SCAN_LIMIT",
                            "Requested neighbors exceed the physical scan budget.",
                        )
                    # All requested content is available. Do not fail the
                    # window merely because the presence of *more* visible
                    # history cannot be established within the budget.
                    return found[:count], None
                ref = f"{loc.agent}:e{seq:06d}"
                try:
                    event = self._store.events[ref]
                except KeyError:
                    event = None
                if isinstance(event, dict) and self._visible_scoped(event, view):
                    found.append((ref, event))
                seq += direction
            return found[:count], len(found) > count

        prior, has_before = side(-1, before)
        later, has_after = side(1, after)
        ordered = [
            *((ref, evt, "before") for ref, evt in reversed(prior)),
            (loc.ref, raw, "match"),
            *((ref, evt, "after") for ref, evt in later),
        ]
        # The in-use cursor must survive the new neighbor tokens issued
        # during the same expansion, even when the cache is full.
        self._anchors.move_to_end(anchor)
        out = []
        for ref, event, role in ordered:
            matched = role == "match"
            content = _event_text(event)
            start = (offset or 0) if matched else 0
            if matched and offset is None and loc.focus:
                focus_at = content.lower().find(loc.focus.lower())
                if focus_at >= MAX_PREVIEW:
                    start = max(0, focus_at - 120)
            if start >= len(content) and start > 0:
                raise MemoryNavigationError("INVALID_OFFSET", "offset exceeds event")
            end = min(len(content), start + MAX_PREVIEW)
            token = (
                anchor
                if matched
                else self._remember(_Location(loc.agent, _anchor(event), loc.view, ref))
            )
            out.append(
                ContextEvent(
                    token,
                    str(event.get("type", "")),
                    content[start:end],
                    role,
                    start > 0 or end < len(content),
                    start,
                    end,
                    len(content),
                )
            )
        return ContextWindow(out, has_before, has_after)
