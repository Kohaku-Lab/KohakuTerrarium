"""End-to-end contracts for saved models, build ownership, and branch eligibility."""

import asyncio
import threading
from concurrent.futures import CancelledError as FutureCancelledError
from contextlib import closing

import numpy as np
import pytest

from kohakuterrarium.cli import memory as cli_mod
from kohakuterrarium.session.memory import async_search as async_memory
from kohakuterrarium.session.memory import embedding as emb_mod
from kohakuterrarium.session.memory import embedding_spec as spec_mod
from kohakuterrarium.session.memory.async_search import live_memory_for_store
from kohakuterrarium.session.memory.embedding import (
    BaseEmbedder,
    DEFAULT_M2V_MODEL,
    embedder_identity,
)
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.visibility import resolve_search_view
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.memory import build as build_mod


class FakeLocalModel(BaseEmbedder):
    dimensions = 4

    def __init__(self, model_name=DEFAULT_M2V_MODEL):
        self._model_name = model_name

    def encode(self, texts):
        return np.ones((len(texts), 4), dtype=np.float32)


class FailingModel(BaseEmbedder):
    dimensions = 4

    def encode(self, texts):
        raise RuntimeError("injected embedding failure")


def _store(path, tmp_path):
    store = SessionStore(path)
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    store.append_event(
        "alice",
        "user_input",
        {"content": "needle memory"},
        turn_index=1,
        branch_id=1,
        parent_branch_path=[],
    )
    return store


def test_default_persisted_embedding_spec_restores_same_model(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(emb_mod, "Model2VecEmbedder", FakeLocalModel)
    path = tmp_path / "default.kohakutr"
    store = _store(path, tmp_path)
    store.close(update_status=False)

    result = build_mod.build_index(path, provider="model2vec")
    assert result["stats"]["vec_blocks"] == 1

    store = SessionStore.open_readonly(path)
    try:
        saved = store.state["embedding_config"]
        assert saved["provider"] == "model2vec"
        assert saved["model"] == DEFAULT_M2V_MODEL
        assert all(value is not None for value in saved.values())
    finally:
        store.close(update_status=False)

    # Even if a future release changes the factory's default, the stored
    # effective spec must still reconstruct the original index model.
    monkeypatch.setattr(spec_mod, "DEFAULT_M2V_MODEL", "future-changed-default")
    restored = emb_mod.create_embedder(saved)
    assert isinstance(restored, FakeLocalModel)
    assert restored._model_name == DEFAULT_M2V_MODEL
    with closing(SessionMemory(str(path), restored, index_all_branches=True)) as memory:
        assert memory._manifest()["vector_identity"] == embedder_identity(restored)
        assert len(memory.search("needle", mode="semantic", agent="alice")) == 1
    monkeypatch.setattr(cli_mod, "_resolve_session", lambda _: path)
    assert cli_mod.search_cli("s", "needle", mode="semantic") == 0
    assert "Found 1 result(s)" in capsys.readouterr().out


@pytest.mark.parametrize(
    "injection",
    [
        {
            "type": "user_input",
            "event_id": 100,
            "turn_index": 1,
            "branch_id": True,
            "content": "invalid bool",
        },
        {
            "type": "user_input",
            "event_id": 101,
            "turn_index": 1,
            "branch_id": 0,
            "content": "invalid zero",
        },
        {
            "type": "user_input",
            "event_id": 102,
            "turn_index": None,
            "branch_id": 1,
            "content": "invalid missing",
        },
        {
            "type": "user_input",
            "event_id": 103,
            "turn_index": "1",
            "branch_id": 1,
            "content": "invalid string",
        },
    ],
)
@pytest.mark.parametrize("offset", [0, 1, 2])
def test_invalid_events_cannot_change_valid_branch_decisions(injection, offset):
    events = [
        {
            "type": "user_input",
            "event_id": 1,
            "turn_index": 1,
            "branch_id": 1,
            "content": "valid first",
            "parent_branch_path": [],
        },
        {
            "type": "user_input",
            "event_id": 2,
            "turn_index": 2,
            "branch_id": 1,
            "content": "valid descendant",
            "parent_branch_path": [[1, 1]],
        },
    ]
    expected = resolve_search_view(events)
    assert expected == {1: 1, 2: 1}
    with_invalid = [*events[:offset], injection, *events[offset:]]
    assert resolve_search_view(with_invalid) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["exception", "timeout", "cancel"])
