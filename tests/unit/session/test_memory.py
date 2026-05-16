"""Unit tests for :mod:`kohakuterrarium.session.memory`."""

import time

import numpy as np

from kohakuterrarium.session.embedding import BaseEmbedder, NullEmbedder
from kohakuterrarium.session.memory import (
    Block,
    SearchResult,
    SessionMemory,
    _block_metadata,
    _extract_blocks,
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


class TestExtractBlocks:
    def test_user_input_starts_round(self):
        events = [
            {
                "type": "user_input",
                "content": "find the bug",
                "event_id": 1,
            }
        ]
        blocks = _extract_blocks("alice", events)
        assert len(blocks) == 1
        assert blocks[0].block_type == "user"
        assert blocks[0].round_num == 1

    def test_empty_user_input_skipped(self):
        events = [
            {"type": "user_input", "content": "   ", "event_id": 1},
        ]
        blocks = _extract_blocks("alice", events)
        assert blocks == []

    def test_trigger_fired_creates_round(self):
        events = [
            {
                "type": "trigger_fired",
                "channel": "ch1",
                "content": "ping",
                "event_id": 1,
            }
        ]
        blocks = _extract_blocks("alice", events)
        assert len(blocks) == 1
        assert blocks[0].block_type == "trigger"
        assert blocks[0].channel == "ch1"

    def test_text_only_indexed_inside_round(self):
        events = [
            # No user_input → in_round=False, text dropped.
            {"type": "text", "content": "orphan text", "event_id": 1},
        ]
        blocks = _extract_blocks("alice", events)
        assert blocks == []

    def test_text_chunk_indexed_inside_round(self):
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {"type": "text_chunk", "content": "first reply", "event_id": 2},
        ]
        blocks = _extract_blocks("alice", events)
        # 1 user + 1 text block.
        types = [b.block_type for b in blocks]
        assert "user" in types
        assert "text" in types

    def test_long_text_splits_on_double_newline(self):
        long_text = "para1\n\n" + ("y" * 350) + "\n\n" + "para3"
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {"type": "text", "content": long_text, "event_id": 2},
        ]
        blocks = _extract_blocks("alice", events)
        text_blocks = [b for b in blocks if b.block_type == "text"]
        assert len(text_blocks) == 3

    def test_tool_call_indexed(self):
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {
                "type": "tool_call",
                "name": "bash",
                "args": {"cmd": "ls"},
                "event_id": 2,
            },
        ]
        blocks = _extract_blocks("alice", events)
        tool_blocks = [b for b in blocks if b.block_type == "tool"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].tool_name == "bash"
        assert "cmd=ls" in tool_blocks[0].content

    def test_tool_result_indexed(self):
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {
                "type": "tool_result",
                "name": "bash",
                "output": "this is the output of the tool call",
                "event_id": 2,
            },
        ]
        blocks = _extract_blocks("alice", events)
        tool_blocks = [b for b in blocks if b.block_type == "tool"]
        assert len(tool_blocks) == 1
        assert "output" in tool_blocks[0].content

    def test_short_tool_result_skipped(self):
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {
                "type": "tool_result",
                "name": "x",
                "output": "ok",
                "event_id": 2,
            },
        ]
        blocks = _extract_blocks("alice", events)
        # Tool result content "[result:x] ok" is <=20 chars → skipped.
        tool_blocks = [b for b in blocks if b.block_type == "tool"]
        assert tool_blocks == []

    def test_processing_end_ends_round(self):
        events = [
            {"type": "user_input", "content": "q", "event_id": 1},
            {"type": "processing_end", "event_id": 2},
            # After processing_end, text events are no longer indexed.
            {"type": "text", "content": "post round", "event_id": 3},
        ]
        blocks = _extract_blocks("alice", events)
        types = [b.block_type for b in blocks]
        assert "text" not in types


# ── SearchResult ─────────────────────────────────────────────────


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

    def test_semantic_falls_back_to_fts_when_no_vec(self, tmp_path):
        m = self._setup(tmp_path)
        # Without an embedder, semantic falls back to FTS — which still
        # finds the postgres block by keyword.
        results = m.search("postgres", mode="semantic", k=5)
        assert len(results) == 1
        assert "postgres" in results[0].content.lower()

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

    def test_unknown_mode_falls_back_to_fts(self, tmp_path):
        m = self._setup(tmp_path)
        results = m.search("postgres", mode="not-a-mode", k=5)
        assert len(results) == 1
        assert "postgres" in results[0].content.lower()

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
