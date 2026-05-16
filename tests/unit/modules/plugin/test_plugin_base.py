"""Unit tests for :mod:`kohakuterrarium.modules.plugin.base`.

Behavior-first: PluginContext accessors degrade gracefully without a
host agent, helpers no-op when unbound, plugin-scoped state is namespaced
into the session store, declarative ``applies_to`` gating works, and
option set/get round-trips through validation.
"""

from pathlib import Path

import pytest

from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)
from kohakuterrarium.modules.plugin.option_validation import PluginOptionError


class _FakeStore:
    """Minimal session store stand-in with a dict-backed ``state``."""

    def __init__(self):
        self.state: dict[str, object] = {}


class _FakeController:
    def __init__(self):
        self.pushed: list[object] = []

    def push_event_sync(self, event):
        self.pushed.append(event)


class _FakeAgent:
    def __init__(self, **attrs):
        for k, v in attrs.items():
            setattr(self, k, v)


# ── PluginContext: accessors without a host agent ──────────────────


class TestPluginContextNoAgent:
    def test_accessors_return_none_when_unbound(self):
        ctx = PluginContext(agent_name="a")
        assert ctx.host_agent is None
        assert ctx.session_store is None
        assert ctx.session_memory is None
        assert ctx.registry is None
        assert ctx.scratchpad is None
        assert ctx.compact_manager is None
        assert ctx.controller is None
        assert ctx.subagent_manager is None

    def test_working_dir_defaults_to_cwd(self):
        ctx = PluginContext(agent_name="a")
        assert ctx.working_dir == Path.cwd()

    def test_explicit_working_dir_retained(self, tmp_path):
        ctx = PluginContext(agent_name="a", working_dir=tmp_path)
        assert ctx.working_dir == tmp_path

    def test_switch_model_noop_returns_empty_string(self):
        ctx = PluginContext(agent_name="a")
        assert ctx.switch_model("gpt-5") == ""

    def test_inject_event_noop_without_agent(self):
        ctx = PluginContext(agent_name="a")
        # Must not raise even though there's no agent/controller.
        ctx.inject_event(object())

    def test_inject_message_before_llm_noop_without_controller(self):
        ctx = PluginContext(agent_name="a")
        ctx.inject_message_before_llm("user", "hi")  # must not raise

    def test_get_state_returns_none_without_store(self):
        ctx = PluginContext(agent_name="a", _plugin_name="p")
        assert ctx.get_state("key") is None

    def test_set_state_noop_without_store(self):
        ctx = PluginContext(agent_name="a", _plugin_name="p")
        ctx.set_state("key", "value")  # must not raise

    def test_repr_includes_identity(self):
        ctx = PluginContext(agent_name="swe", session_id="s1", _plugin_name="bud")
        text = repr(ctx)
        assert "swe" in text and "s1" in text and "bud" in text


# ── PluginContext: accessors WITH a host agent ─────────────────────


