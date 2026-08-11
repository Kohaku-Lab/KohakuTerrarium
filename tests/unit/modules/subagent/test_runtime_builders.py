"""Unit tests for :mod:`kohakuterrarium.modules.subagent.runtime_builders`.

Behavior-first: resolve_llm (re-exported from ``model_resolve``) inherits
the parent on empty/sentinel models, resolves a known selector through the
profile system, keeps the raw ``with_model`` override for bare AND raw
``provider/model`` ids (OpenRouter/LiteLLM), and fails loud only on an
unresolved ``@variations`` selector; build_compact_manager only builds when
compaction is configured and wires the llm/config; build_plugin_manager /
load_and_wrap_plugins handle the empty case.
"""

import logging

import pytest

from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.errors import LLMNotConfiguredError
from kohakuterrarium.modules.subagent import model_resolve
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.subagent.model_resolve import resolve_subagent_llm
from kohakuterrarium.modules.subagent.runtime_builders import (
    build_compact_manager,
    build_plugin_manager,
    load_and_wrap_plugins,
    resolve_llm,
)


class _FakeLLM:
    """LLM stand-in that records with_model calls."""

    def __init__(self, model="parent-model", fail_with_model=False):
        self.model = model
        self._fail = fail_with_model
        self.with_model_calls: list[str] = []

    def with_model(self, name):
        self.with_model_calls.append(name)
        if self._fail:
            raise ValueError(f"unknown model: {name}")
        return _FakeLLM(model=name)


def _no_profiles(monkeypatch):
    """Force profile resolution to miss, so every non-sentinel model
    falls through to the raw/explicit branches."""
    monkeypatch.setattr(model_resolve, "get_profile", lambda *a, **k: None)


class TestResolveLLM:
    def test_resolve_llm_is_the_shared_resolver(self):
        # The manager imports ``resolve_llm``; it must be the same
        # canonical resolver so the two code paths never diverge.
        assert resolve_llm is resolve_subagent_llm

    def test_empty_model_inherits_parent(self, monkeypatch):
        _no_profiles(monkeypatch)
        parent = _FakeLLM()
        cfg = SubAgentConfig(name="x", model=None)
        assert resolve_llm(parent, cfg) is parent
        # No model switch attempted.
        assert parent.with_model_calls == []

    def test_sentinel_model_inherits_parent(self, monkeypatch):
        _no_profiles(monkeypatch)
        parent = _FakeLLM()
        for sentinel in ("subagent-default", "inherit", "parent", "default"):
            cfg = SubAgentConfig(name="x", model=sentinel)
            assert resolve_llm(parent, cfg) is parent
        assert parent.with_model_calls == []

    def test_whitespace_only_model_inherits_parent(self, monkeypatch):
        _no_profiles(monkeypatch)
        parent = _FakeLLM()
        cfg = SubAgentConfig(name="x", model="   ")
        assert resolve_llm(parent, cfg) is parent

    def test_known_selector_resolves_through_profile_system(self, monkeypatch):
        # A selector that resolves is built from the resolved profile
        # object (single resolve), NOT the parent's raw with_model.
        resolved_provider = object()
        profile_obj = object()
        seen: dict[str, object] = {}

        def fake_get_profile(name, *args, **kwargs):
            seen["get"] = name
            return profile_obj

        def fake_create(profile):
            seen["build"] = profile
            return resolved_provider

        monkeypatch.setattr(model_resolve, "get_profile", fake_get_profile)
        monkeypatch.setattr(model_resolve, "_create_from_profile", fake_create)

        parent = _FakeLLM()
        cfg = SubAgentConfig(name="x", model="anthropic/claude-opus-4.8")
        out = resolve_llm(parent, cfg)
        assert out is resolved_provider
        assert seen == {"get": "anthropic/claude-opus-4.8", "build": profile_obj}
        # The raw same-provider path must NOT run for a resolved profile.
        assert parent.with_model_calls == []

    def test_raw_bare_model_uses_with_model(self, monkeypatch):
        # A bare model id that is not a known profile falls back to the
        # legacy same-provider raw override.
        _no_profiles(monkeypatch)
        parent = _FakeLLM()
        cfg = SubAgentConfig(name="x", model="my-raw-model-xyz")
        resolved = resolve_llm(parent, cfg)
        assert resolved is not parent
        assert resolved.model == "my-raw-model-xyz"
        assert parent.with_model_calls == ["my-raw-model-xyz"]

    def test_slash_raw_model_id_falls_back_to_with_model(self, monkeypatch):
        # A raw ``provider/model`` id (every OpenRouter / LiteLLM id) is NOT
        # a fail-loud selector — only ``@variations`` selectors are. It must
        # reach ``with_model`` so the sub-agent actually runs.
        _no_profiles(monkeypatch)
        parent = _FakeLLM()
        cfg = SubAgentConfig(name="x", model="anthropic/claude-haiku-4.5")
        resolved = resolve_llm(parent, cfg)
        assert resolved is not parent
        assert resolved.model == "anthropic/claude-haiku-4.5"
        assert parent.with_model_calls == ["anthropic/claude-haiku-4.5"]

    def test_unresolved_variation_selector_fails_loud(self, monkeypatch):
        # An unresolved ``@variations`` selector unambiguously names a
        # profile → raise, never silently inherit the parent.
        _no_profiles(monkeypatch)
        parent = _FakeLLM()
        cfg = SubAgentConfig(name="x", model="opus@reasoning=high")
        with pytest.raises(LLMNotConfiguredError, match="opus@reasoning=high"):
            resolve_llm(parent, cfg)
        # No silent inherit: the raw path is never reached.
        assert parent.with_model_calls == []

    def test_raw_slash_id_does_not_log_profile_not_found(self, caplog):
        # A legit raw ``provider/model`` id must NOT emit the misleading
        # "LLM profile not found" warning (single, warning-free resolve).
        parent = _FakeLLM()
        cfg = SubAgentConfig(name="x", model="ghost-provider-xyz/ghost-model-123")
        with caplog.at_level(logging.WARNING):
            out = resolve_llm(parent, cfg)
        assert out.model == "ghost-provider-xyz/ghost-model-123"
        assert "profile not found" not in caplog.text.lower()

    def test_failing_with_model_falls_back_to_parent(self, monkeypatch):
        # Contract: an unknown BARE model id must not crash the sub-agent —
        # the legacy raw fallback logs and inherits the parent LLM.
        _no_profiles(monkeypatch)
        parent = _FakeLLM(fail_with_model=True)
        cfg = SubAgentConfig(name="x", model="bogus-model")
        assert resolve_llm(parent, cfg) is parent


