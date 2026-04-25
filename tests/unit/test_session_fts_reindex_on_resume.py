"""FTS + vector reindex when embedding dimensions change between runs.

``session/memory.py:SessionMemory.index_events`` (~line 170-176)
detects the "vectors enabled but empty" case and forces a full
re-index. This exercises the scenario from the audit: a session is
resumed with a different embedder than the one it was originally
indexed with; vector search should still work after the reindex.
"""

import os
import tempfile

import numpy as np
import pytest

from kohakuterrarium.session.embedding import BaseEmbedder
from kohakuterrarium.session.memory import SessionMemory


class FakeEmbedder4D(BaseEmbedder):
    """Deterministic 4-dim embedder."""

    dimensions = 4

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = []
        for text in texts:
            h = hash(text) & 0xFFFFFFFF
            v = np.array(
                [(h >> i) & 0xFF for i in range(0, 32, 8)],
                dtype=np.float32,
            )
            norm = np.linalg.norm(v)
            vecs.append(v / norm if norm > 0 else v)
        return np.array(vecs, dtype=np.float32)


class FakeEmbedder8D(BaseEmbedder):
    """Deterministic 8-dim embedder (incompatible with 4D table)."""

    dimensions = 8

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = []
        for text in texts:
            h = hash(text) & 0xFFFFFFFFFFFFFFFF
            v = np.array(
                [(h >> i) & 0xFF for i in range(0, 64, 8)],
                dtype=np.float32,
            )
            norm = np.linalg.norm(v)
            vecs.append(v / norm if norm > 0 else v)
        return np.array(vecs, dtype=np.float32)


@pytest.fixture
def tmp_db():
    fd, path = tempfile.mkstemp(suffix=".kohakutr")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def sample_events():
    """Simple two-round event stream."""
    return [
        {"type": "user_input", "content": "Fix the auth bug", "ts": 1000.0},
        {"type": "processing_start", "ts": 1000.1},
        {
            "type": "text",
            "content": "I'll investigate the auth module today.",
            "ts": 1001.0,
        },
        {
            "type": "tool_call",
            "name": "read",
            "args": {"path": "src/auth.py"},
            "ts": 1002.0,
        },
        {
            "type": "tool_result",
            "name": "read",
            "output": "def login(user): return make_token(user)",
            "ts": 1003.0,
        },
        {"type": "processing_end", "ts": 1005.0},
        {"type": "user_input", "content": "Now add an expiry field", "ts": 2000.0},
        {"type": "processing_start", "ts": 2000.1},
        {
            "type": "text",
            "content": "Added expiry to the token payload.",
            "ts": 2001.0,
        },
        {"type": "processing_end", "ts": 2002.0},
    ]


class TestFTSOnlyToVectorResume:
    """Open FTS-only first, then reopen with an embedder -> vectors rebuild."""

    def test_vectors_rebuild_when_embedder_added_on_resume(self, tmp_db, sample_events):
        # First run: index FTS only (no embedder).
        mem1 = SessionMemory(tmp_db)
        mem1.index_events("agent", sample_events)
        stats1 = mem1.get_stats()
        assert stats1["fts_blocks"] > 0
        assert stats1["vec_blocks"] == 0

        # Second run: resume with an embedder. The "vectors enabled
        # but empty" branch in index_events should force a full
        # rebuild of both FTS and vector indexes.
        mem2 = SessionMemory(tmp_db, embedder=FakeEmbedder4D())
        count = mem2.index_events("agent", sample_events)
        stats2 = mem2.get_stats()
        assert count > 0
        assert stats2["vec_blocks"] > 0
        # FTS + vector counts now agree.
        assert stats2["fts_blocks"] == stats2["vec_blocks"]

    def test_semantic_search_works_after_reindex(self, tmp_db, sample_events):
        mem1 = SessionMemory(tmp_db)
        mem1.index_events("agent", sample_events)

        mem2 = SessionMemory(tmp_db, embedder=FakeEmbedder4D())
        mem2.index_events("agent", sample_events)

        results = mem2.search("authentication issue", mode="semantic", k=5)
        assert len(results) > 0


class TestDimensionChangeBetweenRuns:
    """Switching to a differently-dimensioned embedder picks a fresh table."""

    def test_4d_to_8d_switch_populates_new_table(self, tmp_db, sample_events):
        # First run with 4D embedder.
        mem1 = SessionMemory(tmp_db, embedder=FakeEmbedder4D())
        mem1.index_events("agent", sample_events)
        stats1 = mem1.get_stats()
        assert stats1["dimensions"] == 4
        assert stats1["vec_blocks"] > 0

        # Second run with 8D embedder. A different vector table name
        # is chosen (memory_vec_8d); it starts empty so the rebuild
        # path in index_events kicks in.
        mem2 = SessionMemory(tmp_db, embedder=FakeEmbedder8D())
        reindexed = mem2.index_events("agent", sample_events)
        stats2 = mem2.get_stats()
        assert stats2["dimensions"] == 8
        assert reindexed > 0
        assert stats2["vec_blocks"] > 0
        # FTS should have been cleared + repopulated to match.
        assert stats2["fts_blocks"] == stats2["vec_blocks"]

    def test_search_still_works_after_dim_switch(self, tmp_db, sample_events):
        mem1 = SessionMemory(tmp_db, embedder=FakeEmbedder4D())
        mem1.index_events("agent", sample_events)

        mem2 = SessionMemory(tmp_db, embedder=FakeEmbedder8D())
        mem2.index_events("agent", sample_events)

        # Both FTS and hybrid search return hits against the fresh index.
        fts_hits = mem2.search("auth", mode="fts", k=5)
        assert len(fts_hits) > 0

        hybrid_hits = mem2.search("auth token", mode="hybrid", k=5)
        assert len(hybrid_hits) > 0


class TestIncrementalIndexingStillWorks:
    """After a reindex, incremental indexing continues to skip processed events."""

    def test_no_double_index_after_rebuild(self, tmp_db, sample_events):
        mem1 = SessionMemory(tmp_db)
        mem1.index_events("agent", sample_events)

        mem2 = SessionMemory(tmp_db, embedder=FakeEmbedder4D())
        mem2.index_events("agent", sample_events)
        stats_after_rebuild = mem2.get_stats()

        # Calling again with the same events is a no-op.
        added = mem2.index_events("agent", sample_events)
        assert added == 0
        stats_after_second_call = mem2.get_stats()
        assert (
            stats_after_second_call["vec_blocks"] == stats_after_rebuild["vec_blocks"]
        )
