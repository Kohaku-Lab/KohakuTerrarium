"""Unit tests for :mod:`kohakuterrarium.studio.sessions.memory_search`."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.session.embedding import NullEmbedder
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions import memory_build as build_mod
from kohakuterrarium.studio.sessions import memory_search as mem_mod


def _store(tmp_path, name="s.kohakutr") -> SessionStore:
    return SessionStore(str(tmp_path / name))


# ── _live_store_for_path ──────────────────────────────────────


class TestLiveStoreForPath:
    def test_no_creatures(self):
        engine = SimpleNamespace(list_creatures=lambda: [])
        agent, store = mem_mod._live_store_for_path(engine, Path("/x.kohakutr"))
        assert agent is None
        assert store is None

    def test_matches_by_path(self, tmp_path):
        path = tmp_path / "s.kohakutr"
        # Build a real store so ``_path`` is set.
        store = SessionStore(str(path))
        try:
            agent = SimpleNamespace(session_store=store)
            creature = SimpleNamespace(agent=agent)
            engine = SimpleNamespace(list_creatures=lambda: [creature])
            found_agent, found_store = mem_mod._live_store_for_path(engine, path)
            assert found_agent is agent
            assert found_store is store
        finally:
            store.close()

    def test_no_session_store_attribute(self):
        agent = SimpleNamespace(session_store=None)
        creature = SimpleNamespace(agent=agent)
        engine = SimpleNamespace(list_creatures=lambda: [creature])
        a, s = mem_mod._live_store_for_path(engine, Path("/x"))
        assert a is None
        assert s is None


# ── _resolve_embed_config ─────────────────────────────────────


class TestResolveEmbedConfig:
    def test_from_store_state(self, tmp_path):
        store = _store(tmp_path)
        try:
            store.state["embedding_config"] = {"provider": "x"}
            out = mem_mod._resolve_embed_config(store, None)
            assert out == {"provider": "x"}
        finally:
            store.close()

    def test_from_live_agent_config(self, tmp_path):
        store = _store(tmp_path)
        try:
            agent = SimpleNamespace(
                config=SimpleNamespace(memory={"embedding": {"provider": "y"}})
            )
            out = mem_mod._resolve_embed_config(store, agent)
            assert out == {"provider": "y"}
        finally:
            store.close()

    def test_default(self, tmp_path):
        store = _store(tmp_path)
        try:
            out = mem_mod._resolve_embed_config(store, None)
            assert out == {"provider": "auto"}
        finally:
            store.close()


# ── build_embeddings ─────────────────────────────────────────


class TestBuildEmbeddings:
    def test_basic(self, tmp_path, monkeypatch):
        # Seed a store with some events.
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        try:
            store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            store.append_event("alice", "user_input", {"content": "hi"})
            store.flush()
        finally:
            store.close()

        # Stub the embedder + memory to avoid heavy deps.
        class _FakeEmbedder:
            dimensions = 0

            def encode(self, texts):
                return []

        monkeypatch.setattr(build_mod, "create_embedder", lambda cfg: _FakeEmbedder())

        class _FakeMemory:
            has_vectors = False

            def __init__(self, *a, **kw):
                pass

            def close(self):
                pass

            def index_events(self, agent, events):
                return len(events)

            def get_stats(self):
                return {"fts": 1}

            def _set_indexed_count(self, *a, **kw):
                pass

            def _clear_fts(self, *a, **kw):
                pass

        monkeypatch.setattr(build_mod, "SessionMemory", _FakeMemory)
        out = mem_mod.build_embeddings(path)
        assert out["path"] == str(path)
        assert "alice" in out["agents"]
        assert "alice" in out["indexed_per_agent"]
        assert out["stats"] == {"fts": 1}

    def test_no_events_for_agent(self, tmp_path, monkeypatch):
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        try:
            store.init_meta("sess", "agent", "/p", "/w", ["alice"])
        finally:
            store.close()

        class _FakeEmbedder:
            dimensions = 0

            def encode(self, texts):
                return []

        monkeypatch.setattr(build_mod, "create_embedder", lambda cfg: _FakeEmbedder())

        class _FakeMemory:
            has_vectors = False

            def __init__(self, *a, **kw):
                pass

            def close(self):
                pass

            def index_events(self, *a, **kw):
                return 0

            def get_stats(self):
                return {}

            def _set_indexed_count(self, *a, **kw):
                pass

            def _clear_fts(self, *a, **kw):
                pass

        monkeypatch.setattr(build_mod, "SessionMemory", _FakeMemory)
        out = mem_mod.build_embeddings(path)
        # No events → indexed_per_agent reports {events: 0, blocks: 0}.
        assert out["indexed_per_agent"]["alice"] == {"events": 0, "blocks": 0}


# ── search_session_memory ────────────────────────────────────


class TestSearchSessionMemory:
    async def test_search_runs(self, tmp_path, monkeypatch):
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        try:
            store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            store.append_event("alice", "user_input", {"content": "hi"})
            store.flush()
        finally:
            store.close()

        # Disable live-store detection.
        monkeypatch.setattr(
            mem_mod, "_live_store_for_path", lambda eng, p: (None, None)
        )

        class _FakeEmbedder:
            dimensions = 0

        monkeypatch.setattr(mem_mod, "create_embedder", lambda cfg: _FakeEmbedder())

        # Build a memory stub that returns fake SearchResult-shaped items.
        from kohakuterrarium.session.memory import SearchResult

        class _FakeMemory:
            def __init__(self, *a, **kw):
                pass

            def close(self):
                pass

            def index_events(self, *a, **kw):
                return 0

            def search(self, query, mode, k, agent):
                return [
                    SearchResult(
                        content="hi",
                        round_num=0,
                        block_num=0,
                        agent="alice",
                        block_type="user",
                        score=0.5,
                    )
                ]

        monkeypatch.setattr(mem_mod, "SessionMemory", _FakeMemory)
        out = await mem_mod.search_session_memory(
            path, q="hi", mode="fts", k=5, agent="alice", engine=SimpleNamespace()
        )
        assert out["session_name"] == "s"
        assert out["count"] == 1
        assert out["results"][0]["content"] == "hi"

    async def test_failure_raises_session_error(self, tmp_path, monkeypatch):
        path = tmp_path / "no.kohakutr"
        path.write_bytes(b"")

        def boom(*a, **kw):
            raise RuntimeError("dead")

        monkeypatch.setattr(mem_mod, "_live_store_for_path", boom)
        from kohakuterrarium.errors import SessionError

        with pytest.raises(SessionError) as exc:
            await mem_mod.search_session_memory(
                path,
                q="x",
                mode="fts",
                k=5,
                agent=None,
                engine=SimpleNamespace(),
            )
        assert "Memory search failed" in str(exc.value)

    @staticmethod
    def _seed_store(path: Path) -> None:
        store = SessionStore(str(path))
        try:
            store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            store.append_event("alice", "user_input", {"content": "hello world"})
            store.append_event("alice", "text_chunk", {"content": "general reply"})
            store.flush()
        finally:
            store.close()

    async def test_semantic_without_embedder_falls_back_to_fts(
        self, tmp_path, monkeypatch
    ):
        """Legacy HTTP contract: explicit ``semantic`` with no embedding
        model answers with FTS hits — NOT a SessionError (which the API
        adapter would surface as a 500).  ``SessionMemory.search`` is
        strict post-E4; the studio adapter must degrade gracefully
        because the frontend's Find tab / StatePanel offer "semantic"
        before any index exists (regression caught by the old-vs-new
        differential probe)."""
        path = tmp_path / "s.kohakutr"
        self._seed_store(path)
        monkeypatch.setattr(
            mem_mod, "_live_store_for_path", lambda eng, p: (None, None)
        )
        monkeypatch.setattr(mem_mod, "create_embedder", lambda cfg: NullEmbedder())
        out = await mem_mod.search_session_memory(
            path, q="hello", mode="semantic", k=5, agent=None, engine=None
        )
        # Payload echoes the REQUESTED mode (old wire shape) but the
        # results come from the FTS fallback.
        assert out["mode"] == "semantic"
        assert out["count"] == len(out["results"]) >= 1
        assert out["results"][0]["content"] == "hello world"

    async def test_unknown_mode_falls_back_to_fts(self, tmp_path, monkeypatch):
        """An unrecognised ``mode`` used to silently run FTS (200); the
        strict library now raises ValueError — the adapter must keep
        answering with FTS results instead of erroring."""
        path = tmp_path / "s.kohakutr"
        self._seed_store(path)
        monkeypatch.setattr(
            mem_mod, "_live_store_for_path", lambda eng, p: (None, None)
        )
        monkeypatch.setattr(mem_mod, "create_embedder", lambda cfg: NullEmbedder())
        out = await mem_mod.search_session_memory(
            path, q="hello", mode="weird", k=5, agent=None, engine=None
        )
        assert out["mode"] == "weird"
        assert out["count"] == len(out["results"]) >= 1

    async def test_missing_path_raises_not_found_without_creating_it(self, tmp_path):
        from kohakuterrarium.errors import SessionNotFoundError

        ghost = tmp_path / "ghost.kohakutr"
        with pytest.raises(SessionNotFoundError):
            await mem_mod.search_session_memory(
                ghost, q="x", mode="fts", k=5, agent=None, engine=None
            )
        # The audit defect: the lookup minted an empty .kohakutr.
        assert not ghost.exists()
