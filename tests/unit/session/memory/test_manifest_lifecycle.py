"""Session-wide index format and fail-closed migration regressions."""

import numpy as np
import pytest

from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.embedding import BaseEmbedder


class DummyEmbedder(BaseEmbedder):
    dimensions = 4

    def __init__(self, axis: int = 0):
        self.axis = axis
        self.encoded: list[str] = []

    def encode(self, texts):
        self.encoded.extend(texts)
        vec = np.zeros((len(texts), 4), dtype=np.float32)
        vec[:, self.axis] = 1.0
        return vec


def _rows(name):
    return [
        {
            "type": "user_input",
            "content": f"needle {name} {branch}",
            "event_id": branch,
            "turn_index": 1,
            "branch_id": branch,
            "parent_branch_path": [],
        }
        for branch in (1, 2)
    ]


def test_global_format_migration_rebuilds_all_agents_before_fts_is_ready(tmp_path):
    path = str(tmp_path / "multi-agent.kohakutr")
    original = SessionMemory(path)
    try:
        original.index_events("alice", _rows("alice"))
        original.index_events("bob", _rows("bob"))
        assert original.get_stats()["fts_blocks"] > 0
    finally:
        original.close()

    upgrade = SessionMemory(path, index_all_branches=True)
    try:
        upgrade._index_keywords("alice", _rows("alice"))
        manifest = upgrade._state["memory:index_manifest"]
        assert manifest["format"] == 2
        assert manifest["fts"] != "ready"
        with pytest.raises(ValueError, match="index.*(building|invalid|rebuild)"):
            upgrade.search("needle", mode="fts", branch_view={1: 2})
        upgrade._index_keywords("bob", _rows("bob"))
        assert upgrade._state["memory:index_manifest"]["fts"] == "ready"
        assert {
            hit.content
            for hit in upgrade.search("needle", mode="fts", branch_view={1: 2})
        } == {"needle alice 2", "needle bob 2"}
    finally:
        upgrade.close()


def test_old_checkpointless_vector_rows_never_survive_vector_promotion(tmp_path):
    path = str(tmp_path / "orphan.kohakutr")
    old = SessionMemory(path, DummyEmbedder())
    try:
        old.index_events("alice", _rows("alice")[:1])
        old._vec.insert(
            np.array([1, 0, 0, 0], dtype=np.float32),
            {"agent": "alice", "content": "needle orphan excluded", "block_id": "old"},
        )
        del old._state["alice:cursor:vectors:4"]
        del old._state["alice:indexed_vectors:4"]
        assert old.get_stats()["vec_blocks"] == 2
    finally:
        old.close()

    keywords = SessionMemory(path, index_all_branches=True)
    try:
        keywords._index_keywords("alice", _rows("alice"))
        assert keywords._state["memory:index_manifest"]["fts"] == "ready"
        assert keywords._state["memory:index_manifest"]["vector"] != "ready"
    finally:
        keywords.close()

    memory = SessionMemory(path, DummyEmbedder(), index_all_branches=True)
    try:
        with pytest.raises(ValueError, match="vector.*(stale|invalid|rebuild)"):
            memory.search("needle", mode="semantic", branch_view={1: 2})
        memory.index_events("alice", _rows("alice"))
        assert {
            r.content
            for r in memory.search("needle", mode="semantic", branch_view={1: 2})
        } == {"needle alice 2"}
        assert memory.get_stats()["vec_blocks"] == 2
    finally:
        memory.close()


def test_vector_model_change_rebuilds_all_agents_without_mixing(tmp_path):
    path = str(tmp_path / "model.kohakutr")
    old = SessionMemory(path, DummyEmbedder(0), index_all_branches=True)
    try:
        old.index_events("alice", _rows("alice"))
        old.index_events("bob", _rows("bob"))
    finally:
        old.close()

    change = SessionMemory(path, DummyEmbedder(1), index_all_branches=True)
    try:
        with pytest.raises(ValueError, match="vector.*(stale|invalid|rebuild)"):
            change.search("needle", mode="semantic", branch_view={1: 2})
        change.index_events("alice", _rows("alice"))
        with pytest.raises(ValueError, match="vector.*(stale|invalid|rebuild)"):
            change.search("needle", mode="semantic", branch_view={1: 2})
        change.index_events("bob", _rows("bob"))
        assert change._state["memory:index_manifest"]["vector"] == "ready"
        assert change.get_stats()["vec_blocks"] == 4
        assert all(
            distance == 0
            for _, distance, _ in change._vec.search(
                np.array([0, 1, 0, 0], dtype=np.float32), k=4
            )
        )
    finally:
        change.close()