class TestPluginContextWithAgent:
    def test_accessors_proxy_agent_attributes(self):
        store = _FakeStore()
        agent = _FakeAgent(
            session_store=store,
            session_memory="mem",
            registry="reg",
            scratchpad="pad",
            compact_manager="cm",
            controller="ctrl",
            subagent_manager="sam",
        )
        ctx = PluginContext(agent_name="a", _host_agent=agent)
        assert ctx.host_agent is agent
        assert ctx.session_store is store
        assert ctx.session_memory == "mem"
        assert ctx.registry == "reg"
        assert ctx.scratchpad == "pad"
        assert ctx.compact_manager == "cm"
        assert ctx.controller == "ctrl"
        assert ctx.subagent_manager == "sam"

    def test_switch_model_delegates_to_agent(self):
        class _ModelAgent:
            def switch_model(self, name):
                return f"resolved:{name}"

        ctx = PluginContext(agent_name="a", _host_agent=_ModelAgent())
        assert ctx.switch_model("opus") == "resolved:opus"

    def test_inject_event_pushes_into_controller_queue(self):
        ctrl = _FakeController()
        agent = _FakeAgent(controller=ctrl)
        ctx = PluginContext(agent_name="a", _host_agent=agent)
        event = object()
        ctx.inject_event(event)
        assert ctrl.pushed == [event]

    def test_inject_message_before_llm_appends_to_pending(self):
        # First injection lazily creates the queue; second appends.
        class _Ctrl:
            pass

        ctrl = _Ctrl()
        agent = _FakeAgent(controller=ctrl)
        ctx = PluginContext(agent_name="a", _host_agent=agent)
        ctx.inject_message_before_llm("user", "first")
        ctx.inject_message_before_llm("system", "second")
        assert ctrl._pending_injections == [
            {"role": "user", "content": "first"},
            {"role": "system", "content": "second"},
        ]

    def test_plugin_scoped_state_is_namespaced(self):
        # Contract: get/set_state writes under plugin:<name>:<key>.
        store = _FakeStore()
        agent = _FakeAgent(session_store=store)
        ctx = PluginContext(agent_name="a", _host_agent=agent, _plugin_name="budget")
        ctx.set_state("turns", 7)
        assert store.state == {"plugin:budget:turns": 7}
        assert ctx.get_state("turns") == 7
        # A different plugin name does NOT see it.
        other = PluginContext(
            agent_name="a", _host_agent=agent, _plugin_name="permgate"
        )
        assert other.get_state("turns") is None

    def test_spawn_child_agent_delegates_to_helper(self):
        calls = []

        def _helper(ctx, config, role):
            calls.append((ctx, config, role))
            return "child-agent"

        ctx = PluginContext(agent_name="a", _spawn_child_agent_helper=_helper)
        result = ctx.spawn_child_agent({"name": "c"}, role="worker")
        assert result == "child-agent"
        assert calls[0][1] == {"name": "c"}
        assert calls[0][2] == "worker"


# ── PluginContext: async emit helpers ──────────────────────────────


class TestPluginContextEmit:
    async def test_emit_noop_without_agent(self):
        ctx = PluginContext(agent_name="a")
        await ctx.emit(object())  # must not raise

    async def test_emit_noop_when_agent_has_no_router(self):
        agent = _FakeAgent()  # no output_router attr
        ctx = PluginContext(agent_name="a", _host_agent=agent)
        await ctx.emit(object())  # must not raise

    async def test_emit_forwards_to_router(self):
        class _Router:
            def __init__(self):
                self.emitted = []

            async def emit(self, event):
                self.emitted.append(event)

        router = _Router()
        agent = _FakeAgent(output_router=router)
        ctx = PluginContext(agent_name="a", _host_agent=agent)
        ev = object()
        await ctx.emit(ev)
        assert router.emitted == [ev]

    async def test_emit_and_wait_raises_without_agent(self):
        ctx = PluginContext(agent_name="a")
        with pytest.raises(RuntimeError, match="not attached"):
            await ctx.emit_and_wait(object())

    async def test_emit_and_wait_raises_without_router(self):
        agent = _FakeAgent()
        ctx = PluginContext(agent_name="a", _host_agent=agent)
        with pytest.raises(RuntimeError, match="no output_router"):
            await ctx.emit_and_wait(object())

    async def test_emit_and_wait_forwards_and_returns_reply(self):
        class _Router:
            async def emit_and_wait(self, event, timeout_s=None):
                return ("reply", timeout_s)

        agent = _FakeAgent(output_router=_Router())
        ctx = PluginContext(agent_name="a", _host_agent=agent)
        result = await ctx.emit_and_wait(object(), timeout_s=5.0)
        assert result == ("reply", 5.0)


# ── BasePlugin: gating ─────────────────────────────────────────────


class _AgentScopedPlugin(BasePlugin):
    name = "scoped"
    applies_to = {"agent_names": ["swe", "ops"]}


class _ModelScopedPlugin(BasePlugin):
    name = "model_scoped"
    applies_to = {"model_patterns": ["^codex/", "opus"]}


class _BadRegexPlugin(BasePlugin):
    name = "bad_regex"
    applies_to = {"model_patterns": ["(unclosed"]}


class TestBasePluginGating:
    def test_no_filter_applies_everywhere(self):
        plugin = BasePlugin()
        assert plugin.should_apply(PluginContext(agent_name="anything")) is True

    def test_agent_names_filter_matches(self):
        plugin = _AgentScopedPlugin()
        assert plugin.should_apply(PluginContext(agent_name="swe")) is True
        assert plugin.should_apply(PluginContext(agent_name="other")) is False

    def test_model_patterns_filter_matches(self):
        plugin = _ModelScopedPlugin()
        assert (
            plugin.should_apply(PluginContext(agent_name="a", model="codex/x")) is True
        )
        assert (
            plugin.should_apply(PluginContext(agent_name="a", model="claude-opus-4"))
            is True
        )
        assert (
            plugin.should_apply(PluginContext(agent_name="a", model="gpt-4")) is False
        )

    def test_invalid_regex_is_skipped_not_fatal(self):
        # A bad model_patterns regex is dropped at __init__ with a warning;
        # the plugin still constructs and (with no valid patterns) applies.
        plugin = _BadRegexPlugin()
        assert plugin._model_pattern_res == []
        assert plugin.should_apply(PluginContext(agent_name="a", model="x")) is True


