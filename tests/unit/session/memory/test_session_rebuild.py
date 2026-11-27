"""Session-wide Memory index maintenance and recovery."""

import hashlib
import sqlite3
import subprocess
import sys
from contextlib import closing
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


def _events(path):
    store = SessionStore.open_readonly(path)
    try:
        return store.get_all_events()
    finally:
        store.close(update_status=False)


def test_cli_rebuilds_all_agents_without_agent_flag(tmp_path, capsys):
    path = tmp_path / "legacy.kohakutr"
    _seed(path)
    original = _events(path)
    args = _build_parser().parse_args(["memory", "rebuild", str(path)])
    assert COMMANDS[args.command](args) == 0
    output = capsys.readouterr().out
    assert "across 2 agents" in output
    backup = next(tmp_path.glob("*.before-memory-rebuild-*.bak"))
    assert _events(backup) == _events(path) == original
    assert inspect_index(backup).fts_rows == 2

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
    original = _events(path)
    result = rebuild_session_memory(path, vectors="drop")
    assert result.events == 2
    assert result.fts_rows == 2 and result.vector_rows == 0
    assert _events(result.backup) == _events(path) == original
    old = inspect_index(result.backup)
    assert old.fts_rows == 3 and old.vector_rows == ((4, 2),)
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


def test_interrupted_keyword_rebuild_keeps_authority_and_recovers(
    tmp_path, monkeypatch
):
    path = tmp_path / "interrupted.kohakutr"
    _seed(path)
    original = _events(path)
    finish = SessionMemory._finish_batch

    def interrupted(self, agent, kind, batch):
        if agent == "bob" and kind == "keywords":
            raise RuntimeError("injected index update failure")
        return finish(self, agent, kind, batch)

    with monkeypatch.context() as m:
        m.setattr(SessionMemory, "_finish_batch", interrupted)
        with pytest.raises(RebuildRefused, match="injected index update failure"):
            rebuild_session_memory(path)

    backup = next(tmp_path.glob("*.before-memory-rebuild-*.bak"))
    assert _events(backup) == _events(path) == original
    assert inspect_index(backup).fts_rows == 2
    memory = SessionMemory(str(path))
    try:
        assert memory.search("SOURCE_ALICE_MEMORY", mode="fts")
        assert not memory.search("SOURCE_BOB_MEMORY", mode="fts")
        assert memory._state.get("bob:cursor:keywords:pending")
    finally:
        memory.close()

    result = rebuild_session_memory(path)
    assert result.fts_rows == 2
    assert _events(path) == original
    memory = SessionMemory(str(path))
    try:
        assert memory.search("SOURCE_BOB_MEMORY", mode="fts")
        assert not memory._state.get("bob:cursor:keywords:pending")
    finally:
        memory.close()


def test_refuses_active_store_and_unreadable_event(tmp_path):
    path = tmp_path / "active.kohakutr"
    _seed(path)
    active = SessionStore(path)
    try:
        with pytest.raises(RebuildRefused, match="currently open"):
            rebuild_session_memory(path)
    finally:
        active.close(update_status=False)

    store = SessionStore(path)
    store.events["alice:e000001"] = "not an event"
    store.close(update_status=False)
    original = _hash(path)
    with pytest.raises(RebuildRefused, match="Cannot read or project"):
        rebuild_session_memory(path)
    assert _hash(path) == original


def test_vector_encoder_failure_preserves_old_rows_and_allows_retry(tmp_path):
    class FailingEmbedder(_Embedder):
        def encode(self, texts):
            raise RuntimeError("encoder unavailable")

    path = tmp_path / "encoder-failure.kohakutr"
    _seed(path, vectors=True)
    original = _events(path)
    with pytest.raises(RebuildRefused, match="encoder unavailable"):
        rebuild_session_memory(path, vectors="rebuild", embedder=FailingEmbedder())
    assert _events(path) == original
    assert inspect_index(path).vector_rows == ((4, 2),)
    backup = next(tmp_path.glob("*.before-memory-rebuild-*.bak"))
    assert _events(backup) == original
    assert inspect_index(backup).vector_rows == ((4, 2),)
    index = SessionMemory(str(path), _Embedder())
    try:
        assert index.search("SOURCE_ALICE_MEMORY", mode="fts")
        assert not index.search("SOURCE_ALICE_MEMORY", mode="semantic")
    finally:
        index.close()

    result = rebuild_session_memory(path, vectors="rebuild", embedder=_Embedder())
    assert result.fts_rows == result.vector_rows == 2
    assert _events(path) == original


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


