"""Session-wide Memory index rebuild with guarded vector choices and publication."""

import hashlib
from pathlib import Path

import numpy as np
import pytest

from kohakuterrarium.cli._main import COMMANDS, _build_parser
from kohakuterrarium.cli.memory import rebuild_memory_cli
from kohakuterrarium.session.memory.embedding import BaseEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.rebuild import (
    RebuildRefused,
    inspect_index,
    rebuild_session_memory,
)
from kohakuterrarium.session.store import SessionStore


class _Embedder(BaseEmbedder):
    dimensions = 4
    calls = 0

    def encode(self, texts):
        type(self).calls += 1
        return np.ones((len(texts), 4), dtype=np.float32)

    def encode_one(self, query):
        return np.ones(4, dtype=np.float32)


def _seed(path: Path, *, vectors=False):
    store = SessionStore(path)
    store.init_meta("s", "agent", "", str(path.parent), ["alice", "bob"])
    memory = SessionMemory(str(path), _Embedder() if vectors else None)
    try:
        for agent in ("alice", "bob"):
            store.append_event(
                agent, "user_input", {"content": f"SOURCE_{agent.upper()}_MEMORY"}
            )
            memory.index_events(agent, store.get_events(agent))
    finally:
        memory.close()
        store.close(update_status=False)


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cli_rebuilds_all_agents_without_agent_flag(tmp_path, capsys):
    path = tmp_path / "legacy.kohakutr"
    _seed(path)
    original = _hash(path)
    args = _build_parser().parse_args(["memory", "rebuild", str(path)])
    assert COMMANDS[args.command](args) == 0
    output = capsys.readouterr().out
    assert "across 2 agents" in output
    backup = next(tmp_path.glob("*.before-memory-rebuild-*.bak"))
    assert _hash(backup) == original

    memory = SessionMemory(str(path))
    try:
        for agent in ("alice", "bob"):
            hit = memory.search(f"SOURCE_{agent.upper()}_MEMORY", mode="fts")[0]
            assert hit.source_ref == f"{agent}:e000000"
            assert hit.source_fingerprint
        assert memory._vec is None
    finally:
        memory.close()


def test_existing_vectors_require_explicit_choice_no_writes(tmp_path, capsys):
    path = tmp_path / "has-vectors.kohakutr"
    _seed(path, vectors=True)
    original = _hash(path)
    assert rebuild_memory_cli(str(path)) == 1
    assert "--vectors rebuild" in capsys.readouterr().out
    assert _hash(path) == original
    assert not list(tmp_path.glob("*.before-memory-rebuild-*.bak"))
    assert not list(tmp_path.glob("*.memory-rebuild-*.kohakutr"))
    args = _build_parser().parse_args(["memory", "rebuild", str(path), "--dry-run"])
    assert COMMANDS[args.command](args) == 0
    assert _hash(path) == original


def test_discard_vectors_removes_old_derived_format_but_retains_backup(tmp_path):
    path = tmp_path / "all-branch-old.kohakutr"
    _seed(path, vectors=True)
    original = _hash(path)
    memory = SessionMemory(str(path))
    try:
        cursor = memory._state["alice:cursor:keywords"]
        memory._state["alice:cursor:keywords"] = {**cursor, "all_branches": True}
        memory._state["memory:index_manifest"] = {
            "format": 3,
            "fts": "ready",
            "vector": "invalid",
        }
        memory._fts.insert(
            "OLD_UNSELECTED_TOOL_RESULT",
            {
                "agent": "alice",
                "block_id": "alice:e397:p0",
                "type": "tool",
                "source_ref": "alice:e000000",
                "source_fingerprint": "stale",
            },
        )
    finally:
        memory.close()
    original = _hash(path)
    result = rebuild_session_memory(path, vectors="drop")
    assert result.events == 2
    assert result.fts_rows == 2 and result.vector_rows == 0
    assert _hash(result.backup) == original
    assert inspect_index(path).vector_rows == ((4, 0),)
    store = SessionStore(path)
    memory = SessionMemory(str(path))
    try:
        assert memory._fts.count() == 2
        assert memory._state.get("memory:index_manifest") is None
        assert memory._vec is None
        assert store.state.get("embedding_config") == {"provider": "none"}
    finally:
        memory.close()
        store.close(update_status=False)


