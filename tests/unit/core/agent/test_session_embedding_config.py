"""A shared SessionStore has one effective embedding configuration."""

from concurrent.futures import ThreadPoolExecutor
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