def test_interrupted_fts_migration_does_not_publish_half_index(tmp_path, monkeypatch):
    path = str(tmp_path / "interrupted.kohakutr")
    old = SessionMemory(path)
    try:
        old.index_events("alice", _rows("alice"))
    finally:
        old.close()

    upgrading = SessionMemory(path, index_all_branches=True)
    insert = upgrading._fts.insert

    def interrupted(*args, **kwargs):
        insert(*args, **kwargs)
        raise OSError("injected migration interruption")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(upgrading._fts, "insert", interrupted)
            with pytest.raises(OSError, match="injected migration interruption"):
                upgrading._index_keywords("alice", _rows("alice"))
        with pytest.raises(ValueError, match="index.*(building|invalid|rebuild)"):
            upgrading.search("needle", mode="fts", branch_view={1: 2})
    finally:
        upgrading.close()

    recovered = SessionMemory(path, index_all_branches=True)
    try:
        recovered._index_keywords("alice", _rows("alice"))
        assert {
            h.content
            for h in recovered.search("needle", mode="fts", branch_view={1: 2})
        } == {"needle alice 2"}
        assert recovered.get_stats()["fts_blocks"] == 2
    finally:
        recovered.close()


def test_published_manifest_rejects_deleted_agent_checkpoint(tmp_path):
    path = str(tmp_path / "lost-cursor.kohakutr")
    memory = SessionMemory(path, DummyEmbedder(), index_all_branches=True)
    try:
        memory.index_events("alice", _rows("alice"))
        assert memory._state["memory:index_manifest"]["vector"] == "ready"
        del memory._state["alice:cursor:vectors:4"]
        del memory._state["alice:indexed_vectors:4"]
        for kwargs in (
            {"agent": "alice", "branch_view": {1: 2}},
            {"branch_views": {"alice": {1: 2}}},
            {"branch_view": {1: 2}},
        ):
            with pytest.raises(ValueError, match="vector.*(stale|checkpoint)"):
                memory.search("needle", mode="semantic", **kwargs)
    finally:
        memory.close()


def test_force_rebuild_invalidates_both_indexes_before_rebuilding(tmp_path):
    path = str(tmp_path / "force.kohakutr")
    mem = SessionMemory(path, DummyEmbedder(), index_all_branches=True)
    try:
        mem.index_events("alice", _rows("alice"))
        mem.index_events("bob", _rows("bob"))
        old_epoch = mem._manifest()["epoch"]
        mem.force_rebuild(["alice", "bob"])
        manifest = mem._manifest()
        assert manifest["epoch"] > old_epoch
        assert manifest["fts"] == "building"
        assert manifest["vector"] == "invalid"
        assert mem.get_stats()["fts_blocks"] == 0
        assert mem.get_stats()["vec_blocks"] > 0  # Cleanup can be deferred.
        with pytest.raises(ValueError, match="index.*building"):
            mem.search("needle", mode="fts")
        mem.index_events("alice", _rows("alice"))
        with pytest.raises(ValueError, match="index.*building"):
            mem.search("needle", mode="fts")
        mem.index_events("bob", _rows("bob"))
        assert mem._manifest()["fts"] == "ready"
        assert mem._manifest()["vector"] == "ready"
        assert mem.get_stats()["fts_blocks"] == 4
        assert mem.get_stats()["vec_blocks"] == 4
    finally:
        mem.close()


