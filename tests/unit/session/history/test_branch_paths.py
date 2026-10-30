"""Parent-path identity reuse and legacy ancestry invalidation."""

import random

import pytest

from kohakuterrarium.session.history import branch_paths
from kohakuterrarium.session.history.branch_paths import index_parent_paths


def _baseline(events):
    paths, latest = {}, {}
    for event in events:
        turn, branch, event_id = (
            event.get("turn_index"),
            event.get("branch_id"),
            event.get("event_id"),
        )
        explicit = branch_paths._coerce_path(event.get("parent_branch_path"))
        if isinstance(event_id, int):
            if explicit:
                paths[event_id] = explicit
            elif isinstance(turn, int):
                paths[event_id] = tuple(
                    sorted(
                        ((t, b) for t, b in latest.items() if t < turn),
                        key=lambda pair: pair[0],
                    )
                )
        if (
            isinstance(turn, int)
            and isinstance(branch, int)
            and branch > latest.get(turn, 0)
        ):
            latest[turn] = branch
    return paths


class _UnhashableInt(int):
    __hash__ = None


class TestIndexParentPaths:
    @pytest.mark.parametrize("seed", range(20))
    def test_legacy_explicit_and_duplicate_ids_match_baseline(self, seed):
        rng = random.Random(seed)
        events = [
            {
                "event_id": rng.choice([index, True, 2, None, "bad"]),
                "turn_index": rng.choice([None, 0, 1, 2, 3, 4, 5, "bad"]),
                "branch_id": rng.choice([None, -1, 0, 1, 2, 3, "bad"]),
                "parent_branch_path": rng.choice(
                    [None, [], [[1, 1]], [[1, 2]], [[True, True]], 7]
                ),
            }
            for index in range(1, 50)
        ]
        for projection in (events, events[8:], events[::2]):
            assert index_parent_paths(projection) == _baseline(projection)

    def test_boolean_and_unhashable_integer_paths_keep_original_types(self):
        events = [
            {"event_id": 1, "turn_index": 1, "branch_id": 1},
            {
                "event_id": 2,
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [[1, 1]],
            },
            {
                "event_id": 3,
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [[True, True]],
            },
            {
                "event_id": 4,
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [[1, _UnhashableInt(1)]],
            },
            {"event_id": 5, "turn_index": 3, "branch_id": True},
            {"event_id": 6, "turn_index": 4, "branch_id": 1},
        ]
        paths = index_parent_paths(events)
        assert paths == _baseline(events)
        assert type(paths[2][0][0]) is int
        assert type(paths[3][0][0]) is bool
        assert type(paths[4][0][1]) is _UnhashableInt
        assert type(paths[6][-1][1]) is bool

    def test_equal_explicit_and_legacy_paths_share_immutable_identity(self):
        events = [
            {"event_id": 1, "turn_index": 1, "branch_id": 1},
            {"event_id": 2, "turn_index": 2, "branch_id": 1},
            {"event_id": 3, "turn_index": 2, "branch_id": 1},
            {
                "event_id": 4,
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [[1, 1]],
            },
            {
                "event_id": 5,
                "turn_index": 2,
                "branch_id": 1,
                "parent_branch_path": [[1, 1]],
            },
        ]
        paths = index_parent_paths(events)
        assert paths[2] == ((1, 1),)
        assert all(paths[event_id] is paths[2] for event_id in range(3, 6))
        events[3]["parent_branch_path"][0][1] = 2
        assert paths[4] == ((1, 1),)
        assert index_parent_paths(events)[4] == ((1, 2),)

    def test_legacy_path_cache_invalidates_only_descendant_turns(self, monkeypatch):
        events = [
            {"event_id": 1, "turn_index": 1, "branch_id": 1},
            {"event_id": 2, "turn_index": 2, "branch_id": 1},
            {"event_id": 3, "turn_index": 3, "branch_id": 1},
            {"event_id": 4, "turn_index": 2, "branch_id": 2},
            {"event_id": 5, "turn_index": 3, "branch_id": 2},
            {"event_id": 6, "turn_index": 1, "branch_id": 2},
            {"event_id": 7, "turn_index": 3, "branch_id": 3},
            {"event_id": 8, "turn_index": 2, "branch_id": 3},
            {"event_id": 9, "turn_index": 3, "branch_id": 4},
        ]
        sorts = 0

        def counted_sort(*args, **kwargs):
            nonlocal sorts
            sorts += 1
            return sorted(*args, **kwargs)

        monkeypatch.setattr(branch_paths, "sorted", counted_sort, raising=False)
        paths = index_parent_paths(events)
        assert paths == {
            1: (),
            2: ((1, 1),),
            3: ((1, 1), (2, 1)),
            4: ((1, 1),),
            5: ((1, 1), (2, 2)),
            6: (),
            7: ((1, 2), (2, 2)),
            8: ((1, 2),),
            9: ((1, 2), (2, 3)),
        }
        assert sorts <= 7
