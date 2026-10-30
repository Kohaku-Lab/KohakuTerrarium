"""Live-event selection parity and compatibility work budgets."""

import bisect
import copy
import random

import pytest

from kohakuterrarium.session.history import replay as history
from kohakuterrarium.session.history import selection as history_selection


def _baseline(events, *, branch_view=None, parent_paths=None):
    events = list(events)
    if parent_paths is None:
        parent_paths = history.index_parent_paths(events)
    selected = history.resolve_selected_branches(events, parent_paths, branch_view)
    turns = sorted(selected)
    live = set()
    for event in events:
        turn, branch, event_id = (
            event.get("turn_index"),
            event.get("branch_id"),
            event.get("event_id"),
        )
        if not isinstance(event_id, int):
            continue
        if not isinstance(turn, int) or not isinstance(branch, int):
            live.add(event_id)
            continue
        if selected.get(turn) != branch:
            continue
        path = parent_paths.get(event_id, ())
        if path:
            bound = min(turn, max(t for t, _ in path) + 1)
            prefix = {t: selected[t] for t in turns[: bisect.bisect_left(turns, bound)]}
            if not history._path_matches(path, prefix):
                continue
        live.add(event_id)
    return live


def _event(event_id, turn=None, branch=None, path=None):
    return {
        "event_id": event_id,
        "turn_index": turn,
        "branch_id": branch,
        "parent_branch_path": path,
    }


def _outcome(call):
    try:
        return ("ok", call())
    except (TypeError, ValueError, KeyError) as error:
        return (type(error), str(error))


@pytest.mark.parametrize("size", [16, 64])
@pytest.mark.parametrize("compatible", [True, False])
def test_repeated_path_full_check_budget(monkeypatch, size, compatible):
    path = ((1, 1 if compatible else 2),)
    events = [_event(1, 1, 1), _event(2, 3, 1)] + [
        _event(event_id, 3, 1, path) for event_id in range(3, size + 3)
    ]
    paths = history.index_parent_paths(events)
    expected = _baseline(events, parent_paths=paths)
    checks = 0
    original = history._path_matches

    def counted(candidate, selected):
        nonlocal checks
        if candidate == path:
            checks += 1
        return original(candidate, selected)

    original_direct = history_selection.SelectionCompatibility._path_conflict

    def counted_direct(self, candidate):
        nonlocal checks
        if candidate == path:
            checks += 1
        return original_direct(self, candidate)

    monkeypatch.setattr(history, "_path_matches", counted)
    monkeypatch.setattr(
        history_selection.SelectionCompatibility, "_path_conflict", counted_direct
    )
    assert history.select_live_event_ids(events, parent_paths=paths) == expected
    assert checks <= 2, (size, checks)


@pytest.mark.parametrize("conflict", [None, 8])
def test_immutable_path_validation_is_once_per_selection(monkeypatch, conflict):
    path = tuple((turn, 2 if turn == conflict else 1) for turn in range(1, 17))
    selected = {turn: 1 for turn in range(1, 17)}
    compatibility = history_selection.SelectionCompatibility(selected)
    type_checks = 0

    def counted_type(value):
        nonlocal type_checks
        type_checks += 1
        return type(value)

    monkeypatch.setattr(history_selection, "type", counted_type, raising=False)
    for turn in range(1, 65):
        assert compatibility.matches(path, turn) == (
            conflict is None or conflict >= turn
        )
    assert type_checks <= 8 * len(path) + 3 * 64, type_checks


def test_different_paths_share_selected_prefix_comparisons(monkeypatch):
    reads = 0
    original = history_selection.SelectionCompatibility._pair_conflict

    def counted(self, pair):
        nonlocal reads
        reads += 1
        return original(self, pair)

    monkeypatch.setattr(
        history_selection.SelectionCompatibility, "_pair_conflict", counted
    )
    compatibility = history_selection.SelectionCompatibility(
        {turn: 1 for turn in range(1, 33)}
    )
    for length in range(1, 33):
        path = tuple((turn, 1) for turn in range(1, length + 1))
        assert compatibility.matches(path, 100)
    assert reads <= 32, reads


def test_noncanonical_mutable_path_remains_uncached():
    path = [[1, 1]]
    compatibility = history_selection.SelectionCompatibility({1: 1})
    assert compatibility.matches(path, 2)
    path[0][1] = 2
    assert not compatibility.matches(path, 2)


