"""Coverage tests for ``studio.sessions.memory_search``.

Exercises ``build_embeddings`` (offline / CLI path) and
``search_session_memory`` (HTTP path) end-to-end with a tmp_path
``SessionStore`` populated by hand.

Embedding providers are stubbed to avoid pulling model2vec /
sentence-transformer.  We only validate the orchestration logic, not
the actual semantic-search quality.
"""

from pathlib import Path
from typing import Any

import pytest

import kohakuterrarium.studio.sessions.memory_search as ms
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine import Terrarium

from tests.unit.studio_sessions._fakes import install_fake_creature

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _make_populated_store(path: Path, agent: str = "alice") -> SessionStore:
    """Build a session store with a few events to index."""
    store = SessionStore(path)
    store.init_meta(
        session_id="ms_test",
        config_type="agent",
        config_path="examples/agent-apps/swe_agent",
        pwd=str(path.parent),
        agents=[agent],
    )
    store.append_event(agent, "user_input", {"content": "hello world"})
    store.append_event(agent, "text", {"content": "general kenobi"})
    store.append_event(agent, "tool_call", {"name": "read", "args": {"path": "x"}})
    return store


class _StubEmbedder:
    """No-op embedder — returns deterministic short vectors so the
    indexer doesn't need a real model."""

    dimension = 4
    name = "stub"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1 * i] * 4 for i, _ in enumerate(texts, start=1)]


class _FakeMemory:
    """Stand-in for ``SessionMemory`` so we can assert the orchestration
    without touching FTS5."""

    def __init__(self, db_path: str, *, embedder=None, store=None) -> None:
        self.db_path = db_path
        self.embedder = embedder
        self.store = store
        self.indexed: list[tuple[str, int]] = []
        self.search_calls: list[dict] = []

    def index_events(self, agent_name: str, events: list[dict]) -> int:
        self.indexed.append((agent_name, len(events)))
        return len(events)

    def get_stats(self) -> dict[str, Any]:
        return {"events_indexed": sum(c for _, c in self.indexed)}

    def search(
        self,
        *,
        query: str,
        mode: str,
        k: int,
        agent: str | None,
    ):
        self.search_calls.append({"query": query, "mode": mode, "k": k, "agent": agent})
        return [
            _Hit("hello world", round_num=1, agent="alice", block_num=0),
            _Hit("general kenobi", round_num=1, agent="alice", block_num=1),
        ]


class _Hit:
    """Minimal result row matching the .content / .score / etc. shape."""

    def __init__(
        self,
        content: str,
        *,
        round_num: int,
        agent: str,
        block_num: int = 0,
    ) -> None:
        self.content = content
        self.round_num = round_num
        self.block_num = block_num
        self.agent = agent
        self.block_type = "text"
        self.score = 0.42
        self.ts = "2025-01-01T00:00:00"
        self.tool_name = ""
        self.channel = ""


# ---------------------------------------------------------------------------
# build_embeddings
# ---------------------------------------------------------------------------


class TestBuildEmbeddings:
    def test_indexes_every_agent(self, tmp_path, monkeypatch):
        kt_path = tmp_path / "sess.kohakutr"
        store = _make_populated_store(kt_path)
        store.close()

        monkeypatch.setattr(ms, "create_embedder", lambda cfg: _StubEmbedder())
        monkeypatch.setattr(ms, "SessionMemory", _FakeMemory)

        out = ms.build_embeddings(
            kt_path, provider="model2vec", model="m1", dimensions=8
        )
        assert out["path"] == str(kt_path)
        assert "alice" in out["agents"]
        assert out["indexed_per_agent"]["alice"]["events"] == 3
        assert out["provider"] == "model2vec"

    def test_handles_agent_with_no_events(self, tmp_path, monkeypatch):
        kt_path = tmp_path / "sess.kohakutr"
        store = SessionStore(kt_path)
        store.init_meta(
            session_id="empty",
            config_type="agent",
            config_path="x",
            pwd=str(tmp_path),
            agents=["ghost"],
        )
        store.close()

        monkeypatch.setattr(ms, "create_embedder", lambda cfg: _StubEmbedder())
        monkeypatch.setattr(ms, "SessionMemory", _FakeMemory)

        out = ms.build_embeddings(kt_path)
        assert out["indexed_per_agent"]["ghost"] == {"events": 0, "blocks": 0}


# ---------------------------------------------------------------------------
# _live_store_for_path
# ---------------------------------------------------------------------------


class TestLiveStoreLookup:
    @pytest.mark.asyncio
    async def test_returns_live_store_when_path_matches(self, tmp_path):
        engine = Terrarium()
        try:
            kt_path = tmp_path / "live.kohakutr"
            store = _make_populated_store(kt_path)
            try:
                creature = await install_fake_creature(engine, "alice")
                creature.agent.session_store = store
                ag, ss = ms._live_store_for_path(engine, kt_path)
                assert ag is creature.agent
                assert ss is store
            finally:
                store.close()
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_match(self, tmp_path):
        engine = Terrarium()
        try:
            await install_fake_creature(engine, "alice")
            ag, ss = ms._live_store_for_path(engine, tmp_path / "nope.kohakutr")
            assert ag is None and ss is None
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_creatures(self, tmp_path):
        engine = Terrarium()
        try:
            ag, ss = ms._live_store_for_path(engine, tmp_path / "x.kohakutr")
            assert ag is None and ss is None
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# _resolve_embed_config
# ---------------------------------------------------------------------------