def test_interrupted_vector_clear_restarts_without_stale_rows(tmp_path, monkeypatch):
    path = str(tmp_path / "vector-interrupted.kohakutr")
    old = SessionMemory(path, DummyEmbedder(0), index_all_branches=True)
    try:
        old.index_events("alice", _rows("alice"))
        old.index_events("bob", _rows("bob"))
        assert old.get_stats()["vec_blocks"] == 4
    finally:
        old.close()

    broken = SessionMemory(path, DummyEmbedder(1), index_all_branches=True)
    delete = broken._vec.delete
    calls = 0

    def fail_after_first(row_id):
        nonlocal calls
        calls += 1
        delete(row_id)
        if calls == 1:
            raise OSError("injected vector clear interruption")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(broken._vec, "delete", fail_after_first)
            with pytest.raises(OSError, match="injected vector clear interruption"):
                broken.index_events("alice", _rows("alice"))
        manifest = broken._manifest()
        assert manifest["vector"] != "ready"
        assert manifest["vector_needs_clear"]
        with pytest.raises(ValueError, match="vector.*(stale|invalid)"):
            broken.search("needle", mode="semantic")
    finally:
        broken.close()

    recovered = SessionMemory(path, DummyEmbedder(1), index_all_branches=True)
    try:
        recovered.index_events("alice", _rows("alice"))
        recovered.index_events("bob", _rows("bob"))
        assert recovered.get_stats()["vec_blocks"] == 4
        assert recovered._manifest()["vector"] == "ready"
        assert recovered._manifest()["fts"] == "ready"
    finally:
        recovered.close()


def test_another_instance_cannot_query_during_multi_agent_migration(tmp_path):
    path = str(tmp_path / "read-barrier.kohakutr")
    old = SessionMemory(path)
    try:
        for name in ("alice", "bob"):
            old.index_events(name, _rows(name))
    finally:
        old.close()

    migrating = SessionMemory(path, index_all_branches=True)
    reader = SessionMemory(path, index_all_branches=True)
    try:
        migrating._index_keywords("alice", _rows("alice"))
        with pytest.raises(ValueError, match="index.*building"):
            reader.search("needle", mode="fts", branch_view={1: 2})
        migrating._index_keywords("bob", _rows("bob"))
        assert {
            h.content for h in reader.search("needle", mode="fts", branch_view={1: 2})
        } == {"needle alice 2", "needle bob 2"}
    finally:
        reader.close()
        migrating.close()


def test_writer_file_lock_blocks_competing_memory_mutations(tmp_path):
    from kohakuterrarium.utils.file.lock import FileLock

    path = str(tmp_path / "writer-coordination.kohakutr")
    memory = SessionMemory(path, index_all_branches=True)
    try:
        blocker = FileLock(f"{path}.memory-index.lock")
        blocker.acquire()
        try:
            with pytest.raises(RuntimeError, match="being modified"):
                memory.index_events("alice", _rows("alice"))
            assert memory._manifest() is None
        finally:
            blocker.release()
        assert memory.index_events("alice", _rows("alice")) == 2
        assert memory._manifest()["fts"] == "ready"
    finally:
        memory.close()


def test_delayed_vector_commit_never_publishes_a_lagging_model(tmp_path):
    path = str(tmp_path / "lagging-model.kohakutr")
    rows = _rows("alice")[:1]
    embedder = DummyEmbedder()
    memory = SessionMemory(path, embedder, index_all_branches=True)
    try:
        memory._index_keywords("alice", rows)
        batch, blocks = memory._vector_batch("alice", rows)
        rows = _rows("alice")
        memory._index_keywords("alice", rows)
        with pytest.raises(RuntimeError, match="index changed"):
            memory._commit_vectors(
                "alice",
                batch,
                1,
                blocks,
                embedder.encode([b.content for b in blocks]),
            )
        assert memory._manifest()["vector"] != "ready"
        with pytest.raises(ValueError, match="vector.*(invalid|stale)"):
            memory.search("needle", mode="semantic", branch_view={1: 2})
        memory.index_events("alice", rows)
        assert memory._manifest()["vector"] == "ready"
        assert {
            hit.content
            for hit in memory.search("needle", mode="semantic", branch_view={1: 2})
        } == {"needle alice 2"}
    finally:
        memory.close()


