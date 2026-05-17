"""Push memory_search.py remaining lines (provider build + embedder fallback)."""

from pathlib import Path
from types import SimpleNamespace


from kohakuterrarium.studio.sessions import memory_build as build_mod
from kohakuterrarium.studio.sessions import memory_search as mem_mod

# ── build_embeddings with model/dimensions overrides ────────


class TestBuildEmbeddingsConfigOverrides:
    def test_model_and_dimensions_passed(self, monkeypatch, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s", "agent", "/p", "/w", ["alice"])
        store.close()

        captured = {}

        class _Embed:
            dimensions = 0

            def __init__(self, *a, **kw):
                captured["init"] = True

            def encode(self, texts):
                return []

        monkeypatch.setattr(
            build_mod,
            "create_embedder",
            lambda cfg: captured.update(cfg) or _Embed(),
        )

        class _Mem:
            has_vectors = False

            def __init__(self, *a, **kw):
                pass

            def close(self):
                pass

            def index_events(self, agent, events):
                return 0

            def get_stats(self):
                return {}

            def _set_indexed_count(self, *a, **kw):
                pass

            def _clear_fts(self, *a, **kw):
                pass

        monkeypatch.setattr(build_mod, "SessionMemory", _Mem)
        # build_embeddings is sync and takes model/dimensions.
        mem_mod.build_embeddings(path, provider="custom", model="m1", dimensions=512)
        # Verify the config was constructed with model and dimensions.
        assert captured["provider"] == "custom"
        assert captured["model"] == "m1"
        assert captured["dimensions"] == 512


# ── search_session_memory embedder-failure fallback (line 134-136) ──


class TestSearchSessionMemoryEmbedderFallback:
    async def test_embedder_creation_fails_continues(self, monkeypatch, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s", "agent", "/p", "/w", ["alice"])
        store.append_event("alice", "user_input", {"content": "hi"})
        store.flush()
        store.close()

        monkeypatch.setattr(
            mem_mod, "_live_store_for_path", lambda eng, p: (None, None)
        )

        def _boom(cfg):
            raise RuntimeError("no embedder")

        monkeypatch.setattr(mem_mod, "create_embedder", _boom)

        class _Mem:
            def __init__(self, *a, **kw):
                pass

            def close(self):
                pass

            def index_events(self, *a, **kw):
                return 0

            def search(self, query, mode, k, agent):
                return []

        monkeypatch.setattr(mem_mod, "SessionMemory", _Mem)
        out = await mem_mod.search_session_memory(
            path, q="hi", mode="fts", k=5, agent=None, engine=SimpleNamespace()
        )
        # Search completes despite embedder failure.
        assert out["count"] == 0


# ── _resolve_embed_config exception path ────────────────────


class TestResolveEmbedConfigException:
    def test_state_get_raises(self, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        try:
            store.init_meta("s", "agent", "/p", "/w", ["alice"])

            # Replace state.get with raising fn to hit the except branch.
            def _boom(k):
                raise RuntimeError("state failed")

            store.state.get = _boom
            out = mem_mod._resolve_embed_config(store, None)
            # Falls back to default.
            assert out == {"provider": "auto"}
        finally:
            store.close()


# ── _live_store_for_path — agent has session_store with non-matching path ──


class TestLiveStoreForPathMismatch:
    def test_path_mismatch_returns_none(self):
        agent = SimpleNamespace(session_store=SimpleNamespace(_path="/other"))
        creature = SimpleNamespace(agent=agent)
        engine = SimpleNamespace(list_creatures=lambda: [creature])
        out = mem_mod._live_store_for_path(engine, Path("/different"))
        assert out == (None, None)
