"""M2: branch-aware top-k completeness and lineage projection."""

import numpy as np
import pytest

from kohakuterrarium.session.memory.embedding import BaseEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.visibility import (
    BranchVisibility,
    MemoryCandidateLimitError,
    ranked_filtered,
)


class UniformEmbedder(BaseEmbedder):
    dimensions = 2

    def encode(self, texts):
        return np.ones((len(texts), 2), dtype=np.float32)

    def encode_one(self, text):
        return np.ones(2, dtype=np.float32)


def test_visibility_checks_own_branch_and_ancestry():
    visibility = BranchVisibility({1: 2, 2: 1, 3: 3})
    assert visibility.matches(
        {
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 2]],
        }
    )
    assert not visibility.matches(
        {
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        }
    )
    assert not visibility.matches(
        {
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
        }
    )
    assert not visibility.matches(
        {
            "turn_index": 2,
            "branch_id": 2,
            "parent_branch_path": [[1, 2]],
        }
    )
    assert visibility.matches({"turn_index": None, "branch_id": None})
    assert not visibility.matches(
        {
            "turn_index": 3,
            "branch_id": 3,
            "parent_branch_path": [[1, 2], [2, 2]],
        }
    )


def test_ranked_filtered_widens_past_fixed_overfetch():
    corpus = [{"selected": False}] * 65 + [{"selected": True, "value": 42}]
    limits = []

    def ranked(limit):
        limits.append(limit)
        return corpus[:limit]

    assert ranked_filtered(
        ranked,
        k=1,
        matches=lambda row: row["selected"],
    ) == [corpus[-1]]
    assert limits == [32, 64, 128]


def test_ranked_filtered_exhaustion_is_not_misreported_as_empty():
    excluded = [{"selected": False}] * 200
    with pytest.raises(MemoryCandidateLimitError, match="Cannot prove"):
        ranked_filtered(
            lambda limit: excluded[:limit],
            k=1,
            matches=lambda row: row["selected"],
            max_candidates=64,
        )
    assert (
        ranked_filtered(
            lambda limit: excluded[:limit],
            k=1,
            matches=lambda row: row["selected"],
            max_candidates=256,
        )
        == []
    )


@pytest.mark.parametrize("mode", ["fts", "semantic", "hybrid", "auto"])
def test_all_branch_index_search_filters_prior_to_top_k(tmp_path, mode):
    mem = SessionMemory(
        str(tmp_path / "branch-query.kohakutr"),
        UniformEmbedder(),
        index_all_branches=True,
    )
    rows = [
        {
            "type": "user_input",
            "content": "needle old " + str(n),
            "event_id": n + 1,
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
        }
        for n in range(66)
    ] + [
        {
            "type": "user_input",
            "content": "needle selected " + str(n),
            "event_id": 100 + n,
            "turn_index": 1,
            "branch_id": 2,
            "parent_branch_path": [],
        }
        for n in range(2)
    ]
    try:
        mem.index_events("alice", rows)
        selected = mem.search(
            "needle",
            mode=mode,
            k=2,
            agent="alice",
            branch_view={1: 2},
        )
        assert len(selected) == 2
        assert all(r.branch_id == 2 for r in selected)
        assert {r.content for r in selected} == {
            "needle selected 0",
            "needle selected 1",
        }
        assert {
            r.content
            for r in mem.search(
                "needle",
                mode="fts",
                k=2,
                branch_view={1: 1},
            )
        }.isdisjoint({r.content for r in selected})
    finally:
        mem.close()