def test_vector_rebuild_from_exact_sources_preserves_semantic_search(tmp_path):
    path = tmp_path / "with-embeddings.kohakutr"
    _seed(path, vectors=True)
    initial_calls = _Embedder.calls
    result = rebuild_session_memory(path, vectors="rebuild", embedder=_Embedder())
    assert result.fts_rows == result.vector_rows == 2
    assert _Embedder.calls > initial_calls
    assert result.backup.exists()
    memory = SessionMemory(str(path), _Embedder())
    try:
        hits = memory.search("SOURCE_ALICE_MEMORY", mode="semantic")
        assert hits and hits[0].source_ref
        assert hits[0].source_fingerprint
    finally:
        memory.close()


def test_failed_build_keeps_original_and_cleans_staging(tmp_path, monkeypatch):
    import kohakuterrarium.session.memory.rebuild as module

    path = tmp_path / "interrupted.kohakutr"
    _seed(path, vectors=True)
    original = _hash(path)

    def injected(*args, **kwargs):
        raise RuntimeError("injected stage build error")

    monkeypatch.setattr(module, "_rebuild_staging", injected)
    with pytest.raises(RuntimeError, match="stage build error"):
        rebuild_session_memory(path, vectors="drop")
    assert _hash(path) == original
    assert not list(tmp_path.glob("*.memory-rebuild-*.kohakutr"))
    assert not list(tmp_path.glob("*.before-memory-rebuild-*.bak"))


def test_refuses_active_store_and_unverifiable_event_order(tmp_path):
    path = tmp_path / "active.kohakutr"
    _seed(path)
    active = SessionStore(path)
    try:
        with pytest.raises(RebuildRefused, match="currently open"):
            rebuild_session_memory(path)
    finally:
        active.close(update_status=False)

    store = SessionStore(path)
    store.append_event(
        "alice", "user_input", {"event_id": 1, "content": "DUPLICATE_ID"}
    )
    store.close(update_status=False)
    original = _hash(path)
    with pytest.raises(RebuildRefused, match="Cannot certify"):
        rebuild_session_memory(path)
    assert _hash(path) == original


def test_failed_atomic_replace_preserves_original_and_verified_backup(
    tmp_path, monkeypatch
):
    import kohakuterrarium.session.memory.rebuild as module

    path = tmp_path / "atomic-refusal.kohakutr"
    _seed(path)
    original = _hash(path)

    def fail_replace(*args):
        raise PermissionError("injected replacement denial")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(PermissionError, match="replacement denial"):
        rebuild_session_memory(path)
    assert _hash(path) == original
    backups = list(tmp_path.glob("*.before-memory-rebuild-*.bak"))
    assert len(backups) == 1 and _hash(backups[0]) == original
    assert not list(tmp_path.glob("*.memory-rebuild-*.kohakutr"))


def test_no_vector_auto_rebuild_preserves_saved_embedding_preferences(tmp_path):
    path = tmp_path / "preferences.kohakutr"
    _seed(path)
    store = SessionStore(path)
    try:
        store.state["embedding_config"] = {
            "provider": "model2vec",
            "model": "@base",
        }
    finally:
        store.close(update_status=False)

    output = rebuild_session_memory(path)
    assert output.vector_policy == "fts"
    store = SessionStore(path)
    try:
        assert store.state.get("embedding_config") == {
            "provider": "model2vec",
            "model": "@base",
        }
    finally:
        store.close(update_status=False)


