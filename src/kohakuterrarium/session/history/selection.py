"""Per-selection compatibility checks for indexed parent paths."""

import bisect
from typing import Any

Path = tuple[tuple[int, int], ...]
_NONCANONICAL = object()


class SelectionCompatibility:
    """Check parent paths against one fixed selected branch view."""

    def __init__(self, selected: dict[int, int]):
        self._selected = selected
        self._selected_turns = sorted(selected)
        self._canonical_selection = all(
            (type(turn) is int or type(turn) is bool)
            and (type(branch) is int or type(branch) is bool)
            for turn, branch in selected.items()
        )
        self._paths: dict[int, tuple[Path, int | None]] = {}
        self._pair_conflicts: dict[tuple[int, int], bool] = {}
        self._custom_compatibility: dict[tuple[int, Path], bool] = {}

    def _pair_conflict(self, pair: tuple[int, int]) -> bool:
        """Compare one canonical parent coordinate with the selected view."""
        parent_turn, branch = pair
        return parent_turn in self._selected and self._selected[parent_turn] != branch

    def _path_conflict(self, path: tuple) -> int | None | object:
        """Return the earliest conflicting turn of a canonical immutable path."""
        first = None
        for pair in path:
            if not (
                type(pair) is tuple
                and len(pair) == 2
                and (type(pair[0]) is int or type(pair[0]) is bool)
                and (type(pair[1]) is int or type(pair[1]) is bool)
            ):
                return _NONCANONICAL
            conflict = self._pair_conflicts.get(pair)
            if conflict is None:
                conflict = self._pair_conflict(pair)
                self._pair_conflicts[pair] = conflict
            if conflict and (first is None or pair[0] < first):
                first = pair[0]
        return first

    def _custom_matches(self, path: tuple, turn: int) -> bool | object:
        """Evaluate a custom mapping only at constraints preceding the event turn."""
        if not all(
            type(pair) is tuple
            and len(pair) == 2
            and (type(pair[0]) is int or type(pair[0]) is bool)
            and (type(pair[1]) is int or type(pair[1]) is bool)
            for pair in path
        ):
            return _NONCANONICAL
        key = (turn, path)
        matched = self._custom_compatibility.get(key)
        if matched is None:
            matched = all(
                not (
                    parent_turn < turn
                    and parent_turn in self._selected
                    and self._selected[parent_turn] != branch
                )
                for parent_turn, branch in path
            )
            self._custom_compatibility[key] = matched
        return matched

    def matches(self, path: Any, turn: int) -> bool:
        """Return compatibility with the selected prefix preceding a turn."""
        if (
            self._canonical_selection
            and (type(turn) is int or type(turn) is bool)
            and type(path) is tuple
        ):
            cached = self._paths.get(id(path))
            if cached is not None:
                first = cached[1]
                return first is None or first >= turn
            if type(self._selected) is dict:
                first = self._path_conflict(path)
                if first is not _NONCANONICAL:
                    self._paths[id(path)] = (path, first)
                    return first is None or first >= turn
            else:
                matched = self._custom_matches(path, turn)
                if matched is not _NONCANONICAL:
                    return matched

        bound = min(turn, max(t for t, _ in path) + 1)
        idx = bisect.bisect_left(self._selected_turns, bound)
        prefix = {t: self._selected[t] for t in self._selected_turns[:idx]}
        for parent_turn, branch in path:
            if parent_turn in prefix and prefix[parent_turn] != branch:
                return False
        return True