def test_wrong_ancestor_is_not_returned_in_selected_descendant(tmp_path):
    mem = SessionMemory(str(tmp_path / "nested.kohakutr"), index_all_branches=True)
    rows = [
        {
            "type": "user_input",
            "content": "needle parent",
            "event_id": 1,
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
        },
        {
            "type": "user_input",
            "content": "needle sibling",
            "event_id": 2,
            "turn_index": 1,
            "branch_id": 2,
            "parent_branch_path": [],
        },
        {
            "type": "user_input",
            "content": "needle incompatible child",
            "event_id": 3,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
        {
            "type": "user_input",
            "content": "needle compatible child",
            "event_id": 4,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 2]],
        },
    ]
    try:
        mem.index_events("alice", rows)
        selected = mem.search("needle", mode="fts", k=5, branch_view={1: 2, 2: 1})
        assert {r.content for r in selected} == {
            "needle sibling",
            "needle compatible child",
        }
    finally:
        mem.close()


def test_selected_lineage_index_rejects_arbitrary_branch_filter(tmp_path):
    mem = SessionMemory(str(tmp_path / "legacy.kohakutr"))
    try:
        with pytest.raises(ValueError, match="all-branch"):
            mem.search("needle", mode="fts", branch_view={1: 1})
    finally:
        mem.close()


def test_multi_agent_selected_views_are_independent(tmp_path):
    mem = SessionMemory(str(tmp_path / "group.kohakutr"), index_all_branches=True)
    try:
        for name in ("alice", "bob"):
            rows = [
                {
                    "type": "user_input",
                    "content": f"needle {name} branch {branch}",
                    "event_id": 10 + branch,
                    "turn_index": 1,
                    "branch_id": branch,
                    "parent_branch_path": [],
                }
                for branch in (1, 2)
            ]
            mem.index_events(name, rows)
        selected = mem.search(
            "needle",
            mode="fts",
            k=10,
            branch_views={"alice": {1: 1}, "bob": {1: 2}},
        )
        assert {(r.agent, r.branch_id) for r in selected} == {
            ("alice", 1),
            ("bob", 2),
        }
    finally:
        mem.close()


def test_index_must_be_migrated_before_scoped_query(tmp_path):
    path = str(tmp_path / "unmigrated.kohakutr")
    original = SessionMemory(path)
    try:
        original.index_events(
            "alice",
            [
                {
                    "type": "user_input",
                    "content": "needle old",
                    "event_id": 1,
                    "turn_index": 1,
                    "branch_id": 1,
                }
            ],
        )
    finally:
        original.close()
    new_reader = SessionMemory(path, index_all_branches=True)
    try:
        with pytest.raises(ValueError, match="rebuild"):
            new_reader.search("needle", mode="fts", branch_views={"alice": {1: 1}})
    finally:
        new_reader.close()


def test_legacy_ancestry_is_resolved_before_branch_filtering(tmp_path):
    mem = SessionMemory(str(tmp_path / "legacy-path.kohakutr"), index_all_branches=True)
    # Legacy rows omit parent_branch_path; replay derives it from earlier turns.
    events = [
        {
            "type": "user_input",
            "content": "needle old root",
            "event_id": 1,
            "turn_index": 1,
            "branch_id": 1,
        },
        {
            "type": "user_input",
            "content": "needle old child",
            "event_id": 2,
            "turn_index": 2,
            "branch_id": 1,
        },
        {
            "type": "user_input",
            "content": "needle new root",
            "event_id": 3,
            "turn_index": 1,
            "branch_id": 2,
        },
        {
            "type": "user_input",
            "content": "needle new child",
            "event_id": 4,
            "turn_index": 2,
            "branch_id": 2,
        },
    ]
    try:
        mem.index_events("alice", events)
        all_hits = {r.content: r for r in mem.search("needle", mode="fts", k=10)}
        assert all_hits["needle old child"].parent_branch_path == ((1, 1),)
        assert all_hits["needle new child"].parent_branch_path == ((1, 2),)
        selected = mem.search("needle", mode="fts", k=5, branch_view={1: 2, 2: 1})
        assert {r.content for r in selected} == {"needle new root"}
    finally:
        mem.close()


