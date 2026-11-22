"""Shared-prefix branch candidates for the derived Memory v2 source catalogue.

Only candidate nodes and their parent edges are materialized here. A certificate
of source freshness remains in Reconciler, and the original SessionStore event
log remains authoritative. Every prefix node is immutable once interned.
"""

from __future__ import annotations

from typing import Any

from kohakuterrarium.session.history.semantics import strict_parent_branch_path

BRANCH_ENCODING = "prefix-edges-v1"
_ROOT = -1


def empty_candidates() -> dict[str, Any]:
    return {"encoding": BRANCH_ENCODING, "nodes": [], "entries": [], "default_view": {}}


def _intern_path(
    path: tuple[tuple[int, int], ...],
    nodes: list[list[int]],
    index: dict[tuple[int, int, int], int],
) -> int:
    parent = _ROOT
    for turn, branch in path:
        key = (parent, turn, branch)
        found = index.get(key)
        if found is None:
            found = len(nodes)
            nodes.append([parent, turn, branch])
            index[key] = found
        parent = found
    return parent


def project_candidates(
    candidates: dict[str, Any], requested: dict[int, int] | None
) -> dict[int, int]:
    """Match Replay branch-choice semantics without expanding full ancestry.

    Turns are visited in increasing order. Every persisted path is canonical:
    ancestors precede their child turn. Consequently a path node is checked
    only after all of its ancestor turns have been resolved. Memoizing its
    compatibility is safe even as later turns are selected.
    """
    if candidates.get("encoding") != BRANCH_ENCODING:
        raise ValueError("unrecognized branch-candidate encoding")
    nodes = candidates["nodes"]
    by_turn: dict[int, list[tuple[int, int]]] = {}
    for turn, branch, node in candidates["entries"]:
        by_turn.setdefault(turn, []).append((node, branch))

    chosen: dict[int, int] = {}
    override = requested or {}
    compatible = {_ROOT: True}

    def matches(node: int) -> bool:
        missing = []
        while node not in compatible:
            missing.append(node)
            node = nodes[node][0]
        permitted = compatible[node]
        for index in reversed(missing):
            _, turn, branch = nodes[index]
            permitted = permitted and (turn not in chosen or chosen[turn] == branch)
            compatible[index] = permitted
        return permitted

    for turn in sorted(by_turn):
        choices = [branch for node, branch in by_turn[turn] if matches(node)]
        if choices:
            request = override.get(turn)
            chosen[turn] = request if request in choices else max(choices)
    return {turn: branch for turn, branch in chosen.items() if turn > 0 and branch > 0}


def append_candidates(previous: dict[str, Any], entries: list[list]) -> dict[str, Any]:
    """Intern only new suffix ancestry edges; never re-expand earlier paths."""
    if previous.get("encoding") != BRANCH_ENCODING:
        raise ValueError("unrecognized branch-candidate encoding")
    nodes = [list(node) for node in previous["nodes"]]
    candidates = [list(entry) for entry in previous["entries"]]
    node_index = {
        (parent, turn, branch): i for i, (parent, turn, branch) in enumerate(nodes)
    }
    seen = {(turn, branch, parent) for turn, branch, parent in candidates}
    for turn, branch, raw_path in entries:
        path = strict_parent_branch_path(raw_path, turn=turn)
        if path is None:
            continue
        parent = _intern_path(path, nodes, node_index)
        key = (turn, branch, parent)
        if key not in seen:
            candidates.append([turn, branch, parent])
            seen.add(key)
    new = {"encoding": BRANCH_ENCODING, "nodes": nodes, "entries": candidates}
    new["default_view"] = project_candidates(new, None)
    return new


def candidates_from_entries(entries: list[list]) -> dict[str, Any]:
    return append_candidates(empty_candidates(), entries)


def branch_candidates_valid(candidate: Any, *, count: int, anchor: str) -> bool:
    """Check a materialization's source binding and basic derived schema."""
    return (
        isinstance(candidate, dict)
        and candidate.get("encoding") == BRANCH_ENCODING
        and candidate.get("count") == count
        and candidate.get("anchor") == anchor
        and isinstance(candidate.get("nodes"), list)
        and isinstance(candidate.get("entries"), list)
        and isinstance(candidate.get("default_view"), dict)
    )