def test_custom_selection_does_not_read_future_turn_constraints():
    class FutureReadError(dict):
        def __getitem__(self, key):
            if key == 2:
                raise ValueError("future selection accessed")
            return super().__getitem__(key)

    compatibility = history_selection.SelectionCompatibility(
        FutureReadError({1: 1, 2: 1})
    )
    assert compatibility.matches(((2, 1),), 1)
    assert compatibility.matches(((2, 1),), 2)
    with pytest.raises(ValueError, match="future selection accessed"):
        compatibility.matches(((2, 1),), 3)


class _UnhashableInt(int):
    __hash__ = None


class _RaisingHashInt(int):
    def __hash__(self):
        raise ValueError("branch value must not be hashed")


class _RaisingHashTuple(tuple):
    def __hash__(self):
        raise ValueError("path must not be hashed")


@pytest.mark.parametrize(
    "path",
    [
        ((1, _UnhashableInt(1)),),
        ((1, _RaisingHashInt(1)),),
        _RaisingHashTuple(((1, 1),)),
        None,
        (),
        [[1, 1]],
        ((1, []),),
        ((4, []),),
        ((1, {}),),
        ((1, 2), (4,)),
        ((1, 2), ("bad", 1)),
        ((1, 2), (4, None)),
        ((True, True), (False, False)),
        ((1.0, 1),),
        ((1, 1), (1, 2)),
        7,
        "bad",
    ],
)
def test_supplied_noncanonical_paths_preserve_results_and_errors(path):
    events = [_event(1, 1, 1), _event(2, 3, 1), _event(3, 3, 1)]
    paths = {1: (), 2: (), 3: path}
    before = copy.deepcopy((events, paths))
    expected = _outcome(lambda: _baseline(events, parent_paths=paths))
    actual = _outcome(lambda: history.select_live_event_ids(events, parent_paths=paths))
    assert actual == expected
    assert (events, paths) == before


def test_turn_key_and_call_local_selected_views():
    events = [
        _event(1, 1, 1),
        _event(2, 1, 2),
        _event(3, 1, 1, [[1, 2]]),
        _event(4, 3, 1),
        _event(5, 3, 1, [[1, 2]]),
    ]
    paths = {1: (), 2: (), 3: ((1, 2),), 4: (), 5: ((1, 2),)}
    for view, expected in [({1: 1}, {1, 3, 4}), ({1: 2}, {2, 4, 5})]:
        assert _baseline(events, branch_view=view, parent_paths=paths) == expected
        for _ in range(2):
            assert (
                history.select_live_event_ids(
                    events, branch_view=view, parent_paths=paths
                )
                == expected
            )


def test_projection_derives_legacy_ancestry_independently():
    events = [
        _event(1, 1, 2),
        _event(2, 1, 1),
        _event(3, 2, 1),
        _event(4, 2, 1, "invalid"),
    ]
    full_paths = history.index_parent_paths(events)
    projected = events[1:]
    projected_paths = history.index_parent_paths(projected)
    assert full_paths[3] == ((1, 2),)
    assert projected_paths[3] == ((1, 1),)
    assert history.select_live_event_ids(events, branch_view={1: 1}) == {2}
    assert history.select_live_event_ids(projected, branch_view={1: 1}) == {2, 3, 4}
    assert history.select_live_event_ids(
        projected, branch_view={1: 1}, parent_paths=projected_paths
    ) == {2, 3, 4}


def test_bool_int_aliases_duplicate_ids_and_nonstate_events():
    events = [
        _event(True, True, True),
        _event(2, 2, True, [[True, True]]),
        _event(2, 2, 2, [[1, 1]]),
        _event(3, "2", 99),
        _event(4, 2, None),
        _event("5", 2, 2),
        _event(None, 3, 1),
        _event(False),
    ]
    for view in [None, {True: True}, {2: True}, {2: 2}, {"2": 1}]:
        paths = history.index_parent_paths(events)
        expected = _baseline(events, branch_view=view, parent_paths=paths)
        assert history.select_live_event_ids(iter(events), branch_view=view) == expected
        assert (
            history.select_live_event_ids(events, branch_view=view, parent_paths=paths)
            == expected
        )