async def test_aborted_live_vector_rebuild_releases_ownership_and_fences_old_work(
    tmp_path, failure_mode, monkeypatch
):
    path = tmp_path / "aborted.kohakutr"
    store = _store(path, tmp_path)
    entered = threading.Event()
    release = threading.Event()

    class BlockedModel(BaseEmbedder):
        dimensions = 4

        def encode(self, texts):
            entered.set()
            if not release.wait(timeout=15):
                raise TimeoutError("injected model timeout")
            return np.ones((len(texts), 4), dtype=np.float32)

    try:
        live = await store.run(live_memory_for_store, store)
        broken = FailingModel() if failure_mode == "exception" else BlockedModel()
        if failure_mode == "timeout":
            monkeypatch.setattr(async_memory, "EMBEDDING_TIMEOUT", 0.05)
        task = asyncio.create_task(
            live.search(
                "needle",
                names=["alice"],
                config={},
                create_embedder=lambda _: broken,
                mode="semantic" if failure_mode == "cancel" else "auto",
            )
        )
        if failure_mode in ("cancel", "timeout"):
            assert await asyncio.to_thread(entered.wait, 10)
        if failure_mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            hits, warning = await task
            assert len(hits) == 1 and "keyword search" in warning

        assert live._memory._vector_build_lock is None
        assert live._memory._manifest()["vector"] != "ready"
        with closing(
            SessionMemory(str(path), FakeLocalModel(), index_all_branches=True)
        ) as peer:
            assert peer.index_events("alice", store.get_memory_events("alice")) == 1
            assert peer._manifest()["vector"] == "ready"
        assert live._memory._manifest()["vector"] == "ready"
    finally:
        release.set()
        pending = getattr(getattr(store, "_live_memory", None), "_worker", None)
        if pending is not None and pending._pending is not None:
            try:
                await asyncio.to_thread(pending._pending.result, 16)
            except (RuntimeError, TimeoutError, FutureCancelledError):
                pass
        store.close(update_status=False)


def test_effective_spec_freezes_presets_and_legacy_unset_fields(monkeypatch):
    monkeypatch.setattr(emb_mod, "Model2VecEmbedder", FakeLocalModel)
    from_preset = emb_mod.create_embedder(
        {"provider": "model2vec", "model": "@base", "dimensions": None}
    )
    spec = emb_mod.effective_embedding_config(
        {"provider": "model2vec", "model": "@base", "dimensions": None},
        from_preset,
    )
    assert spec == {
        "provider": "model2vec",
        "model": emb_mod.MODEL2VEC_PRESETS["base"]["model"],
    }
    assert emb_mod.effective_embedding_config(spec) == spec
    assert embedder_identity(emb_mod.create_embedder(spec)) == embedder_identity(
        from_preset
    )
    assert emb_mod.effective_embedding_config(
        {"provider": "api", "model": None, "dimensions": None}
    ) == {"provider": "api", "model": "text-embedding-3-small"}


def test_aborting_prepared_vectors_rejects_old_commit_and_allows_rebuild(tmp_path):
    path = tmp_path / "stale-commit.kohakutr"
    store = _store(path, tmp_path)
    try:
        rows = store.get_memory_events("alice")
        with closing(
            SessionMemory(str(path), FakeLocalModel(), index_all_branches=True)
        ) as memory:
            memory._index_keywords("alice", rows)
            batch, blocks = memory._vector_batch("alice", rows)
            vectors = memory._embedder.encode([block.content for block in blocks])
            previous_generation = memory._manifest()["vector_generation"]

            memory._abort_vector_build()
            assert memory._vector_build_lock is None
            assert memory._manifest()["vector"] == "building"
            assert memory._manifest()["vector_generation"] > previous_generation
            with pytest.raises(RuntimeError, match="index changed"):
                memory._commit_vectors("alice", batch, len(rows), blocks, vectors)

            with closing(
                SessionMemory(str(path), FakeLocalModel(), index_all_branches=True)
            ) as peer:
                assert peer.index_events("alice", rows) == len(blocks)
                assert peer._manifest()["vector"] == "ready"
                assert len(peer.search("needle", mode="semantic", agent="alice")) == 1
    finally:
        store.close(update_status=False)


def test_saved_index_build_reports_effective_config_persistence_errors(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(emb_mod, "Model2VecEmbedder", FakeLocalModel)
    path = tmp_path / "unrestorable.kohakutr"
    store = _store(path, tmp_path)
    store.close(update_status=False)

    def fail_spec(config, embedder):
        raise OSError("injected spec persistence error")

    monkeypatch.setattr(build_mod, "effective_embedding_config", fail_spec)
    with pytest.raises(OSError, match="spec persistence error"):
        build_mod.build_index(path, provider="model2vec")


def test_cli_unchanged_search_uses_reconciler_roster_without_key_discovery(
    tmp_path, monkeypatch, capsys
):
    """CLI, Reader and Saved Studio share one source-roster authority."""
    from kohakuterrarium.session.memory.reconcile import SessionIndexReconciler

    path = tmp_path / "cli-ready.kohakutr"
    with closing(_store(path, tmp_path)) as writer:
        with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
            reconciler = SessionIndexReconciler(writer, memory)
            reconciler.build(reconciler.capture([], all_agents=True))
    monkeypatch.setattr(cli_mod, "_resolve_session", lambda _: path)

    def cannot_discover(*args, **kwargs):
        raise AssertionError("CLI stable search scanned full Agent keyspace")

    monkeypatch.setattr(SessionStore, "_event_key_names", cannot_discover)
    monkeypatch.setattr(SessionStore, "load_meta", cannot_discover)
    assert cli_mod.search_cli("s", "needle", mode="fts") == 0
    assert "Found 1 result(s)" in capsys.readouterr().out
