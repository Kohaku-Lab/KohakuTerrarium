"""Unit tests for :mod:`kohakuterrarium.core.agent_model`."""

import types

import pytest

from kohakuterrarium.core import agent_model as am
from kohakuterrarium.core.agent_compact import AgentCompactMixin
from kohakuterrarium.core.agent_model import AgentModelMixin


class _Router:
    def __init__(self):
        self.calls: list[tuple] = []

    def notify_activity(self, kind, msg, metadata=None):
        self.calls.append((kind, msg, metadata))


def _build_agent(
    *,
    profile=None,
    new_llm=None,
    has_compact=True,
    compact_max=0,
    llm_max=10000,
    identifier="openai/gpt-4",
    llm_profile="",
    llm_model="",
):
    """Compose a duck-typed agent that satisfies AgentModelMixin's surface."""

    class _Agent(AgentModelMixin, AgentCompactMixin):
        pass

    a = _Agent()
    a.llm = types.SimpleNamespace(model="prev", _profile_max_context=llm_max)
    a.controller = types.SimpleNamespace(llm=a.llm)
    # Sub-agent manager — task + interactive sub-agents resolve their LLM
    # from its ``llm`` attribute at spawn time.
    a.subagent_manager = types.SimpleNamespace(llm=a.llm)
    if has_compact:
        a.compact_manager = types.SimpleNamespace(
            _llm=a.llm,
            config=types.SimpleNamespace(
                threshold=0.8,
                max_tokens=8000,
            ),
        )
    else:
        a.compact_manager = None
    a.output_router = _Router()
    a.config = types.SimpleNamespace(
        name="alice",
        llm_profile=llm_profile,
        model=llm_model,
        provider="",
        variation_selections={},
    )
    a._llm_override = None
    a._llm_identifier = ""
    a._session_id = "sess"
    a._new_llm = new_llm
    return a


# ── switch_model ─────────────────────────────────────────────────


class TestSwitchModel:
    def test_unknown_profile_raises(self, monkeypatch):
        monkeypatch.setattr(am, "resolve_controller_llm", lambda *a, **k: None)
        a = _build_agent()
        with pytest.raises(ValueError, match="not found"):
            a.switch_model("ghost")

    def test_swaps_llm_and_emits_session_info(self, monkeypatch):
        new_llm = types.SimpleNamespace(model="gpt-5", _profile_max_context=20000)
        monkeypatch.setattr(am, "resolve_controller_llm", lambda *a, **k: object())
        monkeypatch.setattr(am, "create_llm_from_profile_name", lambda n: new_llm)
        monkeypatch.setattr(am, "profile_to_identifier", lambda p: "openai/gpt-5")

        # Stub compact LLM construction so we don't go into bootstrap.
        captured = []

        def fake_build(self, cfg):
            captured.append(cfg)
            return new_llm  # share

        monkeypatch.setattr(AgentCompactMixin, "_build_compact_llm", fake_build)

        a = _build_agent()
        out = a.switch_model("openai/gpt-5")
        assert out == "openai/gpt-5"
        # LLM swapped.
        assert a.llm is new_llm
        assert a.controller.llm is new_llm
        assert a._llm_identifier == "openai/gpt-5"
        assert a._llm_override == "openai/gpt-5"
        # The swap MUST propagate to the sub-agent manager — sub-agents
        # resolve their LLM from ``subagent_manager.llm`` at spawn time,
        # so a sub-agent dispatched after a ``/model`` switch (or the
        # frontend model-selection modal, which routes here too) would
        # otherwise still run on the model the agent booted with.
        assert a.subagent_manager.llm is new_llm
        # Compact manager rebuilt + max_tokens updated.
        assert a.compact_manager._llm is new_llm
        assert a.compact_manager.config.max_tokens == 20000
        # session_info activity emitted with compact_threshold = max * threshold.
        kind, msg, meta = a.output_router.calls[0]
        assert kind == "session_info"
        assert meta["llm_name"] == "openai/gpt-5"
        assert meta["max_context"] == 20000
        assert meta["compact_threshold"] == 16000  # 20000 * 0.8

    def test_compact_manager_optional(self, monkeypatch):
        new_llm = types.SimpleNamespace(model="x", _profile_max_context=0)
        monkeypatch.setattr(am, "resolve_controller_llm", lambda *a, **k: object())
        monkeypatch.setattr(am, "create_llm_from_profile_name", lambda n: new_llm)
        monkeypatch.setattr(am, "profile_to_identifier", lambda p: "x/y")
        a = _build_agent(has_compact=False)
        out = a.switch_model("x/y")
        assert out == "x/y"
        # No notify_activity crash even without a compact manager.
        assert a.output_router.calls

    def test_compact_uses_distinct_llm(self, monkeypatch):
        new_llm = types.SimpleNamespace(model="m", _profile_max_context=4000)
        distinct_compact = types.SimpleNamespace(
            model="compact-m", _profile_max_context=8000
        )
        monkeypatch.setattr(am, "resolve_controller_llm", lambda *a, **k: object())
        monkeypatch.setattr(am, "create_llm_from_profile_name", lambda n: new_llm)
        monkeypatch.setattr(am, "profile_to_identifier", lambda p: "id")
        monkeypatch.setattr(
            AgentCompactMixin,
            "_build_compact_llm",
            lambda self, cfg: distinct_compact,
        )
        a = _build_agent()
        a.switch_model("id")
        # max_tokens picked from the distinct compact provider's profile.
        assert a.compact_manager.config.max_tokens == 8000


# ── llm_identifier ───────────────────────────────────────────────


class TestLLMIdentifier:
    def test_cached_returned_directly(self):
        a = _build_agent()
        a._llm_identifier = "cached/id"
        assert a.llm_identifier() == "cached/id"

    def test_resolved_via_profile_when_empty(self, monkeypatch):
        monkeypatch.setattr(am, "resolve_controller_llm", lambda *a, **k: object())
        monkeypatch.setattr(am, "profile_to_identifier", lambda p: "resolved/id")
        a = _build_agent(llm_profile="x")
        assert a.llm_identifier() == "resolved/id"
        # Cached now.
        assert a._llm_identifier == "resolved/id"

    def test_falls_back_to_llm_model_when_unresolvable(self, monkeypatch):
        monkeypatch.setattr(am, "resolve_controller_llm", lambda *a, **k: None)
        a = _build_agent()
        a.llm.model = "raw-name"
        assert a.llm_identifier() == "raw-name"

    def test_uses_llm_override_when_present(self, monkeypatch):
        captured = {}

        def fake_resolve(data, **k):
            captured["data"] = data
            return None

        monkeypatch.setattr(am, "resolve_controller_llm", fake_resolve)
        a = _build_agent()
        a._llm_override = "override/profile"
        a.llm.model = "fallback"
        a.llm_identifier()
        assert captured["data"]["llm"] == "override/profile"
