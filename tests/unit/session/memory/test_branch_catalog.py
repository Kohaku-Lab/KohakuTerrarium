"""Derived branch-prefix materialization and source-proof separation."""

from contextlib import closing
import json
import random

from kohakuterrarium.session.history.replay import resolve_selected_branches
from kohakuterrarium.session.memory.branch_catalog import (
    append_candidates,
    candidates_from_entries,
    project_candidates,
)
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.reconcile import SessionIndexReconciler
from kohakuterrarium.session.store import SessionStore


def _old_selection(entries, requested):
    events = [{"turn_index": t, "branch_id": b} for t, b, _ in entries]
    paths = [tuple(tuple(pair) for pair in ancestry) for _, _, ancestry in entries]
    return resolve_selected_branches(events, paths, requested)


def test_shared_prefix_projection_matches_replay_across_mirrors_and_overrides():
    """A trie is only a representation change, never a new selection policy."""
    for seed in range(32):
        rng = random.Random(seed)
        entries = []
        for turn in range(1, 17):
            for branch in range(1, rng.randrange(2, 5)):
                # Canonical ancestry includes only earlier turns, but siblings
                # may reuse the same (turn, branch) under different roots.
                for _ in range(rng.randrange(1, 4)):
                    parent = [
                        [older, rng.randint(1, 3)]
                        for older in range(1, turn)
                        if rng.random() < 0.6
                    ]
                    entries.append([turn, branch, parent])
        compact = candidates_from_entries(entries)
        incremental = candidates_from_entries(entries[:3])
        for i in range(3, len(entries), 5):
            incremental = append_candidates(incremental, entries[i : i + 5])
        assert incremental == compact
        for override in (
            None,
            {},
            {turn: 1 for turn in range(1, 17)},
            {turn: 3 for turn in range(1, 17)},
            {rng.randint(1, 16): rng.randint(1, 3)},
        ):
            expected = _old_selection(entries, override)
            assert project_candidates(compact, override) == expected


def test_linear_ancestry_is_interned_once():
    entries = [
        [turn, 1, [[older, 1] for older in range(1, turn)]] for turn in range(1, 321)
    ]
    compact = candidates_from_entries(entries)
    assert len(compact["nodes"]) == 319
    assert len(compact["entries"]) == 320
    assert project_candidates(compact, {turn: 1 for turn in range(1, 321)}) == {
        turn: 1 for turn in range(1, 321)
    }
    assert len(json.dumps(compact)) < 20_000


def test_steady_proof_never_loads_full_keyword_cursor_or_branch_candidates(
    tmp_path, monkeypatch
):
    path = tmp_path / "small-proof.kohakutr"
    with closing(SessionStore(path)) as store:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        for turn in range(1, 41):
            store.append_event(
                "alice",
                "user_input",
                {"content": f"needle {turn}"},
                turn_index=turn,
                branch_id=1,
                parent_branch_path=[(t, 1) for t in range(1, turn)],
            )
        with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
            reconciler = SessionIndexReconciler(store, memory)
            reconciler.build(reconciler.capture([], all_agents=True))
            proof = memory._state.get("alice:memory_source_catalog")
            assert "entries" not in proof and "default_view" not in proof
            assert len(json.dumps(proof)) < 1000
            assert "keyword_revision" in proof
            full = memory._state.get("alice:memory_branch_catalog")
            assert full["count"] == 40
            assert full["anchor"] == proof["anchor"]
            original = memory._state.get
            opened = []

            def counted(key, *args, **kwargs):
                if key in ("alice:cursor:keywords", "alice:memory_branch_catalog"):
                    opened.append(key)
                return original(key, *args, **kwargs)

            monkeypatch.setattr(memory._state, "get", counted)
            for _ in range(3):
                stable = reconciler.probe_source_prefix(["alice"])
                assert stable is not None and stable.published
            assert opened == []
            views = reconciler.selected_views(
                stable, ["alice"], {"alice": {t: 1 for t in range(1, 41)}}
            )
            assert len(views["alice"]) == 40
            assert opened == ["alice:memory_branch_catalog"]
            # Any accepted keyword rewrite changes the small revision. Even
            # with unchanged row count the old certificate then fails closed.
            revision_key = "alice:cursor:keywords:revision"
            revision = memory._state[revision_key]
            memory._state[revision_key] = revision + 1
            assert reconciler.probe_source_prefix(["alice"]) is None


def test_legacy_derived_catalogue_recovers_without_format_change(tmp_path):
    path = tmp_path / "legacy-derived.kohakutr"
    with closing(SessionStore(path)) as store:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        store.append_event(
            "alice",
            "user_input",
            {"content": "legacy needle"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
            reconciler = SessionIndexReconciler(store, memory)
            reconciler.build(reconciler.capture([], all_agents=True))
            manifest = dict(memory._manifest())
            proof = dict(memory._state["alice:memory_source_catalog"])
            proof.pop("keyword_revision")
            proof.pop("generation")
            proof["entries"] = [[1, 1, []]]
            proof["default_view"] = {1: 1}
            memory._state["alice:memory_source_catalog"] = proof
            memory._state.delete("alice:memory_branch_catalog")
            old = reconciler.capture_or_reuse(["alice"], all_agents=True)
            assert not old.published
            assert reconciler.build(old) == {"alice": 0}
            assert memory._manifest() == manifest
            assert reconciler.probe_source_prefix(["alice"]).published
            assert memory.search("needle", mode="fts", branch_views={"alice": {1: 1}})


def test_peer_append_does_not_rewrite_unmodified_branch_candidates(tmp_path):
    path = tmp_path / "two-agent-branches.kohakutr"
    with closing(SessionStore(path)) as store:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        for name in ("alice", "bob"):
            store.append_event(
                name,
                "user_input",
                {"content": f"{name} needle"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
        with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
            rec = SessionIndexReconciler(store, memory)
            rec.build(rec.capture([], all_agents=True))
            alice = memory._state.get("alice:memory_branch_catalog")
            store.append_event(
                "bob",
                "user_input",
                {"content": "bob appended needle"},
                turn_index=2,
                branch_id=1,
                parent_branch_path=[(1, 1)],
            )
            source, _ = rec.prepare_keywords(["alice"])
            assert not source.published
            assert "_branches" not in source.catalogs["alice"]
            assert memory._state.get("alice:memory_branch_catalog") == alice
            assert memory._state.get("bob:memory_branch_catalog")["count"] == 2