class TestBuildCompactManager:
    def test_no_compact_config_returns_none(self):
        cfg = SubAgentConfig(name="x", compact=None)
        assert build_compact_manager(cfg, _FakeLLM()) is None

    def test_compact_config_builds_and_wires_manager(self):
        cfg = SubAgentConfig(
            name="x",
            compact={"threshold": 0.8, "target": 0.3, "keep_recent_turns": 6},
        )
        llm = _FakeLLM()
        cm = build_compact_manager(cfg, llm)
        assert cm is not None
        # The configured values landed on the CompactConfig.
        assert cm.config.threshold == 0.8
        assert cm.config.target == 0.3
        assert cm.config.keep_recent_turns == 6
        # The manager is wired to this llm and the sub-agent's name.
        assert cm._llm is llm
        assert cm._agent_name == "x"

    def test_cooldown_alias_is_honoured(self):
        # The config accepts either "cooldown" or "cooldown_seconds".
        cfg = SubAgentConfig(name="x", compact={"cooldown": 5.0})
        cm = build_compact_manager(cfg, _FakeLLM())
        assert cm.config.cooldown_seconds == 5.0


class TestBuildPluginManager:
    def test_inline_plugin_entries_are_registered(self):
        # An inline plugins:[] entry must surface in the built manager
        # alongside the framework's baseline plugin pack.
        cfg = SubAgentConfig(
            name="x",
            plugins=[
                {
                    "name": "budget",
                    "options": {"max_turns": 3},
                }
            ],
            default_plugins=[],
        )
        loader = ModuleLoader(agent_path=None)
        pm = build_plugin_manager(cfg, loader, [])
        names = [getattr(p, "name", "?") for p in pm._plugins]
        assert "budget" in names


class TestLoadAndWrapPlugins:
    async def test_falsy_plugin_manager_is_a_noop(self):
        # A None / empty plugin manager → load_and_wrap_plugins returns
        # immediately without touching the sub-agent.
        await load_and_wrap_plugins(None, object(), _FakeLLM(), None)

    async def test_empty_manager_is_a_noop(self):
        from kohakuterrarium.modules.plugin.manager import PluginManager

        empty = PluginManager()
        # bool(empty) is False → early return, no on_load attempts.
        await load_and_wrap_plugins(empty, object(), _FakeLLM(), None)
