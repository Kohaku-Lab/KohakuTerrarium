"""Offline legacy FTS/Vector source-metadata migration and safety fences."""

import numpy as np
import pytest
from kohakuvault import TextVault

from kohakuterrarium.session.memory.embedding import BaseEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.migration import (
    UnprovableIndexSource,
    backfill_legacy_sources,
)
from kohakuterrarium.session.store import SessionStore


class _CountingEmbedder(BaseEmbedder):
    dimensions = 4

    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        return np.ones((len(texts), 4), dtype=np.float32)

    def encode_one(self, _query):
        return np.ones(4, dtype=np.float32)


def _vector_rows(memory):
    reader = memory._resources.open(TextVault, memory._path, table="memory_vec_4d")
    try:
        return reader.keys(limit=memory._vec.count())
    finally:
        memory._resources.drop(reader)


def test_existing_index_metadata_backfill_is_idempotent_no_embedding_cost(tmp_path):
    store = SessionStore(tmp_path / "old-vectors.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    embedder = _CountingEmbedder()
    memory = SessionMemory(str(store.path), embedder)
    try:
        store.append_event("alice", "user_input", {"content": "SOURCE_LINK_ONE"})
        memory.index_events("alice", store.get_events("alice"))
        count_before = embedder.calls
        v_id = _vector_rows(memory)[0]
        old_vector, old_meta = memory._vec.get_by_id(v_id)
        assert "source_ref" not in old_meta

        outcome = backfill_legacy_sources(store, memory, "alice")
        assert (outcome.fts_patched, outcome.vector_patched) == (1, 1)
        assert embedder.calls == count_before
        v2, updated = memory._vec.get_by_id(v_id)
        assert np.array_equal(old_vector, v2)
        assert updated["source_ref"] == "alice:e000000"
        assert updated["source_fingerprint"]
        assert memory.search("SOURCE_LINK_ONE", mode="fts")
        assert memory.search("SOURCE_LINK_ONE", mode="semantic")

        again = backfill_legacy_sources(store, memory, "alice")
        assert again.fts_patched == again.vector_patched == 0
        assert (again.fts_present, again.vector_present) == (1, 1)
        assert embedder.calls == count_before
    finally:
        memory.close()
        store.close(update_status=False)

    reopened = SessionMemory(str(store.path), _CountingEmbedder())
    try:
        assert reopened._vec.get_by_id(v_id)[1]["source_ref"] == "alice:e000000"
        assert reopened._fts.get_by_id(1)[1]["source_ref"] == "alice:e000000"
    finally:
        reopened.close()


def test_failed_append_sequence_gap_never_assumes_ordinal_is_physical_key(tmp_path):
    store = SessionStore(tmp_path / "gap.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    memory = SessionMemory(str(store.path))
    try:
        store.append_event("alice", "user_input", {"content": "GAP_FIRST"})
        store._event_seq["alice"] = 2  # Simulate failed seq allocation e000001.
        store.append_event("alice", "user_input", {"content": "GAP_SECOND"})
        memory.index_events("alice", store.get_events("alice"))
        changed = backfill_legacy_sources(store, memory, "alice")
        assert changed.fts_patched == 2
        metas = [
            memory._fts.get_by_id(row)[1]
            for row in memory._fts.keys(limit=memory._fts.count())
        ]
        assert [m["source_ref"] for m in metas] == [
            "alice:e000000",
            "alice:e000002",
        ]
    finally:
        memory.close()
        store.close(update_status=False)


def test_unprovable_source_rejects_backfill_without_mutating_index(tmp_path):
    store = SessionStore(tmp_path / "mirror.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    memory = SessionMemory(str(store.path), _CountingEmbedder())
    try:
        store.append_event(
            "alice", "user_input", {"event_id": 50, "content": "BEFORE_MIRROR"}
        )
        store.append_event(
            "alice", "user_input", {"event_id": 10, "content": "AFTER_MIRROR"}
        )
        memory.index_events("alice", store.get_events("alice"))
        with pytest.raises(ValueError, match="Non-monotonic"):
            backfill_legacy_sources(store, memory, "alice")
        assert all(
            "source_ref" not in memory._fts.get_by_id(row)[1]
            for row in memory._fts.keys(limit=memory._fts.count())
        )
        assert all(
            "source_ref" not in memory._vec.get_by_id(row)[1]
            for row in _vector_rows(memory)
        )
    finally:
        memory.close()
        store.close(update_status=False)


def test_preexisting_incompatible_provenance_fails_before_patching_other_rows(
    tmp_path,
):
    store = SessionStore(tmp_path / "conflict.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    memory = SessionMemory(str(store.path), _CountingEmbedder())
    try:
        for marker in ("ONE_SOURCE_RECORD", "TWO_SOURCE_RECORD"):
            store.append_event("alice", "user_input", {"content": marker})
        memory.index_events("alice", store.get_events("alice"))
        first = memory._fts.keys(limit=memory._fts.count())[0]
        content, meta = memory._fts.get_by_id(first)
        memory._fts.update(first, value={**meta, "source_ref": "alice:e999999"})
        with pytest.raises(UnprovableIndexSource, match="Existing provenance"):
            backfill_legacy_sources(store, memory, "alice")
        for row in memory._fts.keys(limit=memory._fts.count()):
            _, saved = memory._fts.get_by_id(row)
            if row != first:
                assert "source_ref" not in saved
        assert all(
            "source_ref" not in memory._vec.get_by_id(row)[1]
            for row in _vector_rows(memory)
        )
    finally:
        memory.close()
        store.close(update_status=False)


def test_metadata_patch_interruption_is_retryable_without_vector_reencode(
    tmp_path, monkeypatch
):
    store = SessionStore(tmp_path / "interrupt.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    embedder = _CountingEmbedder()
    memory = SessionMemory(str(store.path), embedder)
    try:
        store.append_event("alice", "user_input", {"content": "RECOVERY_LINK_TEST"})
        memory.index_events("alice", store.get_events("alice"))
        calls = embedder.calls
        vector_type = type(memory._vec)
        original = vector_type.update

        def fail_once(self, *args, **kwargs):
            raise RuntimeError("Injected vector metadata write failure")

        monkeypatch.setattr(vector_type, "update", fail_once)
        with pytest.raises(RuntimeError, match="Injected"):
            backfill_legacy_sources(store, memory, "alice")
        assert memory._fts.get_by_id(1)[1]["source_ref"] == "alice:e000000"
        assert "source_ref" not in memory._vec.get_by_id(1)[1]
        monkeypatch.setattr(vector_type, "update", original)
        outcome = backfill_legacy_sources(store, memory, "alice")
        assert outcome.fts_present == outcome.vector_patched == 1
        assert embedder.calls == calls
    finally:
        memory.close()
        store.close(update_status=False)


def test_stale_keyword_cursor_rejects_offline_patch_without_side_effect(tmp_path):
    store = SessionStore(tmp_path / "stale-keyword.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    memory = SessionMemory(str(store.path))
    try:
        store.append_event("alice", "user_input", {"content": "BEFORE_APPEND"})
        memory.index_events("alice", store.get_events("alice"))
        store.append_event("alice", "user_input", {"content": "AFTER_APPEND"})
        with pytest.raises(UnprovableIndexSource, match="Keyword cursor"):
            backfill_legacy_sources(store, memory, "alice")
        assert "source_ref" not in memory._fts.get_by_id(1)[1]
    finally:
        memory.close()
        store.close(update_status=False)


def test_stale_vector_cursor_rejects_preflight_without_partial_patch(tmp_path):
    store = SessionStore(tmp_path / "stale-vector.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    memory = SessionMemory(str(store.path), _CountingEmbedder())
    try:
        store.append_event("alice", "user_input", {"content": "BEFORE_REFRESH"})
        memory.index_events("alice", store.get_events("alice"))
        store.append_event("alice", "user_input", {"content": "KEYWORD_ONLY_REFRESH"})
        # Simulate an unfinished vector refresh while FTS is already current.
        memory._index_keywords("alice", store.get_events("alice"))
        with pytest.raises(UnprovableIndexSource, match="Vector cursor"):
            backfill_legacy_sources(store, memory, "alice")
        assert all(
            "source_ref" not in memory._fts.get_by_id(row)[1]
            for row in memory._fts.keys(limit=memory._fts.count())
        )
        assert "source_ref" not in memory._vec.get_by_id(1)[1]
    finally:
        memory.close()
        store.close(update_status=False)


def test_append_during_validation_rejected_before_any_row_update(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "concurrent-validation.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    memory = SessionMemory(str(store.path), _CountingEmbedder())
    try:
        store.append_event("alice", "user_input", {"content": "ORIGINAL_VALIDATION"})
        memory.index_events("alice", store.get_events("alice"))
        vector_type = type(memory._vec)
        original = vector_type.get_by_id
        injected = False

        def append_during_vector_preflight(self, *args, **kwargs):
            nonlocal injected
            result = original(self, *args, **kwargs)
            if not injected:
                injected = True
                store.append_event("alice", "user_input", {"content": "RACING_APPEND"})
            return result

        monkeypatch.setattr(vector_type, "get_by_id", append_during_vector_preflight)
        with pytest.raises(UnprovableIndexSource, match="changed during"):
            backfill_legacy_sources(store, memory, "alice")
        assert injected
        assert "source_ref" not in memory._fts.get_by_id(1)[1]
        assert "source_ref" not in memory._vec.get_by_id(1)[1]
    finally:
        memory.close()
        store.close(update_status=False)


def test_mid_patch_append_leaves_only_verified_rows_and_requires_retry(
    tmp_path, monkeypatch
):
    store = SessionStore(tmp_path / "racing-write.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    embedder = _CountingEmbedder()
    memory = SessionMemory(str(store.path), embedder)
    try:
        for marker in ("PRE_RACE_FIRST", "PRE_RACE_SECOND"):
            store.append_event("alice", "user_input", {"content": marker})
        memory.index_events("alice", store.get_events("alice"))
        initial_calls = embedder.calls
        fts_type = type(memory._fts)
        original_update = fts_type.update
        inserted = False

        def append_after_first_write(self, *args, **kwargs):
            nonlocal inserted
            result = original_update(self, *args, **kwargs)
            if not inserted:
                inserted = True
                store.append_event("alice", "user_input", {"content": "RACING_NEW_ROW"})
            return result

        monkeypatch.setattr(fts_type, "update", append_after_first_write)
        with pytest.raises(UnprovableIndexSource, match="changed during"):
            backfill_legacy_sources(store, memory, "alice")
        assert inserted
        metadata = [
            memory._fts.get_by_id(row)[1]
            for row in memory._fts.keys(limit=memory._fts.count())
        ]
        assert sum("source_ref" in meta for meta in metadata) == 1
        assert all(
            "source_ref" not in memory._vec.get_by_id(row)[1]
            for row in _vector_rows(memory)
        )
        assert embedder.calls == initial_calls
        assert memory.search("PRE_RACE_FIRST", mode="fts")
        # With source appended, the old complete cursor is no longer enough.
        with pytest.raises(UnprovableIndexSource, match="Keyword cursor"):
            backfill_legacy_sources(store, memory, "alice")
        monkeypatch.setattr(fts_type, "update", original_update)
        memory.index_events("alice", store.get_events("alice"))
        outcome = backfill_legacy_sources(store, memory, "alice")
        assert outcome.fts_present == 1
        assert outcome.vector_patched == 3
    finally:
        memory.close()
        store.close(update_status=False)


def test_backfill_scoped_to_one_agent_not_peer_source_or_vectors(tmp_path):
    store = SessionStore(tmp_path / "two-agents.kohakutr")
    store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
    memory = SessionMemory(str(store.path), _CountingEmbedder())
    try:
        for agent in ("alice", "bob"):
            store.append_event(
                agent, "user_input", {"content": f"INDEXED_{agent.upper()}"}
            )
            memory.index_events(agent, store.get_events(agent))
        outcome = backfill_legacy_sources(store, memory, "alice")
        assert (outcome.fts_patched, outcome.vector_patched) == (1, 1)
        fts = [
            memory._fts.get_by_id(row)[1]
            for row in memory._fts.keys(limit=memory._fts.count())
        ]
        vectors = [memory._vec.get_by_id(row)[1] for row in _vector_rows(memory)]
        assert all(
            (
                (meta["source_ref"] == "alice:e000000")
                if meta["agent"] == "alice"
                else ("source_ref" not in meta)
            )
            for meta in [*fts, *vectors]
        )
    finally:
        memory.close()
        store.close(update_status=False)


def test_append_while_capturing_source_rejected_before_preflight(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "capture-race.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    memory = SessionMemory(str(store.path))
    try:
        store.append_event("alice", "user_input", {"content": "BEFORE_CAPTURE"})
        memory.index_events("alice", store.get_events("alice"))
        old_get = store.get_events
        called = False

        def raced_get(agent):
            nonlocal called
            rows = old_get(agent)
            if not called:
                called = True
                store.append_event("alice", "user_input", {"content": "AFTER_CAPTURE"})
            return rows

        monkeypatch.setattr(store, "get_events", raced_get)
        with pytest.raises(UnprovableIndexSource, match="while capturing"):
            backfill_legacy_sources(store, memory, "alice")
        assert called
        assert "source_ref" not in memory._fts.get_by_id(1)[1]
    finally:
        memory.close()
        store.close(update_status=False)


def test_backfill_refuses_store_and_index_from_different_sessions(tmp_path):
    stores = []
    indexes = []
    try:
        for number in (1, 2):
            store = SessionStore(tmp_path / f"source-{number}.kohakutr")
            stores.append(store)
            store.init_meta(f"s{number}", "agent", "", str(tmp_path), ["alice"])
            store.append_event(
                "alice", "user_input", {"content": "IDENTICAL_EVENT_CONTENT"}
            )
            index = SessionMemory(str(store.path))
            indexes.append(index)
            index.index_events("alice", store.get_events("alice"))
        with pytest.raises(UnprovableIndexSource, match="different files"):
            backfill_legacy_sources(stores[0], indexes[1], "alice")
        assert "source_ref" not in indexes[1]._fts.get_by_id(1)[1]
    finally:
        for index in indexes:
            index.close()
        for store in stores:
            store.close(update_status=False)


def test_offline_backfill_of_saved_vectors_does_not_load_original_embedder(
    tmp_path,
):
    store = SessionStore(tmp_path / "no-encoder-needed.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
    embedder = _CountingEmbedder()
    writer = SessionMemory(str(store.path), embedder)
    try:
        store.append_event("alice", "user_input", {"content": "PERSISTED_VECTOR_FACT"})
        writer.index_events("alice", store.get_events("alice"))
        encoded_calls = embedder.calls
        assert writer._state.get("vec_dimensions") == 4
    finally:
        writer.close()
    # Old vectors can be patched by their stored dimensions alone. Constructing
    # an encoder is not necessary for the explicit migration operation.
    index = SessionMemory(str(store.path))
    try:
        assert index.has_vectors
        assert index._embedder.dimensions == 0
        old_keys = set(index._state.keys())
        result = backfill_legacy_sources(store, index, "alice")
        assert (result.fts_patched, result.vector_patched) == (1, 1)
        assert embedder.calls == encoded_calls
        assert set(index._state.keys()) == old_keys
        assert index._vec.get_by_id(1)[1]["source_ref"] == "alice:e000000"
    finally:
        index.close()
        store.close(update_status=False)


def test_agent_fts_only_migrates_when_peer_owns_all_vector_rows(tmp_path):
    """A shared vector table does not imply every Agent has vector rows."""
    store = SessionStore(tmp_path / "peer-only-vectors.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice", "bob"])
    memory = SessionMemory(str(store.path), _CountingEmbedder())
    try:
        store.append_event("alice", "user_input", {"content": "ALICE_FTS_ONLY"})
        memory._index_keywords("alice", store.get_events("alice"))
        store.append_event("bob", "user_input", {"content": "BOB_VECTOR_SOURCE"})
        memory.index_events("bob", store.get_events("bob"))

        assert all(
            memory._vec.get_by_id(row)[1]["agent"] == "bob"
            for row in _vector_rows(memory)
        )
        result = backfill_legacy_sources(store, memory, "alice")
        assert result.fts_patched == 1
        assert result.vector_patched == 0
        assert memory._fts.get_by_id(1)[1]["source_ref"] == "alice:e000000"
        assert all(
            "source_ref" not in memory._vec.get_by_id(row)[1]
            for row in _vector_rows(memory)
        )
    finally:
        memory.close()
        store.close(update_status=False)