@pytest.mark.parametrize("seed", range(40))
def test_differential_legacy_explicit_views_and_projections(seed):
    rng = random.Random(seed)
    raw_paths = [None, [], "bad", 7, [[1, 1]], [[1, 2]], [[True, True]], [[9, 2]]]
    events = [
        _event(
            rng.choice([index, index, True, None, "invalid"]),
            rng.choice([None, False, True, 1, 2, 3, 4, "2"]),
            rng.choice([None, False, True, 1, 2, 3, "1"]),
            copy.deepcopy(rng.choice(raw_paths)),
        )
        for index in range(1, 21)
    ]
    views = [None, {}, {1: 1}, {1: 2}, {2: 1}, {1: True, 3: 2}]
    for projection in [events, events[4:], events[::2]]:
        paths = history.index_parent_paths(projection)
        for view in views:
            before = copy.deepcopy((projection, paths, view))
            expected = _outcome(
                lambda: _baseline(projection, branch_view=view, parent_paths=paths)
            )
            for supplied in [None, paths]:
                actual = _outcome(
                    lambda: history.select_live_event_ids(
                        iter(projection), branch_view=view, parent_paths=supplied
                    )
                )
                assert actual == expected, (seed, projection, view, supplied)
                assert (projection, paths, view) == before


class _ReverseInt(int):
    def __lt__(self, other):
        return int(self) > int(other)


class _BoolSensitiveBranch(int):
    def __ne__(self, other):
        return True if type(other) is bool else int.__ne__(self, other)


class _SelectiveOrder(int):
    def __lt__(self, other):
        if int(self) == 3 and int(other) == 1:
            raise ValueError("selected sort compares 3 with 1")
        return int.__lt__(self, other)


class _PretendBuiltin(type):
    def __eq__(cls, other):
        return other is int or other is bool or cls is other

    __hash__ = type.__hash__


class _SpoofedReverseInt(_ReverseInt, metaclass=_PretendBuiltin):
    pass


class _SpoofedBoolBranch(_BoolSensitiveBranch, metaclass=_PretendBuiltin):
    pass


class _SpoofedHashInt(int, metaclass=_PretendBuiltin):
    def __hash__(self):
        raise ValueError("branch must not be hashed")


@pytest.mark.parametrize(
    "case",
    [
        "reverse",
        "bool_branch",
        "eager_sort",
        "spoof_reverse",
        "spoof_branch",
        "spoof_path",
    ],
)
def test_noncanonical_selected_preserves_prefix_and_eager_sort(case):
    match case:
        case "reverse" | "spoof_reverse":
            number = _ReverseInt if case == "reverse" else _SpoofedReverseInt
            events = [
                _event(1, number(1), 1),
                _event(2, number(2), 1),
                _event(3, 3, 1),
                _event(4, 3, 1),
            ]
            paths = {1: (), 2: (), 3: (), 4: ((1, 2),)}
            expected = ("ok", {1, 2, 3, 4})
        case "bool_branch" | "spoof_branch":
            number = (
                _BoolSensitiveBranch if case == "bool_branch" else _SpoofedBoolBranch
            )
            events = [
                _event(1, 1, number(1)),
                _event(2, 3, 1),
                _event(3, 3, 1),
                _event(4, 3, 1),
            ]
            paths = {1: (), 2: (), 3: ((1, 1),), 4: ((1, True),)}
            expected = ("ok", {1, 2, 3})
        case "eager_sort":
            events = [_event(t, _SelectiveOrder(t), 1) for t in (1, 2, 3)]
            paths = {1: (), 2: ((1, 2),), 3: ()}
            expected = (ValueError, "selected sort compares 3 with 1")
        case "spoof_path":
            events = [_event(1, 1, 1), _event(2, 3, 1), _event(3, 3, 1)]
            paths = {1: (), 2: (), 3: ((1, _SpoofedHashInt(1)),)}
            expected = ("ok", {1, 2, 3})
    before = copy.deepcopy((events, paths))
    assert _outcome(lambda: _baseline(events, parent_paths=paths)) == expected
    assert (
        _outcome(lambda: history.select_live_event_ids(events, parent_paths=paths))
        == expected
    )
    assert (events, paths) == before


@pytest.mark.parametrize("view", [1, "bad", [(1,)], {1: []}])
def test_branch_view_error_parity(view):
    events = [_event(1, 1, 1), _event(2, 2, 1)]
    expected = _outcome(lambda: _baseline(events, branch_view=view))
    assert (
        _outcome(lambda: history.select_live_event_ids(events, branch_view=view))
        == expected
    )