def test_studio_status_distinguishes_physical_rows_from_published_vectors(tmp_path):
    from kohakuterrarium.session.store import SessionStore
    from kohakuterrarium.studio.sessions.memory.build import (
        index_status,
        index_status_quick,
    )

    path = tmp_path / "status.kohakutr"
    store = SessionStore(path)
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    store.append_event(
        "alice",
        "user_input",
        {"content": "needle"},
        turn_index=1,
        branch_id=1,
        parent_branch_path=[],
    )
    rows = store.get_memory_events("alice")
    store.close(update_status=False)

    old = SessionMemory(str(path), DummyEmbedder())
    try:
        old.index_events("alice", rows)
        assert old.get_stats()["vec_blocks"] == 1
    finally:
        old.close()

    migrate = SessionMemory(str(path), index_all_branches=True)
    try:
        migrate._index_keywords("alice", rows)
        assert migrate.get_stats()["vec_blocks"] > 0
    finally:
        migrate.close()

    assert index_status(path)["indexed"] is False
    assert index_status_quick(path) is False


def test_active_format_migration_has_a_single_owner(tmp_path):
    path = str(tmp_path / "two-builders.kohakutr")
    old = SessionMemory(path)
    try:
        old.index_events("alice", _rows("alice"))
        old.index_events("bob", _rows("bob"))
    finally:
        old.close()

    first = SessionMemory(path, index_all_branches=True)
    second = SessionMemory(path, index_all_branches=True)
    try:
        first._index_keywords("alice", _rows("alice"))
        epoch = first._manifest()["epoch"]
        with pytest.raises(RuntimeError, match="already in progress"):
            second._index_keywords("bob", _rows("bob"))
        assert first._manifest()["epoch"] == epoch
        first._index_keywords("bob", _rows("bob"))
        assert first._manifest()["fts"] == "ready"
        assert {
            hit.content
            for hit in second.search("needle", mode="fts", branch_view={1: 2})
        } == {"needle alice 2", "needle bob 2"}
    finally:
        second.close()
        first.close()


def test_idempotent_vector_index_does_not_republish_or_lock(tmp_path):
    path = str(tmp_path / "idempotent.kohakutr")
    memory = SessionMemory(path, DummyEmbedder(), index_all_branches=True)
    try:
        rows = _rows("alice")
        memory.index_events("alice", rows)
        before = dict(memory._manifest())
        assert memory.index_events("alice", rows) == 0
        assert memory._manifest() == before
        assert memory._vector_build_lock is None
        assert memory._fts_build_lock is None
    finally:
        memory.close()


def test_reopened_partial_build_reencodes_previously_completed_agent(tmp_path):
    """A shared table reset invalidates every prior vector checkpoint."""
    path = str(tmp_path / "partial-reopen.kohakutr")
    alice, bob = _rows("alice"), _rows("bob")
    initial = SessionMemory(path, DummyEmbedder(), index_all_branches=True)
    try:
        initial._index_keywords("alice", alice)
        initial._index_keywords("bob", bob)
        batch, blocks = initial._vector_batch("alice", alice)
        initial._commit_vectors(
            "alice",
            batch,
            len(alice),
            blocks,
            initial._embedder.encode([block.content for block in blocks]),
        )
        generation = initial._manifest().get("vector_generation", 0)
        assert initial.get_stats()["vec_blocks"] == 2
        assert initial._manifest()["vector"] == "building"
    finally:
        initial.close()

    reopened = SessionMemory(path, DummyEmbedder(), index_all_branches=True)
    try:
        reopened.index_events("alice", alice)
        reopened.index_events("bob", bob)
        manifest = reopened._manifest()
        assert manifest["vector"] == "ready"
        assert manifest["vector_generation"] > generation
        assert reopened.get_stats()["vec_blocks"] == 4
        assert {
            h.content
            for h in reopened.search(
                "needle", agent="alice", mode="semantic", branch_view={1: 1}
            )
        } == {"needle alice 1"}
        assert {
            h.content
            for h in reopened.search(
                "needle", agent="bob", mode="semantic", branch_view={1: 2}
            )
        } == {"needle bob 2"}
    finally:
        reopened.close()


