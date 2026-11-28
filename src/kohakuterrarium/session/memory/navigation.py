"""Opaque, source-bound navigation over the selected-lineage Memory index.

Legacy index rows can be searched, but only the explicit offline Session
rebuild can upgrade their derived source-bound Anchors.
"""

import re
import secrets
from collections import OrderedDict
from dataclasses import dataclass

from kohakuterrarium.session.history.branch_paths import (
    InvalidBranchViewError,
    _coerce_branch_view,
    _coerce_path,
    index_parent_paths,
)
from kohakuterrarium.session.history.selection import SelectionCompatibility
from kohakuterrarium.session.memory.blocks import (
    _anchor,
    _content_to_text,
    _extract_blocks,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

MAX_ANCHORS = 2048
MAX_WINDOW_SIDE = 10
MAX_PREVIEW = 900
MAX_SCANNED_PER_SIDE = 256
MAX_LEGACY_PREFIX = 256
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
    view: tuple[tuple[int, int], ...] | None
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
    turn: int | None = None
    branch: int | None = None


@dataclass(frozen=True)
class ContextWindow:
    events: list[ContextEvent]
    has_before: bool | None
    has_after: bool | None
    scope: str = "selected"
    notice: str = ""


def _scope(event: dict) -> tuple[int, int] | None:
    turn, branch = event.get("turn_index"), event.get("branch_id")
    if type(turn) is int and type(branch) is int and turn > 0 and branch > 0:
        return turn, branch
    return None


def _explicit_path(event: dict, turn: int) -> tuple | None:
    raw = event.get("parent_branch_path")
    if not isinstance(raw, (list, tuple)):
        return None
    path = _coerce_path(raw)
    if len(path) != len(raw):
        return None
    parents = {}
    for t, b in path:
        if type(t) is not int or type(b) is not int or not 0 < t < turn or b <= 0:
            return None
        if parents.setdefault(t, b) != b:
            return None
    return tuple(sorted(parents.items()))


def _legacy_paths(store, agent: str, frontier: int) -> dict[int, tuple]:
    """Resolve ancestry from a bounded physical prefix, using unique row positions."""
    rows = []
    for seq in range(min(frontier, MAX_LEGACY_PREFIX)):
        try:
            event = store.events[f"{agent}:e{seq:06d}"]
        except KeyError:
            continue
        except Exception:
            break
        if not isinstance(event, dict):
            break
        rows.append(
            {
                "event_id": seq,
                "turn_index": event.get("turn_index"),
                "branch_id": event.get("branch_id"),
                "parent_branch_path": event.get("parent_branch_path"),
            }
        )
    return index_parent_paths(rows)


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
        self._anchors: OrderedDict[str, _Location] = OrderedDict()

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
                    first_error = MemoryNavigationError(
                        "ANCHOR_REBUILD_REQUIRED",
                        "Existing index row has no physical source reference. "
                        "Stop session writers, then run 'kt memory rebuild SESSION'.",
                    )
                continue
            try:
                location = self._direct_hit(hit, query)
            except Exception as exc:
                if not isinstance(exc, MemoryNavigationError):
                    logger.warning(
                        "Memory source evidence unavailable",
                        agent=hit.agent,
                        error=str(exc),
                    )
                    exc = MemoryNavigationError(
                        "UNVERIFIABLE_SOURCE", "Unable to read source evidence."
                    )
                if first_error is None:
                    first_error = exc
                continue
            hit.anchor = self._remember(location)
        if first_error is not None:
            raise first_error
        return hits

    def expand(
        self,
        anchor: str,
        before: int = 3,
        after: int = 3,
        offset: int | None = None,
        scope: str = "selected",
    ) -> ContextWindow:
        if scope not in ("selected", "raw"):
            raise MemoryNavigationError(
                "INVALID_SCOPE", "scope must be selected or raw"
            )
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
        return self._expand_direct(anchor, loc, before, after, offset, scope)

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

    def _index_view(self, agent: str) -> tuple[tuple[int, int], ...] | None:
        """Capture the selected index view if available; raw navigation needs none."""
        if self._memory is None or not self._memory._valid_index(agent, "keywords"):
            return None
        checkpoint = self._memory._state.get(f"{agent}:cursor:keywords")
        if not isinstance(checkpoint, dict) or checkpoint.get("all_branches"):
            return None
        raw = checkpoint.get("branches")
        if not isinstance(raw, dict):
            return None
        try:
            selected = _coerce_branch_view(
                {
                    turn: branch
                    for turn, branch in raw.items()
                    if (turn, branch) not in (("0", 0), (0, 0))
                }
            )
            requested = _coerce_branch_view(
                self._store.state.get(f"{agent}:branch_view")
            )
        except InvalidBranchViewError:
            return None
        if any(selected.get(turn) != branch for turn, branch in requested.items()):
            return None
        return tuple(sorted(selected.items()))

    def _direct_hit(self, hit, query: str) -> _Location:
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
        try:
            view = self._index_view(hit.agent)
        except Exception as exc:
            logger.warning(
                "Selected Memory view unavailable", agent=hit.agent, error=str(exc)
            )
            view = None
        return _Location(hit.agent, hit.source_fingerprint, view, ref, query[:100])

    def _expand_direct(
        self,
        anchor: str,
        loc: _Location,
        before: int,
        after: int,
        offset: int | None,
        scope: str,
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
        if not isinstance(raw, dict) or _anchor(raw) != loc.fingerprint:
            raise MemoryNavigationError("ANCHOR_EXPIRED", "Anchor source changed.")
        frontier = self._store._event_seq.get(loc.agent)
        if type(frontier) is not int or frontier <= start_seq:
            raise MemoryNavigationError(
                "UNVERIFIABLE_SOURCE", "Physical event high-watermark unavailable."
            )

        view = dict(loc.view or ()) if scope == "selected" else {}
        compatibility = SelectionCompatibility(view)
        paths = None

        def visible(event: dict, seq: int) -> bool | None:
            nonlocal paths
            if event.get("type") not in CONTEXT_TYPES:
                return False
            if scope == "raw":
                return True
            turn, branch = event.get("turn_index"), event.get("branch_id")
            if turn is None and branch is None:
                return True
            if type(turn) is int and type(branch) is int and (turn, branch) == (0, 0):
                return True
            if _scope(event) is None or loc.view is None:
                return None
            if view.get(turn) != branch:
                return False
            ancestry = event.get("parent_branch_path")
            if ancestry is None or ancestry == [] or ancestry == ():
                if turn == 1:
                    path = ()
                elif seq >= MAX_LEGACY_PREFIX:
                    return None
                else:
                    if paths is None:
                        paths = _legacy_paths(self._store, loc.agent, frontier)
                    path = paths.get(seq)
            else:
                path = _explicit_path(event, turn)
            return None if path is None else compatibility.matches(path, turn)

        if visible(raw, start_seq) is not True:
            return ContextWindow(
                [],
                None,
                None,
                scope,
                "Anchor is outside the selected view or its lineage is unknown. "
                'Use scope="raw" to inspect the physical event.',
            )

        def side(direction: int, count: int):
            found = []
            seq = start_seq + direction
            examined = 0
            while 0 <= seq < frontier and len(found) <= count:
                examined += 1
                if examined > MAX_SCANNED_PER_SIDE:
                    return found[:count], None
                ref = f"{loc.agent}:e{seq:06d}"
                try:
                    event = self._store.events[ref]
                except KeyError:
                    event = None
                if isinstance(event, dict):
                    eligible = visible(event, seq)
                    if eligible is None:
                        return found[:count], None
                    if eligible:
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
                # Unicode lower()/casefold() may expand code points (e.g. İ),
                # shifting offsets away from the original source coordinates.
                # A literal ignore-case regex returns positions in *content*.
                focus_match = re.search(re.escape(loc.focus), content, re.IGNORECASE)
                if focus_match and focus_match.start() >= MAX_PREVIEW:
                    start = max(0, focus_match.start() - 120)
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
                    event.get("turn_index"),
                    event.get("branch_id"),
                )
            )
        return ContextWindow(out, has_before, has_after, scope)