# ── BasePlugin: options ────────────────────────────────────────────


class _ConfigurablePlugin(BasePlugin):
    name = "configurable"

    def __init__(self):
        super().__init__()
        self.options = {"limit": 10}
        self.derived = None

    @classmethod
    def option_schema(cls):
        return {"limit": {"type": "int", "min": 1, "max": 100}}

    def refresh_options(self):
        self.derived = self.options["limit"] * 2


class _NoSuperInitPlugin(BasePlugin):
    """A plugin that overrides ``__init__`` WITHOUT calling
    ``super().__init__()`` — several builtin plugins do exactly this."""

    name = "no-super-init"

    def __init__(self):
        # Deliberately no super().__init__() — so ``self.options`` is
        # never set by BasePlugin.
        self.set_up = True


class TestBasePluginOptions:
    def test_default_option_schema_empty(self):
        assert BasePlugin.option_schema() == {}

    def test_get_options_survives_missing_options_attr(self):
        # Regression guard: a plugin that skips ``super().__init__()``
        # has no ``self.options`` — ``get_options`` must still return
        # ``{}`` instead of AttributeError-ing (the cause of the
        # "Plugin get_options raised; skipping" warning spam).
        plugin = _NoSuperInitPlugin()
        assert plugin.get_options() == {}

    def test_set_options_survives_missing_options_attr(self):
        # ``set_options`` on the same shape must also work — it lazily
        # creates the option store.
        plugin = _NoSuperInitPlugin()
        # Empty schema → only an empty update is valid; it must not raise.
        assert plugin.set_options({}) == {}

    def test_get_options_returns_a_copy(self):
        plugin = _ConfigurablePlugin()
        snapshot = plugin.get_options()
        snapshot["limit"] = 999
        # Mutating the snapshot must not affect the plugin.
        assert plugin.options["limit"] == 10

    def test_set_options_validates_merges_and_refreshes(self):
        plugin = _ConfigurablePlugin()
        result = plugin.set_options({"limit": 20})
        assert result["limit"] == 20
        assert plugin.options["limit"] == 20
        # refresh_options ran and re-derived state.
        assert plugin.derived == 40

    def test_set_options_rejects_out_of_range(self):
        plugin = _ConfigurablePlugin()
        with pytest.raises(PluginOptionError):
            plugin.set_options({"limit": 999})
        # The bad value was NOT stored.
        assert plugin.options["limit"] == 10

    def test_refresh_options_default_is_noop(self):
        assert BasePlugin().refresh_options() is None


# ── BasePlugin: default hook returns ───────────────────────────────


class TestBasePluginDefaultHooks:
    async def test_pre_post_hooks_return_none_by_default(self):
        plugin = BasePlugin()
        assert await plugin.pre_llm_call([]) is None
        assert await plugin.post_llm_call([], "resp", {}) is None
        assert await plugin.pre_tool_dispatch(None, PluginContext()) is None
        assert await plugin.pre_tool_execute({}) is None
        assert await plugin.post_tool_execute(None) is None
        assert await plugin.pre_subagent_run("task") is None
        assert await plugin.post_subagent_run(None) is None

    async def test_compact_start_returns_none_by_default(self):
        # None means "don't veto" — only False vetoes compaction.
        assert await BasePlugin().on_compact_start(1000) is None

    def test_contribution_hooks_default_empty(self):
        plugin = BasePlugin()
        assert plugin.get_prompt_content(PluginContext()) is None
        assert plugin.runtime_services(None) == {}
        assert plugin.contribute_commands() == {}
        assert plugin.contribute_termination_check() is None


class TestPluginBlockError:
    def test_is_an_exception(self):
        err = PluginBlockError("denied: budget exhausted")
        assert isinstance(err, Exception)
        assert str(err) == "denied: budget exhausted"
