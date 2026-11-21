"""A shared SessionStore has one effective embedding configuration."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.agent import session as session_module
from kohakuterrarium.core.agent.model import AgentModelMixin
from kohakuterrarium.core.agent.session import AgentSessionMixin
from kohakuterrarium.session.store import SessionStore


class _SessionTestAgent(AgentSessionMixin, AgentModelMixin):
    """Compose the real model/session contracts without booting a Creature."""


def _publish(agent_name, store, embedding):
    agent = _SessionTestAgent()
    agent.session_store = store
    agent.config = SimpleNamespace(
        name=agent_name,
        llm_profile="",
        memory=({"embedding": embedding} if embedding is not None else {}),
    )
    agent._llm_selector = ""
    agent.llm = SimpleNamespace(model="test")
    agent._init_model_state(agent.llm)
    agent.llm_identifier = lambda: ""
    agent.output_router = SimpleNamespace(notify_activity=lambda *a, **kw: None)
    agent._publish_session_info()


def test_shared_creature_session_rejects_conflicting_embedding_models(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(session_module, "context_window", lambda agent: (4096, 3000))
    store = SessionStore(tmp_path / "shared.kohakutr")
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        chosen = {"provider": "api", "model": "embed-A", "dimensions": 4}
        _publish("alice", store, chosen)
        _publish("bob", store, {**chosen, "base_url": None})
        assert store.state["embedding_config"] == chosen

        with pytest.raises(ValueError, match="session.*embedding|embedding.*session"):
            _publish(
                "bob",
                store,
                {"provider": "api", "model": "embed-B", "api_key": "SECRET"},
            )
        assert store.state["embedding_config"] == chosen
    finally:
        store.close(update_status=False)


def test_unconfigured_creature_does_not_overwrite_session_embedding(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(session_module, "context_window", lambda agent: (4096, 3000))
    store = SessionStore(tmp_path / "missing.kohakutr")
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        _publish("alice", store, {"provider": "model2vec", "model": "base"})
        _publish("bob", store, None)
        assert store.state["embedding_config"] == {
            "provider": "model2vec",
            "model": "base",
        }
    finally:
        store.close(update_status=False)


def test_persisted_session_model_is_not_silently_overwritten_on_resume(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(session_module, "context_window", lambda agent: (4096, 3000))
    path = tmp_path / "resume.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        _publish("alice", store, {"provider": "api", "model": "embed-A"})
    finally:
        store.close(update_status=False)

    reopened = SessionStore(path)
    try:
        with pytest.raises(ValueError, match="session.*embedding|embedding.*session"):
            _publish("alice", reopened, {"provider": "api", "model": "embed-B"})
        assert reopened.state["embedding_config"]["model"] == "embed-A"
    finally:
        reopened.close(update_status=False)


def test_concurrent_creatures_cannot_race_to_replace_one_session_model(tmp_path):
    store = SessionStore(tmp_path / "concurrent.kohakutr")
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        barrier = Barrier(2)

        def claim(name, model):
            barrier.wait(timeout=5)
            try:
                store.claim_embedding_config(name, {"provider": "api", "model": model})
            except ValueError:
                return "rejected"
            return "accepted"

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(claim, "alice", "embed-A")
            b = pool.submit(claim, "bob", "embed-B")
            outcomes = {a.result(timeout=10), b.result(timeout=10)}
        assert outcomes == {"accepted", "rejected"}
        assert store.state["embedding_config"]["model"] in {"embed-A", "embed-B"}
    finally:
        store.close(update_status=False)


@pytest.mark.parametrize(
    "requested, built",
    [
        (
            {"provider": "model2vec"},
            {
                "provider": "model2vec",
                "model": "sentence-transformers/static-similarity-mrl-multilingual-v1",
            },
        ),
        (
            {"provider": "model2vec", "model": "@base"},
            {"provider": "model2vec", "model": "minishlab/potion-base-8M"},
        ),
    ],
)
def test_same_creature_can_resume_after_offline_effective_model_persist(
    tmp_path, monkeypatch, requested, built
):
    monkeypatch.setattr(session_module, "context_window", lambda agent: (4096, 3000))
    path = tmp_path / "effective-resume.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        store.state["embedding_config"] = built
    finally:
        store.close(update_status=False)
    reopened = SessionStore(path)
    try:
        _publish("alice", reopened, requested)
        assert reopened.state["embedding_config"] == built
        with pytest.raises(ValueError, match="conflicting"):
            _publish(
                "alice",
                reopened,
                {"provider": "model2vec", "model": "not-the-same-model"},
            )
    finally:
        reopened.close(update_status=False)


def test_api_embedding_credentials_rotate_without_model_conflict(tmp_path, monkeypatch):
    from kohakuterrarium.builtins.tools.search_memory import SearchMemoryTool
    from kohakuterrarium.studio.sessions.memory.search import _resolve_embed_config
    from kohakuterrarium.session.memory.embedding import create_embedder

    monkeypatch.setattr(session_module, "context_window", lambda agent: (4096, 3000))
    path = tmp_path / "rotate.kohakutr"
    model = {
        "provider": "api",
        "model": "embed-A",
        "base_url": "https://embeddings.example/v1",
        "dimensions": 128,
    }
    with closing(SessionStore(path)) as store:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        _publish("alice", store, {**model, "api_key": "expired-credential"})
        assert "api_key" not in store.state["embedding_config"]
        first = SearchMemoryTool._load_embed_config(None, store, None)
        assert first["api_key"] == "expired-credential"

    with closing(SessionStore(path)) as reopened:
        current = {**model, "api_key": "rotated-credential"}
        _publish("alice", reopened, current)
        assert reopened.state["embedding_config"] == model
        for config in (
            SearchMemoryTool._load_embed_config(None, reopened, None),
            _resolve_embed_config(reopened, None),
        ):
            assert config["api_key"] == "rotated-credential"
            embedder = create_embedder(config)
            try:
                assert (
                    embedder._client.headers["Authorization"]
                    == "Bearer rotated-credential"
                )
            finally:
                embedder._client.close()


def test_legacy_persisted_api_secret_is_not_an_auth_fallback(tmp_path, monkeypatch):
    from kohakuterrarium.session.memory.embedding import create_embedder
    from kohakuterrarium.studio.sessions.memory.search import _resolve_embed_config

    path = tmp_path / "legacy-key.kohakutr"
    with closing(SessionStore(path)) as store:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        store.state["embedding_config"] = {
            "provider": "api",
            "model": "same",
            "api_key": "legacy-expired-secret",
        }
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with closing(SessionStore(path)) as reopened:
        runtime = _resolve_embed_config(reopened, None)
        assert "api_key" not in runtime
        with pytest.raises(ValueError, match="api_key"):
            create_embedder(runtime)

        # Compatible resume scrubs secrets from older SessionStore state.
        reopened.claim_embedding_config(
            "alice", {"provider": "api", "model": "same", "api_key_env": "FRESH_KEY"}
        )
        assert reopened.state["embedding_config"] == {
            "provider": "api",
            "model": "same",
            "api_key_env": "FRESH_KEY",
        }
        monkeypatch.setenv("FRESH_KEY", "new-from-environment")
        active = create_embedder(_resolve_embed_config(reopened, None))
        try:
            assert (
                active._client.headers["Authorization"] == "Bearer new-from-environment"
            )
        finally:
            active._client.close()


@pytest.mark.parametrize(
    "changed",
    [
        {"model": "another"},
        {"base_url": "https://other.example/v1"},
        {"dimensions": 256},
    ],
)
def test_credential_changes_are_distinct_from_embedding_space_changes(
    tmp_path, monkeypatch, changed
):
    monkeypatch.setattr(session_module, "context_window", lambda agent: (4096, 3000))
    with closing(SessionStore(tmp_path / "identity.kohakutr")) as store:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        base = {
            "provider": "api",
            "model": "m",
            "dimensions": 128,
            "base_url": "https://embeddings.example/v1",
            "api_key_env": "KEY_A",
        }
        _publish("alice", store, base)
        _publish("bob", store, {**base, "api_key_env": "KEY_B"})
        assert store.state["embedding_config"]["api_key_env"] == "KEY_B"
        with pytest.raises(ValueError, match="conflicting"):
            _publish("bob", store, {**base, **changed, "api_key": "secret-value"})
        assert store.state["embedding_config"]["model"] == "m"
        assert "api_key" not in store.state["embedding_config"]


def test_embedding_space_identity_is_only_supported_generation_inputs():
    from kohakuterrarium.session.memory.embedding_spec import embedding_space_identity

    base = {
        "provider": "api",
        "model": "m",
        "dimensions": 128,
        "base_url": "https://embeddings.example/v1",
        "api_key": "credential-A",
        "api_key_env": "KEY_A",
        "device": "cpu",
        "unknown_provider_option": "not-passed-to-api",
    }
    altered = {
        **base,
        "api_key": "credential-B",
        "api_key_env": "KEY_B",
        "device": "cuda",
        "unknown_provider_option": "different",
    }
    assert embedding_space_identity(base) == embedding_space_identity(altered)
    assert embedding_space_identity(base) != embedding_space_identity(
        {
            **base,
            "model": "changed",
        }
    )
    assert embedding_space_identity(base) != embedding_space_identity(
        {
            **base,
            "dimensions": 256,
        }
    )


def test_unknown_options_are_not_persisted_as_model_contract(tmp_path):
    from kohakuterrarium.session.memory.embedding_spec import persisted_embedding_config

    config = {
        "provider": "api",
        "model": "m",
        "api_key": "never-persist",
        "api_key_env": "CURRENT_KEY",
        "unrecognized_secret": "should-not-persist",
        "dimensions": 256,
    }
    assert persisted_embedding_config(config) == {
        "provider": "api",
        "model": "m",
        "api_key_env": "CURRENT_KEY",
        "dimensions": 256,
    }


def test_saved_and_explicit_default_endpoint_are_equivalent():
    from kohakuterrarium.session.memory.embedding_spec import embedding_space_identity

    saved = embedding_space_identity({"provider": "api", "model": "m"})
    explicit = embedding_space_identity(
        {
            "provider": "api",
            "model": "m",
            "base_url": "https://api.openai.com/v1",
        }
    )
    assert saved == explicit


def test_auto_provider_does_not_ignore_explicit_model(tmp_path):
    from kohakuterrarium.session.memory.embedding_spec import embedding_space_identity

    assert embedding_space_identity({"provider": "auto", "model": "model-A"}) != (
        embedding_space_identity({"provider": "auto", "model": "model-B"})
    )
    store = SessionStore(tmp_path / "auto-model.kohakutr")
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        store.claim_embedding_config("alice", {"provider": "auto", "model": "model-A"})
        with pytest.raises(ValueError, match="conflicting"):
            store.claim_embedding_config(
                "bob", {"provider": "auto", "model": "model-B"}
            )
    finally:
        store.close(update_status=False)


def test_rotated_api_key_reuses_published_vectors_without_reembedding(
    tmp_path, monkeypatch
):
    import numpy as np

    from kohakuterrarium.session.memory.embedding import (
        APIEmbedder,
        create_embedder,
        resolve_session_embedding_config,
    )
    from kohakuterrarium.session.memory.index import SessionMemory

    monkeypatch.setattr(session_module, "context_window", lambda agent: (4096, 3000))
    monkeypatch.setattr(
        APIEmbedder,
        "encode",
        lambda self, texts: np.ones((len(texts), 3), dtype=np.float32),
    )
    path = tmp_path / "reuse-after-credential-rotation.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        store.append_event(
            "alice",
            "user_input",
            {"content": "needle"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        cfg = {"provider": "api", "model": "same", "dimensions": 3}
        _publish("alice", store, {**cfg, "api_key": "old"})
        first_embedder = create_embedder(resolve_session_embedding_config(store))
        try:
            with closing(
                SessionMemory(path, first_embedder, index_all_branches=True)
            ) as first:
                first.index_events("alice", store.get_memory_events("alice"))
                original = dict(first._manifest())
                assert original["vector"] == "ready"
        finally:
            first_embedder._client.close()
    finally:
        store.close(update_status=False)

    reopened = SessionStore(path)
    try:
        _publish("alice", reopened, {**cfg, "api_key": "rotated"})
        next_embedder = create_embedder(resolve_session_embedding_config(reopened))
        try:
            assert next_embedder._client.headers["Authorization"] == "Bearer rotated"
            with closing(
                SessionMemory(path, next_embedder, index_all_branches=True)
            ) as next_index:
                assert next_index._manifest() == original
                assert [
                    hit.content
                    for hit in next_index.search(
                        "needle", mode="semantic", agent="alice"
                    )
                ] == ["needle"]
                assert (
                    next_index._manifest()["vector_generation"]
                    == original["vector_generation"]
                )
        finally:
            next_embedder._client.close()
    finally:
        reopened.close(update_status=False)
