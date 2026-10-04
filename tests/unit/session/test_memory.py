"""Unit tests for :mod:`kohakuterrarium.session.memory`."""

import sqlite3
import time

import numpy as np
import pytest

from kohakuterrarium.session.embedding import BaseEmbedder, NullEmbedder
from kohakuterrarium.session.memory import (
    Block,
    SearchResult,
    SessionMemory,
    _block_metadata,
)

# ── _block_metadata ──────────────────────────────────────────────


class TestBlockMetadata:
    def test_basic(self):
        b = Block(
            round_num=1,
            block_num=0,
            agent="alice",
            block_type="text",
            content="hi",
            ts=100.0,
        )
        meta = _block_metadata(b)
        assert meta["round"] == 1
        assert meta["block"] == 0
        assert meta["agent"] == "alice"
        assert meta["type"] == "text"
        assert meta["ts"] == 100.0
        assert "content" not in meta

    def test_with_content(self):
        b = Block(
            round_num=0,
            block_num=0,
            agent="a",
            block_type="user",
            content="hello",
        )
        meta = _block_metadata(b, include_content=True)
        assert meta["content"] == "hello"


# ── _extract_blocks ──────────────────────────────────────────────


class TestSearchResultAgeStr:
    def test_no_ts(self):
        r = SearchResult(
            content="", round_num=0, block_num=0, agent="a", block_type="", score=0
        )
        assert r.age_str == ""

    def test_seconds(self):
        r = SearchResult(
            content="",
            round_num=0,
            block_num=0,
            agent="a",
            block_type="",
            score=0,
            ts=time.time() - 5,
        )
        # ~5 seconds ago — rendered in whole seconds (allow for the
        # sub-second drift between the two time.time() calls).
        assert r.age_str in {"4s ago", "5s ago"}

    def test_minutes(self):
        r = SearchResult(
            content="",
            round_num=0,
            block_num=0,
            agent="a",
            block_type="",
            score=0,
            ts=time.time() - 120,
        )
        # 120s → 2 minutes (int division floors).
        assert r.age_str == "2m ago"

    def test_hours(self):
        r = SearchResult(
            content="",
            round_num=0,
            block_num=0,
            agent="a",
            block_type="",
            score=0,
            ts=time.time() - 7200,
        )
        # 7200s → 2.0 hours, rendered with one decimal.
        assert r.age_str == "2.0h ago"


# ── SessionMemory ────────────────────────────────────────────────


def _close_memory(m) -> None:
    """Close every KVault a SessionMemory owns so the SQLite file is
    released (SessionMemory has no public close())."""
    for attr in ("_fts", "_state", "_vec"):
        obj = getattr(m, attr, None)
        if obj is not None:
            try:
                obj.close()
            except Exception:
                pass


class _FakeEmbedder(BaseEmbedder):
    dimensions = 4

    def encode(self, texts):
        # Deterministic: bag-of-bytes ratio for first 4 dims.
        out = []
        for text in texts:
            arr = np.zeros(4, dtype=np.float32)
            for i, ch in enumerate(text[:4]):
                arr[i] = (ord(ch) % 10) / 10.0
            out.append(arr)
        return np.array(out, dtype=np.float32)


class TestSessionMemoryConstruction:
    def test_null_embedder_no_vectors(self, tmp_path):
        m = SessionMemory(str(tmp_path / "mem.db"))
        assert m.has_vectors is False
        assert isinstance(m._embedder, NullEmbedder)

    def test_with_embedder_has_vectors(self, tmp_path):
        m = SessionMemory(str(tmp_path / "mem.db"), _FakeEmbedder())
        assert m.has_vectors is True

    def test_get_stats(self, tmp_path):
        m = SessionMemory(str(tmp_path / "mem.db"))
        stats = m.get_stats()
        assert stats["has_vectors"] is False
        assert stats["dimensions"] == 0