def test_failed_backup_prevents_index_mutation(tmp_path, monkeypatch):
    import kohakuterrarium.session.memory.rebuild as module

    path = tmp_path / "backup-failure.kohakutr"
    _seed(path)
    original = _hash(path)

    def fail_backup(*args):
        raise RebuildRefused("Backup verification failed")

    monkeypatch.setattr(module, "_backup", fail_backup)
    with pytest.raises(RebuildRefused, match="Backup verification failed"):
        rebuild_session_memory(path)
    assert _hash(path) == original
    assert inspect_index(path).fts_rows == 2


def test_zero_wal_readonly_sidecars_are_recoverable_on_next_rebuild(tmp_path):
    """A failed previous read-only model probe must not wedge future rebuilds."""
    path = tmp_path / "stale-readonly.kohakutr"
    _seed(path, vectors=True)
    original = _events(path)
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
    assert _events(result.backup) == _events(path) == original
    assert inspect_index(result.backup).vector_rows == ((4, 2),)


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
        # An active reader must not share the offline inventory connection.
        assert shm.exists()
    finally:
        reader.close(update_status=False)


def test_committed_wal_data_is_preserved_by_native_rebuild_and_backup(tmp_path):
    path = tmp_path / "committed-wal.kohakutr"
    _seed(path)
    code = (
        "import os, sqlite3, sys; "
        "db=sqlite3.connect(sys.argv[1]); "
        "db.execute('PRAGMA journal_mode=WAL'); "
        "db.execute('PRAGMA wal_autocheckpoint=0'); "
        "db.execute('CREATE TABLE preserved_probe (value TEXT)'); "
        "db.execute(\"INSERT INTO preserved_probe VALUES ('committed')\"); "
        "db.commit(); os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", code, str(path)], check=True)
    assert Path(f"{path}-wal").stat().st_size > 0
    result = rebuild_session_memory(path)
    for file in (path, result.backup):
        with closing(sqlite3.connect(file)) as db:
            assert db.execute("SELECT value FROM preserved_probe").fetchone() == (
                "committed",
            )


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


def test_rebuild_uses_original_store_and_writer_lock(tmp_path, monkeypatch):
    from kohakuterrarium.errors import SessionLockedError
    from kohakuterrarium.session.occupancy.gate import resource_gate
    from kohakuterrarium.session.store.lifecycle.lock import acquire_writer_lock

    path = tmp_path / "admitted-rebuild.kohakutr"
    _seed(path)
    original = SessionMemory._index_keywords
    observed = []

    def witness(self, *args, **kwargs):
        assert Path(self._path) == path
        snapshot = resource_gate.snapshot(path)
        assert snapshot.legacy_active > 0 and snapshot.new_active == 0
        with pytest.raises(SessionLockedError):
            acquire_writer_lock(str(path))
        observed.append(True)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SessionMemory, "_index_keywords", witness)
    assert rebuild_session_memory(path).fts_rows == 2
    assert observed
    assert not resource_gate.snapshot(path).registered


def test_rebuild_must_not_publish_empty_fallback_for_unreadable_source(
    tmp_path, monkeypatch
):
    import kohakuterrarium.session.store.events.values as event_values

    path = tmp_path / "unreadable-events.kohakutr"
    _seed(path)
    original = _hash(path)

    def unreadable_rows(*args, **kwargs):
        raise RuntimeError("synthetic physical event decode failure")

    # Normal Search intentionally falls back to the tolerant reader.
    # Rebuild must not inherit its empty-data result when physical keys exist.
    monkeypatch.setattr(event_values, "read_values", unreadable_rows)
    monkeypatch.setattr(SessionStore, "get_events", lambda self, agent: [])
    with pytest.raises(RebuildRefused, match="source|event|decode|certif"):
        rebuild_session_memory(path)
    assert _hash(path) == original
    assert list(tmp_path.glob("*.before-memory-rebuild-*.bak"))