def test_return_to_previous_vector_dimensions_rebuilds_all_agents(tmp_path):
    class ThreeDimEmbedder(BaseEmbedder):
        dimensions = 3

        def encode(self, texts):
            return np.tile(np.array([1, 0, 0], dtype=np.float32), (len(texts), 1))

    path = str(tmp_path / "dimension-roundtrip.kohakutr")
    agents = ("alice", "bob")
    before = SessionMemory(path, DummyEmbedder(), index_all_branches=True)
    try:
        for agent in agents:
            before.index_events(agent, _rows(agent))
        first_generation = before._manifest()["vector_generation"]
    finally:
        before.close()

    other_dim = SessionMemory(path, ThreeDimEmbedder(), index_all_branches=True)
    try:
        for agent in agents:
            other_dim.index_events(agent, _rows(agent))
        assert other_dim._manifest()["vector"] == "ready"
    finally:
        other_dim.close()

    same_dim = SessionMemory(path, DummyEmbedder(), index_all_branches=True)
    try:
        for agent in agents:
            same_dim.index_events(agent, _rows(agent))
        assert same_dim._manifest()["vector_generation"] > first_generation
        assert same_dim.get_stats()["vec_blocks"] == 4
        for agent in agents:
            assert (
                len(
                    same_dim.search(
                        "needle", agent=agent, mode="semantic", branch_view={1: 2}
                    )
                )
                == 1
            )
    finally:
        same_dim.close()


def test_empty_agents_finish_forced_studio_rebuild(tmp_path, monkeypatch):
    from kohakuterrarium.session.store import SessionStore
    from kohakuterrarium.studio.sessions.memory import build

    path = tmp_path / "empty-agents.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta(
            "s", "terrarium", "", str(tmp_path), ["alice", "empty_a", "empty_b"]
        )
        store.append_event(
            "alice",
            "user_input",
            {"content": "needle alice"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
    finally:
        store.close(update_status=False)

    monkeypatch.setattr(build, "create_embedder", lambda config: DummyEmbedder())
    result = build.build_index(path, provider="auto", force=True)
    assert result["indexed_per_agent"]["empty_a"] == {"events": 0, "blocks": 0}
    assert result["indexed_per_agent"]["empty_b"] == {"events": 0, "blocks": 0}

    memory = SessionMemory(str(path), DummyEmbedder(), index_all_branches=True)
    try:
        manifest = memory._manifest()
        assert manifest["fts"] == "ready"
        assert manifest["vector"] == "ready"
        assert not manifest["fts_pending"]
        assert not manifest["vector_pending"]
        for name in ("empty_a", "empty_b"):
            assert memory._state[f"{name}:cursor:keywords"]["count"] == 0
            assert memory._state[f"{name}:cursor:vectors:4"]["count"] == 0
        assert len(memory.search("needle", mode="semantic", branch_view={1: 1})) == 1
    finally:
        memory.close()


def test_only_empty_agents_finish_studio_rebuild(tmp_path, monkeypatch):
    from kohakuterrarium.session.store import SessionStore
    from kohakuterrarium.studio.sessions.memory import build

    path = tmp_path / "only-empty.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
    finally:
        store.close(update_status=False)

    monkeypatch.setattr(build, "create_embedder", lambda config: DummyEmbedder())
    result = build.build_index(path, force=True)
    assert result["stats"]["vec_blocks"] == 0
    mem = SessionMemory(str(path), DummyEmbedder(), index_all_branches=True)
    try:
        assert mem._manifest()["fts"] == "ready"
        assert mem._manifest()["vector"] == "ready"
        assert mem.search("anything", mode="fts", branch_views={}) == []
    finally:
        mem.close()


def test_legacy_ready_vector_manifest_without_generation_must_rebuild(tmp_path):
    """Pre-generation manifests must not be treated as proof of physical rows."""
    path = str(tmp_path / "legacy-vector-manifest.kohakutr")
    rows = _rows("alice")
    original = SessionMemory(path, DummyEmbedder(), index_all_branches=True)
    try:
        original.index_events("alice", rows)
        manifest = dict(original._manifest())
        manifest.pop("vector_generation")
        original._state["memory:index_manifest"] = manifest
    finally:
        original.close()

    reopened = SessionMemory(path, DummyEmbedder(), index_all_branches=True)
    try:
        with pytest.raises(ValueError, match="vector index stale or invalid"):
            reopened.search("needle", mode="semantic", branch_view={1: 1})
        reopened.index_events("alice", rows)
        assert reopened._manifest()["vector_generation"] >= 1
        assert reopened.get_stats()["vec_blocks"] == 2
        assert {
            hit.content
            for hit in reopened.search("needle", mode="semantic", branch_view={1: 1})
        } == {"needle alice 1"}
    finally:
        reopened.close()