def test_existing_offline_embedding_builder_produces_physical_source_refs(
    tmp_path, monkeypatch
):
    import kohakuterrarium.studio.sessions.memory.build as builder

    path = tmp_path / "existing-builder.kohakutr"
    # The incremental embedding command must stamp *new* indices. A pre-
    # existing legacy FTS is explicitly upgraded via kt memory rebuild,
    # not silently converted by an unrelated incremental operation.
    store = SessionStore(path)
    try:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        store.append_event("alice", "user_input", {"content": "SOURCE_ALICE_MEMORY"})
    finally:
        store.close(update_status=False)
    monkeypatch.setattr(builder, "create_embedder", lambda *_args: _Embedder())
    result = builder.build_index(path, provider="model2vec")
    assert result["indexed_per_agent"]["alice"]["events"] == 1
    memory = SessionMemory(str(path), _Embedder())
    try:
        for mode in ("fts", "semantic"):
            hit = memory.search("SOURCE_ALICE_MEMORY", mode=mode)[0]
            assert hit.source_ref == "alice:e000000"
            assert hit.source_fingerprint
    finally:
        memory.close()


def test_corrupt_backup_prevents_publication(tmp_path, monkeypatch):
    import kohakuterrarium.session.memory.rebuild as module

    path = tmp_path / "backup-failure.kohakutr"
    _seed(path)
    original = _hash(path)
    real_copy = module.shutil.copy2

    def copy_corrupt(src, dst, *args, **kwargs):
        if str(dst).endswith(".bak"):
            Path(dst).write_bytes(b"NOT_A_DATABASE_BACKUP")
            return dst
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "copy2", copy_corrupt)
    with pytest.raises(RebuildRefused, match="Backup verification failed"):
        rebuild_session_memory(path)
    assert _hash(path) == original
    assert not list(tmp_path.glob("*.memory-rebuild-*.kohakutr"))


def test_zero_wal_readonly_sidecars_are_recoverable_on_next_rebuild(tmp_path):
    """A failed previous read-only model probe must not wedge future rebuilds."""
    path = tmp_path / "stale-readonly.kohakutr"
    _seed(path, vectors=True)
    baseline = _hash(path)

    # Read-only SessionStore uses the shared WAL and can leave -shm and an
    # empty -wal even after closing its native handles.
    reader = SessionStore.open_readonly(path)
    try:
        reader.state.get("embedding_config")
    finally:
        reader.close(update_status=False)
    wal, shm = Path(str(path) + "-wal"), Path(str(path) + "-shm")
    assert shm.exists() and wal.exists() and wal.stat().st_size == 0
    assert _hash(path) == baseline

    # Inspection is observational. Rebuild owns the writer lock and can use
    # SQLite's normal WAL checkpoint/close to retire truly idle sidecars.
    assert inspect_index(path).vectors_present
    assert shm.exists() and wal.exists() and wal.stat().st_size == 0
    assert _hash(path) == baseline
    result = rebuild_session_memory(path, vectors="rebuild", embedder=_Embedder())
    assert result.fts_rows == result.vector_rows == 2
    assert _hash(result.backup) == baseline
    assert not shm.exists() and not wal.exists()


def test_failing_model_load_then_successful_retry_never_leaves_source_sidecars(
    tmp_path, monkeypatch, capsys
):
    import kohakuterrarium.session.memory.rebuild as module

    path = tmp_path / "retryable-model.kohakutr"
    _seed(path, vectors=True)
    store = SessionStore(path)
    try:
        store.state["embedding_config"] = {
            "provider": "model2vec",
            "model": None,
            "dimensions": None,
        }
    finally:
        store.close(update_status=False)
    initial = _hash(path)
    observed = []

    def provision(config):
        observed.append(dict(config))
        if len(observed) == 1:
            raise ImportError("test-only model setup unavailable")
        return _Embedder()

    monkeypatch.setattr(module, "create_embedder", provision)
    assert rebuild_memory_cli(str(path), vectors="rebuild") == 1
    assert "model setup unavailable" in capsys.readouterr().out
    assert _hash(path) == initial
    assert not Path(str(path) + "-shm").exists()
    assert not Path(str(path) + "-wal").exists()
    assert not list(tmp_path.glob("*.memory-rebuild-*.kohakutr"))

    assert rebuild_memory_cli(str(path), vectors="rebuild") == 0
    assert len(observed) == 2
    assert next(tmp_path.glob("*.before-memory-rebuild-*.bak")).exists()
    assert not Path(str(path) + "-shm").exists()