class TestResolveEmbedConfig:
    def test_uses_saved_embedding_config_in_state(self, tmp_path):
        kt_path = tmp_path / "sess.kohakutr"
        store = _make_populated_store(kt_path)
        try:
            store.state["embedding_config"] = {"provider": "saved", "model": "x"}
            cfg = ms._resolve_embed_config(store, None)
            assert cfg == {"provider": "saved", "model": "x"}
        finally:
            store.close()

    def test_falls_back_to_live_agent_memory_config(self, tmp_path):
        kt_path = tmp_path / "sess.kohakutr"
        store = _make_populated_store(kt_path)
        try:

            class _Agent:
                config = type(
                    "C", (), {"memory": {"embedding": {"provider": "live"}}}
                )()

            cfg = ms._resolve_embed_config(store, _Agent())
            assert cfg == {"provider": "live"}
        finally:
            store.close()

    def test_default_provider_when_no_saved_or_live(self, tmp_path):
        kt_path = tmp_path / "sess.kohakutr"
        store = _make_populated_store(kt_path)
        try:
            cfg = ms._resolve_embed_config(store, None)
            assert cfg == {"provider": "auto"}
        finally:
            store.close()

    def test_ignores_non_dict_saved_config(self, tmp_path):
        kt_path = tmp_path / "sess.kohakutr"
        store = _make_populated_store(kt_path)
        try:
            store.state["embedding_config"] = "not a dict"
            cfg = ms._resolve_embed_config(store, None)
            assert cfg == {"provider": "auto"}
        finally:
            store.close()

    def test_swallows_state_lookup_exception(self, tmp_path):
        """Exercise the ``except (KeyError, Exception)`` branch."""
        kt_path = tmp_path / "sess.kohakutr"
        store = _make_populated_store(kt_path)
        original_state = store.state
        try:

            class _FlakyState:
                def get(self, key, default=None):
                    raise RuntimeError("disk")

                def close(self):
                    pass

            store.state = _FlakyState()
            cfg = ms._resolve_embed_config(store, None)
            assert cfg == {"provider": "auto"}
        finally:
            # Restore real state so close() doesn't blow up later.
            store.state = original_state
            store.close()


# ---------------------------------------------------------------------------
# search_session_memory
# ---------------------------------------------------------------------------


class TestSearchSessionMemory:
    @pytest.mark.asyncio
    async def test_search_with_no_live_store_opens_fresh(self, tmp_path, monkeypatch):
        engine = Terrarium()
        try:
            kt_path = tmp_path / "sess.kohakutr"
            store = _make_populated_store(kt_path)
            store.close()

            monkeypatch.setattr(ms, "create_embedder", lambda cfg: _StubEmbedder())
            monkeypatch.setattr(ms, "SessionMemory", _FakeMemory)

            out = await ms.search_session_memory(
                kt_path, q="kenobi", mode="auto", k=5, agent=None, engine=engine
            )
            assert out["query"] == "kenobi"
            assert out["count"] == 2
            assert out["session_name"] == "sess"
            assert out["results"][0]["content"] == "hello world"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_search_reuses_live_store(self, tmp_path, monkeypatch):
        engine = Terrarium()
        try:
            kt_path = tmp_path / "live.kohakutr"
            store = _make_populated_store(kt_path)
            try:
                creature = await install_fake_creature(engine, "alice")
                creature.agent.session_store = store

                monkeypatch.setattr(ms, "create_embedder", lambda cfg: _StubEmbedder())
                monkeypatch.setattr(ms, "SessionMemory", _FakeMemory)

                out = await ms.search_session_memory(
                    kt_path, q="hello", mode="fts", k=3, agent="alice", engine=engine
                )
                assert out["count"] == 2
                assert out["mode"] == "fts"
            finally:
                store.close()
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_search_swallows_embedder_init_failure(self, tmp_path, monkeypatch):
        engine = Terrarium()
        try:
            kt_path = tmp_path / "sess.kohakutr"
            store = _make_populated_store(kt_path)
            store.close()

            def _boom(cfg):
                raise RuntimeError("model not found")

            monkeypatch.setattr(ms, "create_embedder", _boom)
            monkeypatch.setattr(ms, "SessionMemory", _FakeMemory)

            out = await ms.search_session_memory(
                kt_path, q="x", mode="auto", k=1, agent=None, engine=engine
            )
            # No embedder => still works (fts-only fallback)
            assert out["count"] == 2
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_search_raises_http_exception_on_search_failure(
        self, tmp_path, monkeypatch
    ):
        engine = Terrarium()
        try:
            kt_path = tmp_path / "sess.kohakutr"
            store = _make_populated_store(kt_path)
            store.close()

            class _BoomMemory:
                def __init__(self, *a, **kw):
                    pass

                def index_events(self, *_a):
                    return 0

                def search(self, **_kw):
                    raise RuntimeError("disk full")

            monkeypatch.setattr(ms, "create_embedder", lambda cfg: _StubEmbedder())
            monkeypatch.setattr(ms, "SessionMemory", _BoomMemory)

            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc:
                await ms.search_session_memory(
                    kt_path, q="x", mode="auto", k=1, agent=None, engine=engine
                )
            assert exc.value.status_code == 500
            assert "Memory search failed" in exc.value.detail
        finally:
            await engine.shutdown()
