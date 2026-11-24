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