def test_unverifiable_legacy_ancestor_rejects_instead_of_leaking():
    from kohakuterrarium.session.memory.visibility import (
        MemoryLineageUnavailableError,
    )

    visibility = BranchVisibility({1: 2, 2: 1})
    with pytest.raises(MemoryLineageUnavailableError, match="Rebuild"):
        visibility.matches(
            {"turn_index": 2, "branch_id": 1, "parent_branch_path": None}
        )


@pytest.mark.parametrize("agent", ["alice", None])
def test_singular_branch_view_rejects_unmigrated_corpus(tmp_path, agent):
    """A v2 *instance* must not search persisted v1 rows using either scope."""
    path = str(tmp_path / "legacy-singular.kohakutr")
    rows = [
        {
            "type": "user_input",
            "content": f"needle sibling {branch}",
            "event_id": branch,
            "turn_index": 1,
            "branch_id": branch,
        }
        for branch in (1, 2)
    ]
    prior = SessionMemory(path)
    try:
        prior.index_events("alice", rows)
    finally:
        prior.close()

    opened = SessionMemory(path, index_all_branches=True)
    try:
        with pytest.raises(ValueError, match="rebuild"):
            opened.search("needle", mode="fts", agent=agent, branch_view={1: 1})
        opened.index_events("alice", rows)
        results = opened.search("needle", mode="fts", agent=agent, branch_view={1: 1})
        assert {r.content for r in results} == {"needle sibling 1"}
    finally:
        opened.close()


def test_resolved_view_handles_zero_turn_bookkeeping_without_weakening_explicit_validation():
    from kohakuterrarium.session.history.branch_paths import InvalidBranchViewError
    from kohakuterrarium.session.memory.visibility import resolve_search_view

    events = [
        {
            "type": "user_input_injected",
            "content": "early note",
            "turn_index": 0,
            "branch_id": 0,
            "event_id": 1,
        },
        {
            "type": "user_input",
            "content": "needle",
            "turn_index": 1,
            "branch_id": 1,
            "event_id": 2,
        },
    ]
    selected = resolve_search_view(events)
    assert selected == {1: 1}
    visibility = BranchVisibility(selected)
    assert visibility.matches(
        {"turn_index": 0, "branch_id": 0, "parent_branch_path": []}
    )
    assert visibility.matches(
        {"turn_index": 1, "branch_id": 1, "parent_branch_path": []}
    )
    with pytest.raises(InvalidBranchViewError):
        BranchVisibility({0: 0})


def test_singular_view_without_agent_validates_all_indexed_namespaces(tmp_path):
    path = str(tmp_path / "mixed-index-formats.kohakutr")
    alice = SessionMemory(path)
    try:
        alice.index_events(
            "alice",
            [
                {
                    "type": "user_input",
                    "content": "needle alice",
                    "event_id": 1,
                    "turn_index": 1,
                    "branch_id": 1,
                }
            ],
        )
    finally:
        alice.close()
    bob = SessionMemory(path, index_all_branches=True)
    try:
        bob.index_events(
            "bob",
            [
                {
                    "type": "user_input",
                    "content": "needle bob",
                    "event_id": 2,
                    "turn_index": 1,
                    "branch_id": 1,
                    "parent_branch_path": [],
                }
            ],
        )
        # A format upgrade is session-wide. Bob's freshly indexed rows must
        # not be published while Alice's old-version cursor is still pending.
        with pytest.raises(ValueError, match="rebuild"):
            bob.search("needle", mode="fts", agent="bob", branch_view={1: 1})
        bob.index_events(
            "alice",
            [
                {
                    "type": "user_input",
                    "content": "needle alice",
                    "event_id": 1,
                    "turn_index": 1,
                    "branch_id": 1,
                    "parent_branch_path": [],
                }
            ],
        )
        assert {
            r.content for r in bob.search("needle", mode="fts", branch_view={1: 1})
        } == {"needle alice", "needle bob"}
    finally:
        bob.close()
