"""Unit tests for :mod:`kohakuterrarium.modules.subagent.runtime_builders`.

Behavior-first: resolve_llm honours sentinel "inherit parent" values and
falls back to the parent on a failing with_model; build_compact_manager
only builds when compaction is configured and wires the llm/config;
build_plugin_manager / load_and_wrap_plugins handle the empty case.
"""

from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.modules.subagent.config import SubAgentConfig
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


class TestResolveLLM:
    def test_empty_model_inherits_parent(self):
        parent = _FakeLLM()
        cfg = SubAgentConfig(name="x", model=None)
        assert resolve_llm(parent, cfg) is parent
        # No model switch attempted.
        assert parent.with_model_calls == []

    def test_sentinel_model_inherits_parent(self):
        parent = _FakeLLM()
        for sentinel in ("subagent-default", "inherit", "parent", "default"):
            cfg = SubAgentConfig(name="x", model=sentinel)
            assert resolve_llm(parent, cfg) is parent
        assert parent.with_model_calls == []

    def test_real_model_switches_via_with_model(self):
        parent = _FakeLLM()
        cfg = SubAgentConfig(name="x", model="claude-opus-4.6")
        resolved = resolve_llm(parent, cfg)
        assert resolved is not parent
        assert resolved.model == "claude-opus-4.6"
        assert parent.with_model_calls == ["claude-opus-4.6"]

    def test_failing_with_model_falls_back_to_parent(self):
        # Contract: an unknown model id must not crash the sub-agent —
        # resolve_llm logs and inherits the parent LLM.
        parent = _FakeLLM(fail_with_model=True)
        cfg = SubAgentConfig(name="x", model="bogus-model")
        assert resolve_llm(parent, cfg) is parent

    def test_whitespace_only_model_inherits_parent(self):
        parent = _FakeLLM()
        cfg = SubAgentConfig(name="x", model="   ")
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