def test_strict_source_attestation_does_not_change_tolerant_search(
    tmp_path, monkeypatch
):
    import kohakuterrarium.session.store.events.values as event_values
    import kohakuterrarium.session.memory.source_events as source_events

    path = tmp_path / "tolerant-vs-strict.kohakutr"
    _seed(path)
    store = SessionStore(path)
    try:
        expected = store.get_events("alice")

        real_read = event_values.read_values

        def fail_decode(*args, **kwargs):
            if kwargs.get("strict"):
                raise RuntimeError("synthetic corrupt source values")
            return real_read(*args, **kwargs)

        monkeypatch.setattr(event_values, "read_values", fail_decode)
        tolerated = source_events.sourced_events(store, "alice")
        assert tolerated == expected
        assert tolerated[0].source_ref == "alice:e000000"
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
    assert list(tmp_path.glob("*.before-memory-rebuild-*.bak"))


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
    assert list(tmp_path.glob("*.before-memory-rebuild-*.bak"))


def test_strict_physical_read_does_not_require_monotonic_logical_ids(tmp_path):
    from kohakuterrarium.session.memory.source_events import sourced_events

    path = tmp_path / "mirrored-source.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        store.append_event(
            "alice", "user_input", {"event_id": 50, "content": "FIRST_PHYSICAL"}
        )
        store.append_event(
            "alice", "text", {"event_id": 10, "content": "SECOND_PHYSICAL"}
        )
        events = sourced_events(store, "alice", strict=True)
        assert [event["event_id"] for event in events] == [50, 10]
        assert [event.source_ref for event in events] == [
            "alice:e000000",
            "alice:e000001",
        ]
    finally:
        store.close(update_status=False)


@pytest.mark.parametrize("family", ["alias", "referent"])
def test_rebuild_preserves_existing_symlink_format_admission(tmp_path, family):
    from kohakuterrarium.errors import SessionLockedError
    from kohakuterrarium.session.store.lifecycle.lock import (
        acquire_writer_lock,
        release_writer_lock,
    )
    from kohakuterrarium.session.store.lifecycle.version import SessionFormatError

    path = tmp_path / "actual.kohakutr"
    _seed(path)
    alias = tmp_path / "alias.kohakutr"
    try:
        alias.symlink_to(path)
    except OSError:
        pytest.skip("file symlinks unavailable")
    future = Path(f"{alias if family == 'alias' else path}.v3")
    future.write_bytes(b"future sentinel")
    original = _events(path)
    with pytest.raises(SessionFormatError, match="format v3"):
        rebuild_session_memory(alias)
    assert _events(path) == original
    assert alias.is_symlink() and alias.samefile(path)
    future.unlink()
    for target in (alias, path):
        held = acquire_writer_lock(str(target))
        try:
            with pytest.raises(SessionLockedError):
                rebuild_session_memory(alias)
        finally:
            release_writer_lock(held)
    result = rebuild_session_memory(alias)
    assert result.fts_rows == 2
    assert alias.is_symlink() and alias.samefile(path)
    assert _events(path) == original


def test_rebuild_accounts_for_inactive_vector_dimensions(tmp_path):
    class OtherEmbedder(BaseEmbedder):
        dimensions = 3

        def encode(self, texts):
            return np.ones((len(texts), 3), dtype=np.float32)

    path = tmp_path / "inactive-vectors.kohakutr"
    _seed(path, vectors=True)
    store = SessionStore(path)
    memory = SessionMemory(str(path), OtherEmbedder())
    try:
        for agent in ("alice", "bob"):
            memory.index_events(agent, store.get_events(agent))
    finally:
        memory.close()
        store.close(update_status=False)
    assert inspect_index(path).vector_rows == ((3, 2), (4, 2))
    with pytest.raises(RebuildRefused, match="Existing vectors"):
        rebuild_session_memory(path)
    result = rebuild_session_memory(path, vectors="drop")
    assert inspect_index(result.backup).vector_rows == ((3, 2), (4, 2))
    assert inspect_index(path).vector_rows == ((3, 0), (4, 0))