def test_active_readonly_store_cannot_be_mistaken_for_idle_sidecars(tmp_path):
    path = tmp_path / "reader-active.kohakutr"
    _seed(path)
    original = _hash(path)
    reader = SessionStore.open_readonly(path)
    try:
        reader.state.get("embedding_config")
        shm = Path(str(path) + "-shm")
        wal = Path(str(path) + "-wal")
        assert shm.exists() and wal.stat().st_size == 0
        with pytest.raises(RebuildRefused, match="currently open"):
            rebuild_session_memory(path)
        assert _hash(path) == original
        # An active reader must never be forcibly detached to satisfy the
        # rebuild's publisher, even though its WAL has no pending frames.
        assert shm.exists()
    finally:
        reader.close(update_status=False)


def test_uncheckpointed_wal_is_not_discarded_during_rebuild(tmp_path):
    path = tmp_path / "uncheckpointed.kohakutr"
    _seed(path)
    original = _hash(path)
    wal = Path(str(path) + "-wal")
    wal.write_bytes(b"SIMULATED_PENDING_WAL_FRAMES")
    try:
        with pytest.raises(RebuildRefused, match="uncheckpointed"):
            rebuild_session_memory(path)
        assert _hash(path) == original
        assert wal.read_bytes() == b"SIMULATED_PENDING_WAL_FRAMES"
    finally:
        wal.unlink(missing_ok=True)


def test_standalone_memory_handles_block_rebuild_before_sqlite_cleanup(tmp_path):
    """Standalone SessionMemory is native occupancy, not a live SessionStore."""
    from kohakuterrarium.session.occupancy.gate import resource_gate

    path = tmp_path / "native-occupancy.kohakutr"
    _seed(path, vectors=True)
    original = _hash(path)
    index = SessionMemory(str(path))
    try:
        assert resource_gate.snapshot(path).legacy_active == 1
        with pytest.raises(RebuildRefused, match="native|occupancy"):
            inspect_index(path)
        with pytest.raises(RebuildRefused, match="native|occupancy"):
            rebuild_session_memory(path, vectors="drop")
        assert _hash(path) == original
        assert index._fts.count() == 2
        assert not list(tmp_path.glob("*.before-memory-rebuild-*.bak"))
    finally:
        index.close()


def test_staged_build_holds_exclusive_native_occupancy(tmp_path, monkeypatch):
    """Block new same-process raw SQLite copies until publication completes."""
    import kohakuterrarium.session.memory.rebuild as module
    from kohakuterrarium.session.occupancy.gate import (
        REASON_FAST_ACTIVE,
        resource_gate,
    )

    path = tmp_path / "admitted-rebuild.kohakutr"
    _seed(path)
    original = module._rebuild_staging

    def observed_stage(*args, **kwargs):
        snapshot = resource_gate.snapshot(path)
        assert snapshot.new_active == 1 and snapshot.legacy_active == 0
        assert resource_gate.try_fast(path, eligible=True).reason == REASON_FAST_ACTIVE
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_rebuild_staging", observed_stage)
    result = rebuild_session_memory(path)
    assert result.fts_rows == 2
    assert not resource_gate.snapshot(path).registered


def test_rebuild_must_not_publish_empty_fallback_for_unreadable_source(
    tmp_path, monkeypatch
):
    import kohakuterrarium.session.memory.source_events as source_events

    path = tmp_path / "unreadable-events.kohakutr"
    _seed(path)
    original = _hash(path)

    def unreadable_rows(*args, **kwargs):
        raise RuntimeError("synthetic physical event decode failure")

    # Normal Search intentionally falls back to the tolerant reader.
    # Rebuild must not inherit its empty-data result when physical keys exist.
    monkeypatch.setattr(source_events, "read_values", unreadable_rows)
    monkeypatch.setattr(SessionStore, "get_events", lambda self, agent: [])
    with pytest.raises(RebuildRefused, match="source|event|decode|certif"):
        rebuild_session_memory(path)
    assert _hash(path) == original
    assert not list(tmp_path.glob("*.before-memory-rebuild-*.bak"))