class TestSessionMemoryIndexing:
    def test_rebuild_uses_native_handles_and_preserves_other_agents(
        self, tmp_path, monkeypatch
    ):
        def forbidden_connection(*args, **kwargs):
            raise AssertionError("A second SQLite library must not open a live vault")

        path = str(tmp_path / "native.db")
        memory = SessionMemory(path, _FakeEmbedder())
        rows = [{"type": "user_input", "content": "alice needle"}]
        try:
            with monkeypatch.context() as patch:
                patch.setattr(sqlite3, "connect", forbidden_connection)
                memory.index_events("alice", rows)
                memory.index_events(
                    "bob", [{"type": "user_input", "content": "bob needle"}]
                )
                for _ in range(2):
                    memory.index_events("alice", [])
                    for mode in ("fts", "semantic", "hybrid"):
                        assert [
                            h.content for h in memory.search("needle", mode=mode)
                        ] == ["bob needle"]
                    memory.index_events("alice", rows)
                    assert memory.get_stats()["vec_blocks"] == 2
        finally:
            memory.close()
        memory = SessionMemory(path, _FakeEmbedder())
        try:
            for mode in ("fts", "semantic", "hybrid"):
                assert {h.content for h in memory.search("needle", mode=mode)} == {
                    "alice needle",
                    "bob needle",
                }
        finally:
            memory.close()

    def test_legacy_indexes_rebuild_on_use_and_preserve_other_agents(self, tmp_path):
        path = str(tmp_path / "migration.db")
        memory = SessionMemory(path, _FakeEmbedder())
        rows = [
            {"type": "user_input", "content": "needle prompt"},
            {"type": "text", "content": "needle reply"},
        ]
        try:
            memory.index_events("alice", rows[:1])
            memory.index_events(
                "bob", [{"type": "user_input", "content": "bob needle"}]
            )
            for kind in ("keywords", "vectors:4"):
                del memory._state[f"alice:cursor:{kind}"]
                memory._state[f"alice:indexed_{kind}"] = len(rows)
            before = memory.get_stats()
        finally:
            memory.close()
        memory = SessionMemory(path, _FakeEmbedder())
        try:
            assert memory.get_stats() == before
            memory.index_events("alice", rows)
            for mode in ("fts", "semantic", "hybrid"):
                assert {r.content for r in memory.search("needle", mode=mode)} == {
                    "needle prompt",
                    "needle reply",
                    "bob needle",
                }
            assert memory.get_stats()["vec_blocks"] == 3
        finally:
            memory.close()

    def test_incremental_encode_only_sees_new_blocks_and_force_does_not_duplicate(
        self, tmp_path
    ):
        class CountingEmbedder(_FakeEmbedder):
            def __init__(self):
                self.inputs = []

            def encode(self, texts):
                self.inputs.extend(texts)
                return super().encode(texts)

        embedder = CountingEmbedder()
        memory = SessionMemory(str(tmp_path / "cost.db"), embedder)
        rows = [{"type": "user_input", "content": "needle prompt"}]
        try:
            memory.index_events("alice", rows)
            rows.append({"type": "text", "content": "needle reply"})
            memory.index_events("alice", rows)
            memory.index_events("alice", rows)
            assert embedder.inputs == ["needle prompt", "needle reply"]
            for _ in range(2):
                memory._set_indexed_count("alice", 0)
                memory._clear_fts("alice")
                memory.index_events("alice", rows)
                assert memory.get_stats()["vec_blocks"] == 2
                assert memory.get_stats()["fts_blocks"] == 2
        finally:
            memory.close()

    @pytest.mark.parametrize("kind", ["keywords", "vectors:4"])
    def test_interrupted_batch_recovers_without_duplicate_rows(
        self, tmp_path, monkeypatch, kind
    ):
        path = str(tmp_path / "interrupted.db")
        memory = SessionMemory(path, _FakeEmbedder())
        rows = [
            {"type": "user_input", "content": "needle prompt"},
            {"type": "text", "content": "needle reply"},
        ]
        vault = memory._fts if kind == "keywords" else memory._vec
        insert = vault.insert
        calls = 0

        def failing_insert(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("interrupted write")
            return insert(*args, **kwargs)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(vault, "insert", failing_insert)
                with pytest.raises(OSError, match="interrupted write"):
                    memory.index_events("alice", rows)
        finally:
            memory.close()
        memory = SessionMemory(path, _FakeEmbedder())
        try:
            memory.index_events("alice", rows)
            assert memory.get_stats()["fts_blocks"] == 2
            assert memory.get_stats()["vec_blocks"] == 2
            assert len(memory.search("needle", mode="hybrid")) == 2
        finally:
            memory.close()

    def test_truncated_history_removes_rows_and_reindexes_after_empty_reset(
        self, tmp_path
    ):
        memory = SessionMemory(str(tmp_path / "truncate.db"), _FakeEmbedder())
        rows = [
            {"type": "user_input", "content": "needle prompt"},
            {"type": "text", "content": "needle reply"},
        ]
        try:
            memory.index_events("alice", rows)
            memory.index_events("alice", [])
            assert memory.search("needle", mode="hybrid") == []
            memory.index_events("alice", rows[:1])
            assert len(memory.search("needle", mode="hybrid")) == 1
        finally:
            memory.close()

    def test_partial_rounds_keep_blocks_and_global_round_numbers(self, tmp_path):
        path = str(tmp_path / "partial.db")
        rows = []
        expected = []
        for turn in range(3):
            for row in (
                {"type": "user_input", "content": f"needle question {turn}"},
                {"type": "text", "content": f"needle answer {turn}"},
                {"type": "processing_end"},
            ):
                rows.append(row)
                memory = SessionMemory(path, _FakeEmbedder())
                try:
                    memory.index_events("alice", rows)
                    if row["type"] != "processing_end":
                        expected.append(row["content"])
                    for mode in ("fts", "semantic", "hybrid"):
                        hits = memory.search("needle", mode=mode, k=20)
                        assert {hit.content for hit in hits} == set(expected)
                        assert {hit.round_num for hit in hits} == set(
                            range(1, turn + 2)
                        )
                finally:
                    memory.close()

    def test_keyword_progress_does_not_skip_late_vectors_or_duplicate_boundary(
        self, tmp_path
    ):
        rows = [{"type": "user_input", "content": "needle question", "event_id": 1}]
        memory = SessionMemory(str(tmp_path / "catchup.db"))
        try:
            memory.index_events("alice", rows)
            rows.extend(
                [
                    {**rows[0], "event_id": 2},
                    {
                        "type": "tool_result",
                        "name": "read",
                        "output": "needle tool result",
                        "event_id": 3,
                    },
                ]
            )
            memory.index_events("alice", rows)
            memory._set_embedder(_FakeEmbedder())
            memory.index_events("alice", rows)
            assert memory.get_stats()["fts_blocks"] == 2
            assert memory.get_stats()["vec_blocks"] == 2
            assert len(memory.search("needle", mode="hybrid")) == 2
        finally:
            memory.close()

    def test_branch_change_replaces_obsolete_hits_and_fences_prepared_vectors(
        self, tmp_path
    ):
        rows = [
            {
                "type": "user_input",
                "content": "needle old",
                "turn_index": 1,
                "branch_id": 1,
                "event_id": 1,
            },
            {
                "type": "text",
                "content": "needle answer",
                "turn_index": 1,
                "branch_id": 1,
                "event_id": 2,
            },
        ]
        memory = SessionMemory(str(tmp_path / "branch.db"), _FakeEmbedder())
        try:
            memory.index_events("alice", rows[:1])
            start, blocks = memory._vector_batch("alice", rows)
            rows.append(
                {
                    "type": "user_input",
                    "content": "needle replacement",
                    "turn_index": 1,
                    "branch_id": 2,
                    "event_id": 3,
                }
            )
            memory._index_keywords("alice", rows)
            with pytest.raises(RuntimeError, match="index changed"):
                memory._commit_vectors(
                    "alice",
                    start,
                    2,
                    blocks,
                    _FakeEmbedder().encode([b.content for b in blocks]),
                )
            memory.index_events("alice", rows)
            for mode in ("fts", "semantic", "hybrid"):
                assert [hit.content for hit in memory.search("needle", mode=mode)] == [
                    "needle replacement"
                ]
        finally:
            memory.close()

    def test_fts_then_vectors_indexes_every_agent_and_catches_up(self, tmp_path):
        memory = SessionMemory(str(tmp_path / "multi.db"))
        events = {
            "alice": [{"type": "user_input", "content": "alice needle"}],
            "bob": [{"type": "user_input", "content": "bob needle"}],
        }
        try:
            for name, rows in events.items():
                memory._index_keywords(name, rows)
            memory._set_embedder(_FakeEmbedder())
            for name, rows in events.items():
                memory.index_events(name, rows)
                hits = memory.search(f"{name} needle", mode="semantic", agent=name)
                assert [hit.content for hit in hits] == [f"{name} needle"]
            events["alice"].append({"type": "user_input", "content": "later needle"})
            memory._index_keywords("alice", events["alice"])
            memory.index_events("alice", events["alice"])
            assert memory.get_stats()["vec_blocks"] == 3
            assert memory.index_events("alice", events["alice"]) == 0
        finally:
            memory.close()

    def test_force_reset_also_rewinds_vector_progress(self, tmp_path):
        memory = SessionMemory(str(tmp_path / "force.db"), _FakeEmbedder())
        try:
            rows = [{"type": "user_input", "content": "needle"}]
            memory.index_events("alice", rows)
            memory._set_indexed_count("alice", 0)
            start, blocks = memory._vector_batch("alice", rows)
            assert start.start == 0
            assert [block.content for block in blocks] == ["needle"]
        finally:
            memory.close()

    def test_legacy_progress_survives_keyword_catchup(self, tmp_path):
        path = str(tmp_path / "legacy.db")
        memory = SessionMemory(path, _FakeEmbedder())
        rows = [{"type": "user_input", "content": "old needle"}]
        memory.index_events("alice", rows)
        memory._state["alice:indexed_events"] = 1
        del memory._state["alice:indexed_keywords"]
        del memory._state["alice:indexed_vectors:4"]
        memory.close()
        memory = SessionMemory(path)
        try:
            rows.append({"type": "user_input", "content": "new needle"})
            memory.index_events("alice", rows)
            memory._set_embedder(_FakeEmbedder())
            memory.index_events("alice", rows)
            assert memory.get_stats()["vec_blocks"] == 2
            assert {
                hit.content for hit in memory.search("needle", mode="semantic")
            } == {
                "old needle",
                "new needle",
            }
        finally:
            memory.close()

    def test_prepared_vectors_reject_stale_or_incomplete_results(self, tmp_path):
        memory = SessionMemory(str(tmp_path / "commit.db"), _FakeEmbedder())
        rows = [{"type": "user_input", "content": "needle"}]
        try:
            start, blocks = memory._vector_batch("alice", rows)
            with pytest.raises(ValueError, match="result count"):
                memory._commit_vectors("alice", start, 1, blocks, [])
            memory.index_events("alice", rows)
            with pytest.raises(RuntimeError, match="index changed"):
                memory._commit_vectors("alice", start, 1, blocks, [])
            assert memory.get_stats()["vec_blocks"] == 1
        finally:
            memory.close()

    def _events(self):
        return [
            {"type": "user_input", "content": "fix the auth bug", "event_id": 1},
            {"type": "text", "content": "checking the login code", "event_id": 2},
            {
                "type": "tool_call",
                "name": "bash",
                "args": {"cmd": "grep auth"},
                "event_id": 3,
            },
        ]

    def test_first_index_creates_blocks(self, tmp_path):
        m = SessionMemory(str(tmp_path / "mem.db"))
        n = m.index_events("alice", self._events())
        # user_input + text + tool_call → one block each.
        assert n == 3

    def test_incremental_index_is_idempotent(self, tmp_path):
        m = SessionMemory(str(tmp_path / "mem.db"))
        events = self._events()
        n1 = m.index_events("alice", events)
        n2 = m.index_events("alice", events)
        # First call indexes all 3 blocks; second is a pure no-op.
        assert n1 == 3
        assert n2 == 0

    def test_empty_events_indexes_zero(self, tmp_path):
        m = SessionMemory(str(tmp_path / "mem.db"))
        assert m.index_events("alice", []) == 0

    def test_start_from_skips_events(self, tmp_path):
        m = SessionMemory(str(tmp_path / "mem.db"))
        events = self._events()
        # Skip past everything → no blocks.
        n = m.index_events("alice", events, start_from=len(events))
        assert n == 0

    def test_indexed_count_persists(self, tmp_path):
        m = SessionMemory(str(tmp_path / "mem.db"))
        events = self._events()
        m.index_events("alice", events)
        assert m._get_indexed_count("alice") == len(events)


class TestSessionMemorySearch:
    def _setup(self, tmp_path, embedder=None):
        m = SessionMemory(str(tmp_path / "mem.db"), embedder=embedder)
        events = [
            {"type": "user_input", "content": "fix authentication bug", "event_id": 1},
            {"type": "text", "content": "looking at the login code", "event_id": 2},
            {"type": "user_input", "content": "now check the database", "event_id": 3},
            {
                "type": "text",
                "content": "checking the postgres connection",
                "event_id": 4,
            },
        ]
        m.index_events("alice", events)
        return m

    def test_fts_search(self, tmp_path):
        m = self._setup(tmp_path)
        results = m.search("authentication", mode="fts", k=5)
        # FTS finds exactly the block whose text contains the term.
        assert len(results) == 1
        assert "authentication" in results[0].content.lower()
        assert results[0].agent == "alice"

    def test_fts_search_with_agent_filter(self, tmp_path):
        m = self._setup(tmp_path)
        results = m.search("authentication", mode="fts", agent="alice", k=5)
        # The matching block belongs to alice, so the filter keeps it.
        assert len(results) == 1
        assert results[0].agent == "alice"
        assert "authentication" in results[0].content.lower()

    def test_fts_search_filters_other_agent(self, tmp_path):
        m = self._setup(tmp_path)
        # Searching for an agent that doesn't exist returns empty.
        results = m.search("authentication", mode="fts", agent="other", k=5)
        assert results == []

    def test_semantic_without_vec_raises(self, tmp_path):
        m = self._setup(tmp_path)
        # E4: an EXPLICIT semantic request without an embedder raises —
        # it used to silently degrade to FTS.
        with pytest.raises(ValueError, match="embedding model"):
            m.search("postgres", mode="semantic", k=5)

    def test_hybrid_falls_back_to_fts_when_no_vec(self, tmp_path):
        m = self._setup(tmp_path)
        results = m.search("postgres", mode="hybrid", k=5)
        assert len(results) == 1
        assert "postgres" in results[0].content.lower()

    def test_auto_mode_picks_fts_when_no_vec(self, tmp_path):
        m = self._setup(tmp_path)
        results = m.search("postgres", mode="auto", k=5)
        assert len(results) == 1
        assert "postgres" in results[0].content.lower()

    def test_unknown_mode_raises(self, tmp_path):
        m = self._setup(tmp_path)
        with pytest.raises(ValueError, match="Unknown search mode"):
            m.search("postgres", mode="not-a-mode", k=5)

    def test_semantic_with_embedder(self, tmp_path):
        m = self._setup(tmp_path, embedder=_FakeEmbedder())
        results = m.search("postgres", mode="semantic", k=5)
        # Vector search runs against all 4 indexed blocks; every result
        # is a real SearchResult drawn from the indexed corpus.
        corpus = {
            "fix authentication bug",
            "looking at the login code",
            "now check the database",
            "checking the postgres connection",
        }
        assert len(results) == 4
        assert {r.content for r in results} == corpus

    def test_hybrid_with_embedder(self, tmp_path):
        m = self._setup(tmp_path, embedder=_FakeEmbedder())
        results = m.search("postgres", mode="hybrid", k=5)
        # Hybrid fuses FTS + vector — the keyword-matching postgres
        # block ranks first.
        assert len(results) >= 1
        assert "postgres" in results[0].content.lower()

    def test_auto_with_embedder_uses_hybrid(self, tmp_path):
        m = self._setup(tmp_path, embedder=_FakeEmbedder())
        results = m.search("postgres", mode="auto", k=5)
        # auto → hybrid when vectors are available; postgres ranks first.
        assert len(results) >= 1
        assert "postgres" in results[0].content.lower()

    def test_fts_search_respects_k_limit(self, tmp_path):
        # Index several blocks that all match the query, then ask for
        # k=1 — the FTS search must stop at the k ceiling.
        m = SessionMemory(str(tmp_path / "mem.db"))
        events = [
            {"type": "user_input", "content": "alpha alpha one", "event_id": 1},
            {"type": "user_input", "content": "alpha alpha two", "event_id": 2},
            {
                "type": "user_input",
                "content": "alpha alpha three",
                "event_id": 3,
            },
        ]
        m.index_events("alice", events)
        results = m.search("alpha", mode="fts", k=1)
        # Over-fetch internally, but the result list is capped at k.
        assert len(results) == 1

    def test_semantic_search_respects_k_limit(self, tmp_path):
        m = self._setup(tmp_path, embedder=_FakeEmbedder())
        results = m.search("postgres", mode="semantic", k=1)
        # Vector search over-fetches k*2 then caps the output at k.
        assert len(results) == 1

    def test_semantic_search_agent_filter(self, tmp_path):
        # _search_semantic must drop blocks whose agent != the filter.
        m = self._setup(tmp_path, embedder=_FakeEmbedder())
        results = m.search("postgres", mode="semantic", agent="alice", k=5)
        assert all(r.agent == "alice" for r in results)
        # A filter for a non-existent agent yields nothing.
        none = m.search("postgres", mode="semantic", agent="ghost", k=5)
        assert none == []

    def test_search_semantic_helper_returns_empty_without_vec(self, tmp_path):
        # Calling _search_semantic directly on a NullEmbedder store
        # short-circuits to [] (no vector index).
        m = SessionMemory(str(tmp_path / "mem.db"))
        assert m._search_semantic("anything", k=5, agent=None) == []


class TestSessionMemoryIndexingEdgeCases:
    def test_events_with_no_blocks_advances_indexed_count(self, tmp_path):
        # Events that never open a round (no user_input / trigger_fired)
        # produce zero blocks, but the indexed-count still advances so a
        # later incremental index doesn't re-scan them.
        m = SessionMemory(str(tmp_path / "mem.db"))
        events = [
            {"type": "text", "content": "orphan text", "event_id": 1},
            {"type": "processing_end", "event_id": 2},
        ]
        n = m.index_events("alice", events)
        assert n == 0
        # The count advanced past the (block-less) events.
        assert m._get_indexed_count("alice") == len(events)

    def test_adding_embedder_later_forces_full_reindex(self, tmp_path):
        # Index FTS-only first (no embedder), then reopen the same db
        # WITH an embedder: the vector index is empty but blocks were
        # already counted, so index_events clears FTS and rebuilds.
        db = str(tmp_path / "mem.db")
        events = [
            {"type": "user_input", "content": "fix auth", "event_id": 1},
            {"type": "text", "content": "looking at login", "event_id": 2},
        ]
        m_fts = SessionMemory(db)
        assert m_fts.index_events("alice", events) == 2
        _close_memory(m_fts)

        # Reopen with an embedder — vec_needs_rebuild path fires.
        m_vec = SessionMemory(db, embedder=_FakeEmbedder())
        try:
            rebuilt = m_vec.index_events("alice", events)
            # All blocks were re-indexed (FTS cleared + vectors built).
            assert rebuilt == 2
            # The rebuilt corpus is searchable via the vector index.
            results = m_vec.search("login", mode="semantic", k=5)
            assert len(results) == 2
        finally:
            _close_memory(m_vec)

    def test_reopen_with_embedder_restores_saved_dimensions(self, tmp_path):
        # A memory db opened once with an embedder persists
        # ``vec_dimensions``; reopening with an embedder restores the
        # vector store from that saved dimension.
        db = str(tmp_path / "mem.db")
        m1 = SessionMemory(db, embedder=_FakeEmbedder())
        try:
            m1.index_events(
                "alice",
                [{"type": "user_input", "content": "hello", "event_id": 1}],
            )
        finally:
            _close_memory(m1)
        # Reopen — the saved vec_dimensions drives the VectorKVault.
        m2 = SessionMemory(db, embedder=_FakeEmbedder())
        try:
            assert m2.has_vectors is True
            assert m2.get_stats()["dimensions"] == 4
        finally:
            _close_memory(m2)
