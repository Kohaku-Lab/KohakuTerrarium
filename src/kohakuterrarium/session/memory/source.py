"""A bounded append-only freshness contract for published memory indexes.

KT owns *normal SessionStore append_event and fork* provenance. A physical row
count plus Session ID detects those changes, including mirrored duplicate
logical event IDs. In-place SQL edits, manual event-key deletion/replacement,
or externally mutated SQLite files are not guaranteed detectable: callers must
explicitly reindex/reopen those unsupported modifications.

This is derived cache data in the existing memory_state table, not a new source
format, writer protocol, watchdog or persistent state machine.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.session.history.branch_paths import (
    index_eligible_parent_paths_by_position,
)
from kohakuterrarium.session.history.replay import resolve_selected_branches
from kohakuterrarium.session.history.semantics import (
    BranchScope,
    classify_branch_scope,
    strict_parent_branch_path,
)
from kohakuterrarium.session.memory.blocks import _anchor
from kohakuterrarium.session.store.events.read import MemoryEvent


@dataclass(frozen=True)
class SourceFrontier:
    session_id: str
    physical_count: int


class TailHistory(Sequence):
    """Virtual full event sequence exposing only a validated append suffix.

    Extractor/indexer checkpoints access the old last event, the new suffix,
    and the total count. Any unplanned historical access fails closed rather
    than accidentally decoding the previous entire source.
    """

    def __init__(self, count: int, previous: dict | None, suffix: list[dict]):
        self.old_count = count
        self.previous = previous
        self.suffix = suffix

    def __len__(self) -> int:
        return self.old_count + len(self.suffix)

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if start < self.old_count:
                raise IndexError("prefix not materialized; capture full history")
            return self.suffix[start - self.old_count : stop - self.old_count : step]
        if index < 0:
            index += len(self)
        if self.old_count and index == self.old_count - 1:
            return self.previous
        if self.old_count <= index < len(self):
            return self.suffix[index - self.old_count]
        raise IndexError("prefix not materialized; capture full history")


def source_frontier(store: Any) -> SourceFrontier | None:
    """Read a cheap committed source marker under KT's append-only contract."""
    meta = store.load_meta()
    identity = meta.get("session_id")
    if not isinstance(identity, str) or not identity:
        return None
    store._flush_events_cache()
    return SourceFrontier(identity, len(store.events))


def _entries(events: list[dict]) -> list[list]:
    paths = index_eligible_parent_paths_by_position(events)
    return _merge_entries([], events, paths)


def _merge_entries(previous: list[list], events: list[dict], paths=None) -> list[list]:
    entries = list(previous)
    seen = {
        (turn, branch, tuple(tuple(pair) for pair in ancestry))
        for turn, branch, ancestry in entries
    }
    for i, event in enumerate(events):
        turn, branch = event.get("turn_index"), event.get("branch_id")
        if classify_branch_scope(turn, branch) is not BranchScope.EXPLICIT:
            continue
        path = (
            paths.get(i)
            if paths is not None
            else (
                strict_parent_branch_path(event.get("parent_branch_path"), turn=turn)
                if event.get("parent_branch_path") is not None
                else ()
            )
        )
        if path is None:
            continue
        entry = [turn, branch, [list(pair) for pair in path]]
        signature = (turn, branch, tuple(path))
        if signature not in seen:
            entries.append(entry)
            seen.add(signature)
    return entries


def selected_from_entries(entries: list[list], requested: dict[int, int] | None):
    events = [{"turn_index": turn, "branch_id": branch} for turn, branch, _ in entries]
    paths = [tuple(tuple(pair) for pair in ancestry) for _, _, ancestry in entries]
    selected = resolve_selected_branches(events, paths, requested)
    return {
        turn: branch for turn, branch in selected.items() if turn > 0 and branch > 0
    }


def catalog_for_full(events: list[dict], frontier: SourceFrontier) -> dict | None:
    """Produce a compact, payload-free branch summary after full capture."""
    entries = _entries(events)
    last = getattr(events[-1], "source_ref", "") if events else ""
    prefix = ""
    next_seq = 0
    if events:
        prefix = last.rsplit(":e", 1)[0] + ":e" if ":e" in last else ""
        suffix = last[len(prefix) :] if prefix else ""
        if not suffix.isascii() or not suffix.isdecimal():
            return None
        next_seq = int(suffix) + 1
    # The first cached frontier can only be trusted when source locators
    # exist; direct event-list tests retain the usual full-rebuild path.
    return {
        "frontier": [frontier.session_id, frontier.physical_count],
        "count": len(events),
        "next_seq": next_seq,
        "anchor": _anchor(events[-1]) if events else "",
        "entries": entries,
        "default_view": selected_from_entries(entries, None),
    }


def read_appended(
    store: Any, agent: str, catalog: dict, frontier: SourceFrontier
) -> tuple[TailHistory, dict] | None:
    """Read only sequential physical suffix keys, or request full fallback."""
    old_frontier = catalog.get("frontier")
    if (
        not isinstance(old_frontier, list)
        or len(old_frontier) != 2
        or old_frontier[0] != frontier.session_id
    ):
        return None
    old_total = old_frontier[1]
    count = catalog.get("count")
    next_seq = catalog.get("next_seq")
    if (
        type(old_total) is not int
        or old_total > frontier.physical_count
        or type(count) is not int
        or count < 0
        or type(next_seq) is not int
        or next_seq < 0
    ):
        return None
    growth = frontier.physical_count - old_total
    if growth == 0:
        # Under the supported append-only source contract, an unchanged
        # physical count means no new source rows. Do not even fetch/hash the
        # last raw payload on the steady read path (it may be very large).
        return TailHistory(count, None, []), catalog
    suffix = []
    for seq in range(next_seq, next_seq + growth):
        ref = f"{agent}:e{seq:06d}"
        try:
            raw = store.events[ref]
        except KeyError:
            break  # Another namespace appended; not this agent.
        if not isinstance(raw, dict):
            return None
        if (
            raw.get("parent_branch_path") is None
            and classify_branch_scope(raw.get("turn_index"), raw.get("branch_id"))
            is BranchScope.EXPLICIT
            and raw["turn_index"] > 1
        ):
            # Legacy ancestry requires a full, once-per-change projection.
            return None
        suffix.append(MemoryEvent(raw, ref))
    previous = None
    if count:
        prior_ref = f"{agent}:e{next_seq - 1:06d}"
        try:
            previous = store.events[prior_ref]
        except KeyError:
            return None
        if not isinstance(previous, dict) or _anchor(previous) != catalog.get("anchor"):
            return None
    view = TailHistory(count, previous, suffix)
    updated = dict(catalog)
    updated["frontier"] = [frontier.session_id, frontier.physical_count]
    if suffix:
        updated["count"] = len(view)
        updated["next_seq"] = next_seq + len(suffix)
        updated["anchor"] = _anchor(suffix[-1])
        updated["entries"] = _merge_entries(catalog["entries"], suffix)
        updated["default_view"] = selected_from_entries(updated["entries"], None)
    return view, updated