def test_strict_source_attestation_does_not_change_tolerant_search(
    tmp_path, monkeypatch
):
    import kohakuterrarium.session.memory.source_events as source_events

    path = tmp_path / "tolerant-vs-strict.kohakutr"
    _seed(path)
    store = SessionStore(path)
    try:
        expected = store.get_events("alice")

        def fail_decode(*args, **kwargs):
            raise RuntimeError("synthetic corrupt source values")

        monkeypatch.setattr(source_events, "read_values", fail_decode)
        tolerated = source_events.sourced_events(store, "alice")
        assert tolerated == expected
        assert not hasattr(tolerated[0], "source_ref")
        with pytest.raises(RuntimeError, match="corrupt source values"):
            source_events.sourced_events(store, "alice", strict=True)
    finally:
        store.close(update_status=False)


def test_rebuild_refuses_to_erase_indexed_agent_with_no_physical_events(tmp_path):
    """Index loss guard, not a general Session event-history integrity check."""
    path = tmp_path / "missing-indexed-events.kohakutr"
    _seed(path)
    store = SessionStore(path)
    try:
        # A real index exists for both agents, but one indexed namespace is gone.
        del store.events["bob:e000000"]
    finally:
        store.close(update_status=False)

    before = _hash(path)
    with pytest.raises(RebuildRefused, match="indexed|source|events"):
        rebuild_session_memory(path)
    assert _hash(path) == before
    index = SessionMemory(str(path))
    try:
        assert index._fts.count() == 2
    finally:
        index.close()
    assert not list(tmp_path.glob("*.before-memory-rebuild-*.bak"))


def test_rebuild_allows_genuinely_empty_unindexed_agent(tmp_path):
    path = tmp_path / "empty-unindexed-agent.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice", "bob"])
        store.append_event("alice", "user_input", {"content": "ONLY_ALICE_INDEXED"})
    finally:
        store.close(update_status=False)

    result = rebuild_session_memory(path)
    assert result.fts_rows == 1 and result.events == 1
    assert result.agents == ("alice", "bob")


def test_rebuild_reuses_source_writable_format_gate(tmp_path):
    """Versioned siblings constrain the original archive, not the staged name."""
    from kohakuterrarium.session.store.lifecycle.version import SessionFormatError

    path = tmp_path / "session.kohakutr"
    _seed(path)
    future = Path(f"{path}.v3")
    future.write_bytes(b"FUTURE_FORMAT_SENTINEL")
    before = _hash(path)
    try:
        with pytest.raises(SessionFormatError, match="format v3"):
            rebuild_session_memory(path)
        assert _hash(path) == before
        assert future.read_bytes() == b"FUTURE_FORMAT_SENTINEL"
        assert not list(tmp_path.glob("*.before-memory-rebuild-*.bak"))
        assert not list(tmp_path.glob("*memory-rebuild-*.kohakutr"))
    finally:
        future.unlink(missing_ok=True)

    # Rejection must release the writer lock and allow an ordinary retry
    # after the incompatible family member is no longer present.
    result = rebuild_session_memory(path)
    assert result.fts_rows == 2


def test_rebuild_refuses_vector_only_index_when_agent_events_are_gone(tmp_path):
    """An old semantic row must not be deleted because its FTS sibling is gone."""
    path = tmp_path / "vector-only-missing-events.kohakutr"
    _seed(path, vectors=True)
    store = SessionStore(path)
    memory = SessionMemory(str(path))
    try:
        for row in memory._fts.keys(limit=memory._fts.count()):
            _, meta = memory._fts.get_by_id(row)
            if meta["agent"] == "bob":
                memory._fts.delete(row)
        assert memory._vec is not None and memory._vec.count() == 2
        del store.events["bob:e000000"]
    finally:
        memory.close()
        store.close(update_status=False)

    original = _hash(path)
    with pytest.raises(RebuildRefused, match="Indexed Agent .*bob.*no physical source"):
        rebuild_session_memory(path, vectors="drop")
    assert _hash(path) == original
    assert not list(tmp_path.glob("*.before-memory-rebuild-*.bak"))
