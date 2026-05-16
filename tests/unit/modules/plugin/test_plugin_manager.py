"""Unit tests for :mod:`kohakuterrarium.modules.plugin.manager`.

Behavior-first: the manager wraps methods with pre/post hooks that
chain linearly by priority, PluginBlockError propagates, disabled
plugins are skipped, callbacks fire fire-and-forget, vetoable callbacks
honour a single False, and collectors aggregate per-plugin output.
"""

import pytest

from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)
from kohakuterrarium.modules.plugin.manager import PluginManager

# ── Test plugins ───────────────────────────────────────────────────


class _AppendPrePlugin(BasePlugin):
    """pre_llm_call appends a tag so chaining order is observable."""

    def __init__(self, name, priority=50, tag="x"):
        super().__init__()
        self.name = name
        self.priority = priority
        self._tag = tag

    async def pre_llm_call(self, messages, **kwargs):
        return messages + [self._tag]


class _RewritePostPlugin(BasePlugin):
    """post_tool_execute rewrites the result; wrap_method passes the real
    method result as the post hook's first positional arg."""

    def __init__(self, name, priority=50, suffix="!"):
        super().__init__()
        self.name = name
        self.priority = priority
        self._suffix = suffix

    async def post_tool_execute(self, result, **kwargs):
        return result + self._suffix


class _BlockingPlugin(BasePlugin):
    name = "blocker"

    async def pre_tool_execute(self, args, **kwargs):
        raise PluginBlockError("blocked by policy")


class _RaisingPrePlugin(BasePlugin):
    name = "raiser"

    async def pre_llm_call(self, messages, **kwargs):
        raise RuntimeError("plugin bug")


class _CallbackPlugin(BasePlugin):
    def __init__(self, name="cb"):
        super().__init__()
        self.name = name
        self.started = 0
        self.events: list[object] = []

    async def on_agent_start(self):
        self.started += 1

    async def on_event(self, event):
        self.events.append(event)


class _VetoPlugin(BasePlugin):
    def __init__(self, name, vote):
        super().__init__()
        self.name = name
        self._vote = vote

    async def on_compact_start(self, context_length):
        return self._vote


# ── Registration / enable / disable ────────────────────────────────


class TestRegistration:
    def test_register_sorts_by_priority(self):
        mgr = PluginManager()
        mgr.register(_AppendPrePlugin("low", priority=80))
        mgr.register(_AppendPrePlugin("high", priority=10))
        names = [p.name for p in mgr._plugins]
        assert names == ["high", "low"]

    def test_bool_and_len_reflect_registration(self):
        mgr = PluginManager()
        assert bool(mgr) is False
        assert len(mgr) == 0
        mgr.register(_CallbackPlugin())
        assert bool(mgr) is True
        assert len(mgr) == 1

    def test_get_plugin_by_name(self):
        mgr = PluginManager()
        plugin = _CallbackPlugin("found")
        mgr.register(plugin)
        assert mgr.get_plugin("found") is plugin
        assert mgr.get_plugin("missing") is None


class TestEnableDisable:
    def test_disable_then_is_enabled_false(self):
        mgr = PluginManager()
        mgr.register(_CallbackPlugin("p"))
        assert mgr.is_enabled("p") is True
        assert mgr.disable("p") is True
        assert mgr.is_enabled("p") is False

    def test_disable_unknown_returns_false(self):
        assert PluginManager().disable("ghost") is False

    def test_enable_re_enables_disabled_plugin(self):
        mgr = PluginManager()
        mgr.register(_CallbackPlugin("p"))
        mgr.disable("p")
        assert mgr.enable("p") is True
        assert mgr.is_enabled("p") is True
        # Re-enabled plugins are queued for a deferred on_load.
        assert "p" in mgr._needs_load

    def test_enable_already_enabled_returns_true_if_registered(self):
        mgr = PluginManager()
        mgr.register(_CallbackPlugin("p"))
        # Not disabled → enable returns True because the plugin exists.
        assert mgr.enable("p") is True

    def test_list_plugins_reports_enabled_state(self):
        mgr = PluginManager()
        mgr.register(_CallbackPlugin("a"))
        mgr.register(_CallbackPlugin("b"))
        mgr.disable("b")
        listing = {p["name"]: p["enabled"] for p in mgr.list_plugins()}
        assert listing == {"a": True, "b": False}


# ── wrap_method: pre/post chaining ─────────────────────────────────


class TestWrapMethod:
    async def test_no_plugins_returns_original_unchanged(self):
        mgr = PluginManager()

        async def original(x):
            return x

        assert mgr.wrap_method("pre_llm_call", "post_llm_call", original) is original

    async def test_no_relevant_hooks_returns_original(self):
        # Plugins registered, but none override the requested hooks.
        mgr = PluginManager()
        mgr.register(_CallbackPlugin("cb"))

        async def original(x):
            return x

        wrapped = mgr.wrap_method("pre_llm_call", "post_llm_call", original)
        assert wrapped is original

    async def test_pre_hooks_chain_in_priority_order(self):
        # Lower priority runs first in pre — the order of appended tags
        # proves the chain.
        mgr = PluginManager()
        mgr.register(_AppendPrePlugin("second", priority=60, tag="B"))
        mgr.register(_AppendPrePlugin("first", priority=10, tag="A"))

        async def original(messages):
            return messages

        wrapped = mgr.wrap_method("pre_llm_call", "post_llm_call", original)
        result = await wrapped([])
        assert result == ["A", "B"]

    async def test_post_hooks_chain_each_seeing_previous_rewrite(self):
        # wrap_method passes the real method's result as the post hook's
        # first positional arg; each plugin sees the previous rewrite.
        mgr = PluginManager()
        mgr.register(_RewritePostPlugin("p1", priority=10, suffix="-1"))
        mgr.register(_RewritePostPlugin("p2", priority=20, suffix="-2"))

        async def original(args):
            return "base"

        wrapped = mgr.wrap_method("pre_tool_execute", "post_tool_execute", original)
        result = await wrapped({})
        assert result == "base-1-2"

    async def test_block_error_propagates_out_of_wrapper(self):
        mgr = PluginManager()
        mgr.register(_BlockingPlugin())

        async def original(args):
            return "should not run"

        wrapped = mgr.wrap_method("pre_tool_execute", "post_tool_execute", original)
        with pytest.raises(PluginBlockError, match="blocked by policy"):
            await wrapped({})

    async def test_raising_pre_hook_is_logged_and_skipped(self):
        # A non-block exception in a pre hook must NOT abort the call —
        # the plugin is skipped and the original still runs.
        mgr = PluginManager()
        mgr.register(_RaisingPrePlugin())

        async def original(messages):
            return messages

        wrapped = mgr.wrap_method("pre_llm_call", "post_llm_call", original)
        result = await wrapped(["kept"])
        assert result == ["kept"]

    async def test_wrapper_skips_plugins_that_dont_override_the_hook(self):
        # One plugin overrides pre_llm_call, another overrides only
        # post_tool_execute. The wrapper must run only the relevant hook
        # per plugin, skipping the non-overriding one in each phase.
        class _PreOnly(BasePlugin):
            name = "pre_only"

            async def pre_llm_call(self, messages, **kwargs):
                return messages + ["PRE"]

        class _PostOnly(BasePlugin):
            name = "post_only"

            async def post_llm_call(self, messages, response, usage, **kwargs):
                return None

        mgr = PluginManager()
        mgr.register(_PreOnly())
        mgr.register(_PostOnly())

        async def original(messages):
            return messages

        wrapped = mgr.wrap_method("pre_llm_call", "post_llm_call", original)
        result = await wrapped([])
        # _PostOnly is skipped in the pre phase; _PreOnly in the post phase.
        assert result == ["PRE"]

    async def test_disabled_plugin_hooks_do_not_run(self):
        mgr = PluginManager()
        mgr.register(_AppendPrePlugin("p", tag="SHOULD_NOT_APPEAR"))
        mgr.disable("p")

        async def original(messages):
            return messages

        wrapped = mgr.wrap_method("pre_llm_call", "post_llm_call", original)
        # The wrapper exists (the plugin DOES override the hook), but at
        # call time the disabled plugin is filtered out.
        result = await wrapped([])
        assert result == []

    async def test_input_kwarg_passes_first_arg_to_post_hooks(self):
        # input_kwarg="messages" → post_llm_call receives the (possibly
        # pre-transformed) first arg under that kwarg name.
        seen = {}

        class _Recorder(BasePlugin):
            name = "rec"

            async def post_llm_call(self, response, messages=None, **kwargs):
                seen["messages"] = messages
                return None

        mgr = PluginManager()
        mgr.register(_Recorder())

        async def original(messages):
            return "resp"

        wrapped = mgr.wrap_method(
            "pre_llm_call", "post_llm_call", original, input_kwarg="messages"
        )
        await wrapped(["m1"])
        assert seen["messages"] == ["m1"]


# ── run_pre_hooks (generator path) ─────────────────────────────────


class TestRunPreHooks:
    async def test_no_plugins_returns_value_unchanged(self):
        mgr = PluginManager()
        assert await mgr.run_pre_hooks("pre_llm_call", "value") == "value"

    async def test_chains_transform_linearly(self):
        mgr = PluginManager()
        mgr.register(_AppendPrePlugin("a", priority=10, tag="X"))
        mgr.register(_AppendPrePlugin("b", priority=20, tag="Y"))
        result = await mgr.run_pre_hooks("pre_llm_call", [])
        assert result == ["X", "Y"]

    async def test_block_error_propagates(self):
        mgr = PluginManager()
        mgr.register(_BlockingPlugin())
        with pytest.raises(PluginBlockError):
            await mgr.run_pre_hooks("pre_tool_execute", {})


# ── Callbacks ──────────────────────────────────────────────────────


class TestCallbacks:
    async def test_notify_fires_on_all_active_plugins(self):
        mgr = PluginManager()
        p1, p2 = _CallbackPlugin("a"), _CallbackPlugin("b")
        mgr.register(p1)
        mgr.register(p2)
        await mgr.notify("on_agent_start")
        assert p1.started == 1
        assert p2.started == 1

    async def test_notify_skips_disabled_plugins(self):
        mgr = PluginManager()
        p = _CallbackPlugin("a")
        mgr.register(p)
        mgr.disable("a")
        await mgr.notify("on_agent_start")
        assert p.started == 0

    async def test_notify_passes_kwargs(self):
        mgr = PluginManager()
        p = _CallbackPlugin("a")
        mgr.register(p)
        sentinel = object()
        await mgr.notify("on_event", event=sentinel)
        assert p.events == [sentinel]

    async def test_notify_no_plugins_is_noop(self):
        await PluginManager().notify("on_agent_start")  # must not raise

    async def test_failing_callback_does_not_break_others(self):
        class _BadCallback(BasePlugin):
            name = "bad"

            async def on_agent_start(self):
                raise RuntimeError("callback crashed")

        mgr = PluginManager()
        good = _CallbackPlugin("good")
        mgr.register(_BadCallback())
        mgr.register(good)
        await mgr.notify("on_agent_start")
        # The healthy plugin still ran.
        assert good.started == 1


# ── Vetoable callbacks ─────────────────────────────────────────────


class TestVetoableCallbacks:
    async def test_no_plugins_proceeds(self):
        assert await PluginManager().should_proceed("on_compact_start") is True

    async def test_all_non_false_votes_proceed(self):
        mgr = PluginManager()
        mgr.register(_VetoPlugin("a", vote=None))
        mgr.register(_VetoPlugin("b", vote=True))
        assert await mgr.should_proceed("on_compact_start", context_length=100) is True

    async def test_single_false_vote_vetoes(self):
        mgr = PluginManager()
        mgr.register(_VetoPlugin("yes", vote=None))
        mgr.register(_VetoPlugin("no", vote=False))
        assert await mgr.should_proceed("on_compact_start", context_length=100) is False

    async def test_vetoable_callback_failure_is_not_a_veto(self):
        # An exception in a vetoable callback is logged and treated as
        # "no opinion" — it must NOT veto.
        class _BadVeto(BasePlugin):
            name = "bad"

            async def on_compact_start(self, context_length):
                raise RuntimeError("vote crashed")

        mgr = PluginManager()
        mgr.register(_BadVeto())
        assert await mgr.should_proceed("on_compact_start", context_length=1) is True


# ── Collectors ─────────────────────────────────────────────────────


class TestCollectors:
    def test_collect_prompt_contributions_in_priority_order(self):
        class _PromptPlugin(BasePlugin):
            def __init__(self, name, priority, content):
                super().__init__()
                self.name = name
                self.priority = priority
                self._content = content

            def get_prompt_content(self, context):
                return self._content

        mgr = PluginManager()
        mgr.register(_PromptPlugin("late", 90, "LATE"))
        mgr.register(_PromptPlugin("early", 10, "EARLY"))
        out = mgr.collect_prompt_contributions(PluginContext())
        assert out == ["EARLY", "LATE"]

    def test_collect_prompt_contributions_skips_empty_and_errors(self):
        class _EmptyPlugin(BasePlugin):
            name = "empty"

            def get_prompt_content(self, context):
                return ""

        class _ErrorPlugin(BasePlugin):
            name = "err"

            def get_prompt_content(self, context):
                raise RuntimeError("boom")

        mgr = PluginManager()
        mgr.register(_EmptyPlugin())
        mgr.register(_ErrorPlugin())
        # Empty string skipped, error swallowed → no contributions.
        assert mgr.collect_prompt_contributions(PluginContext()) == []

    def test_collect_commands_returns_plugin_command_pairs(self):
        class _CmdPlugin(BasePlugin):
            name = "cmd"

            def contribute_commands(self):
                return {"mycmd": object()}

        mgr = PluginManager()
        plugin = _CmdPlugin()
        mgr.register(plugin)
        pairs = mgr.collect_commands()
        assert len(pairs) == 1
        assert pairs[0][0] is plugin
        assert "mycmd" in pairs[0][1]

    def test_collect_termination_checkers(self):
        def _checker(ctx):
            return None

        class _TermPlugin(BasePlugin):
            name = "term"

            def contribute_termination_check(self):
                return _checker

        mgr = PluginManager()
        mgr.register(_TermPlugin())
        checkers = mgr.collect_termination_checkers()
        assert checkers == [("term", _checker)]

    def test_collect_runtime_services_merges_dicts(self):
        class _ServicePlugin(BasePlugin):
            def __init__(self, name, services):
                super().__init__()
                self.name = name
                self._services = services

            def runtime_services(self, context):
                return self._services

        mgr = PluginManager()
        mgr.register(_ServicePlugin("a", {"svc_a": 1}))
        mgr.register(_ServicePlugin("b", {"svc_b": 2}))
        merged = mgr.collect_runtime_services(None)
        assert merged == {"svc_a": 1, "svc_b": 2}


# ── Lifecycle: load / unload ───────────────────────────────────────


class TestLifecycleLoad:
    async def test_load_all_calls_on_load_with_scoped_context(self):
        seen = {}

        class _LoadPlugin(BasePlugin):
            name = "loader"

            async def on_load(self, context):
                seen["plugin_name"] = context._plugin_name
                seen["agent_name"] = context.agent_name

        mgr = PluginManager()
        mgr.register(_LoadPlugin())
        await mgr.load_all(PluginContext(agent_name="swe"))
        # Each plugin gets a context scoped to its own name.
        assert seen == {"plugin_name": "loader", "agent_name": "swe"}

    async def test_load_all_swallows_on_load_errors(self):
        class _BadLoad(BasePlugin):
            name = "bad"

            async def on_load(self, context):
                raise RuntimeError("load failed")

        mgr = PluginManager()
        mgr.register(_BadLoad())
        # Must not raise — the error is logged and load continues.
        await mgr.load_all(PluginContext(agent_name="a"))

    async def test_load_pending_loads_only_runtime_enabled(self):
        loaded = []

        class _PendingPlugin(BasePlugin):
            def __init__(self, name):
                super().__init__()
                self.name = name

            async def on_load(self, context):
                loaded.append(self.name)

        mgr = PluginManager()
        mgr.register(_PendingPlugin("a"))
        mgr.register(_PendingPlugin("b"))
        await mgr.load_all(PluginContext(agent_name="x"))
        loaded.clear()
        # Disable then re-enable 'b' so it lands in _needs_load.
        mgr.disable("b")
        mgr.enable("b")
        await mgr.load_pending()
        assert loaded == ["b"]
        # The pending set is cleared after loading.
        assert mgr._needs_load == set()

    async def test_load_pending_noop_without_context(self):
        mgr = PluginManager()
        mgr._needs_load.add("ghost")
        # No saved load context → load_pending does nothing, no crash.
        await mgr.load_pending()

    async def test_load_pending_swallows_on_load_errors(self):
        class _BadPending(BasePlugin):
            name = "badpending"

            async def on_load(self, context):
                raise RuntimeError("pending load failed")

        mgr = PluginManager()
        mgr.register(_BadPending())
        await mgr.load_all(PluginContext(agent_name="x"))
        mgr.disable("badpending")
        mgr.enable("badpending")
        # Error in a runtime-enabled plugin's on_load is logged, not raised.
        await mgr.load_pending()
        assert mgr._needs_load == set()

    async def test_unload_all_swallows_on_unload_errors(self):
        class _BadUnload(BasePlugin):
            name = "badunload"

            async def on_unload(self):
                raise RuntimeError("unload failed")

        class _GoodUnload(BasePlugin):
            name = "goodunload"

            def __init__(self):
                super().__init__()
                self.unloaded = False

            async def on_unload(self):
                self.unloaded = True

        mgr = PluginManager()
        mgr.register(_BadUnload())
        good_unload = _GoodUnload()
        mgr.register(good_unload)
        await mgr.unload_all()
        # The bad plugin's error is contained; the good one still unloads.
        assert good_unload.unloaded is True

    async def test_unload_all_runs_in_reverse_order(self):
        order = []

        class _UnloadPlugin(BasePlugin):
            def __init__(self, name, priority):
                super().__init__()
                self.name = name
                self.priority = priority

            async def on_unload(self):
                order.append(self.name)

        mgr = PluginManager()
        mgr.register(_UnloadPlugin("first", priority=10))
        mgr.register(_UnloadPlugin("last", priority=90))
        await mgr.unload_all()
        # Registration sorts by priority; unload reverses it.
        assert order == ["last", "first"]


# ── Hook timing observer ───────────────────────────────────────────


class TestHookTiming:
    async def test_timing_callback_fires_around_each_hook(self):
        records = []

        def _observer(hook, plugin_name, duration_ms, blocked):
            records.append((hook, plugin_name, blocked))

        mgr = PluginManager()
        mgr.set_hook_timing_callback(_observer)
        mgr.register(_AppendPrePlugin("p", tag="T"))

        async def original(messages):
            return messages

        wrapped = mgr.wrap_method("pre_llm_call", "post_llm_call", original)
        await wrapped([])
        assert ("pre_llm_call", "p", False) in records

    async def test_timing_callback_marks_blocked_on_block_error(self):
        records = []
        mgr = PluginManager()
        mgr.set_hook_timing_callback(lambda h, n, d, b: records.append((h, n, b)))
        mgr.register(_BlockingPlugin())

        async def original(args):
            return "x"

        wrapped = mgr.wrap_method("pre_tool_execute", "post_tool_execute", original)
        with pytest.raises(PluginBlockError):
            await wrapped({})
        assert ("pre_tool_execute", "blocker", True) in records


# ── set_plugin_options ─────────────────────────────────────────────


class TestSetPluginOptions:
    def test_unknown_plugin_raises_keyerror(self):
        mgr = PluginManager()
        with pytest.raises(KeyError):
            mgr.set_plugin_options("ghost", {})

    def test_set_plugin_options_delegates_to_plugin(self):
        class _Configurable(BasePlugin):
            name = "cfg"

            def __init__(self):
                super().__init__()
                self.options = {"n": 1}

            @classmethod
            def option_schema(cls):
                return {"n": {"type": "int"}}

        mgr = PluginManager()
        mgr.register(_Configurable())
        result = mgr.set_plugin_options("cfg", {"n": 5})
        assert result["n"] == 5

    def test_list_plugins_with_options_includes_schema(self):
        class _Configurable(BasePlugin):
            name = "cfg"

            def __init__(self):
                super().__init__()
                self.options = {"n": 1}

            @classmethod
            def option_schema(cls):
                return {"n": {"type": "int"}}

        mgr = PluginManager()
        mgr.register(_Configurable())
        listing = mgr.list_plugins_with_options()
        assert listing[0]["schema"] == {"n": {"type": "int"}}
        assert listing[0]["options"] == {"n": 1}


# ── Robustness: errors in collectors / hooks are contained ─────────


class TestCollectorErrorContainment:
    def test_collect_commands_skips_raising_plugin(self):
        class _BadCmd(BasePlugin):
            name = "bad"

            def contribute_commands(self):
                raise RuntimeError("boom")

        class _GoodCmd(BasePlugin):
            name = "good"

            def contribute_commands(self):
                return {"ok": object()}

        mgr = PluginManager()
        mgr.register(_BadCmd())
        mgr.register(_GoodCmd())
        pairs = mgr.collect_commands()
        # The bad plugin is skipped; the good one survives.
        assert [p.name for p, _ in pairs] == ["good"]

    def test_collect_termination_checkers_skips_errors_and_none(self):
        class _BadTerm(BasePlugin):
            name = "bad"

            def contribute_termination_check(self):
                raise RuntimeError("boom")

        class _NoneTerm(BasePlugin):
            name = "none"

            def contribute_termination_check(self):
                return None

        mgr = PluginManager()
        mgr.register(_BadTerm())
        mgr.register(_NoneTerm())
        # Bad raises (skipped), None returns nothing → empty list.
        assert mgr.collect_termination_checkers() == []

    def test_collect_runtime_services_skips_raising_plugin(self):
        class _BadSvc(BasePlugin):
            name = "bad"

            def runtime_services(self, context):
                raise RuntimeError("boom")

        class _GoodSvc(BasePlugin):
            name = "good"

            def runtime_services(self, context):
                return {"svc": 1}

        mgr = PluginManager()
        mgr.register(_BadSvc())
        mgr.register(_GoodSvc())
        assert mgr.collect_runtime_services(None) == {"svc": 1}


class TestHookErrorContainment:
    async def test_should_apply_raising_defaults_to_applicable(self):
        # A plugin whose should_apply raises is treated as applicable
        # (safer to run than silently skip).
        class _BadGate(BasePlugin):
            name = "badgate"

            def should_apply(self, context):
                raise RuntimeError("gate crashed")

            async def pre_llm_call(self, messages, **kwargs):
                return messages + ["RAN"]

        mgr = PluginManager()
        mgr.register(_BadGate())
        mgr._load_context = PluginContext(agent_name="a")

        async def original(messages):
            return messages

        wrapped = mgr.wrap_method("pre_llm_call", "post_llm_call", original)
        result = await wrapped([])
        assert result == ["RAN"]

    async def test_post_hook_exception_is_contained(self):
        class _BadPost(BasePlugin):
            name = "badpost"

            async def post_tool_execute(self, result, **kwargs):
                raise RuntimeError("post crashed")

        mgr = PluginManager()
        mgr.register(_BadPost())

        async def original(args):
            return "result"

        wrapped = mgr.wrap_method("pre_tool_execute", "post_tool_execute", original)
        # The post hook crashing must not lose the real result.
        assert await wrapped({}) == "result"

    async def test_run_pre_hooks_contains_non_block_exception(self):
        class _BadPre(BasePlugin):
            name = "badpre"

            async def pre_llm_call(self, messages, **kwargs):
                raise RuntimeError("pre crashed")

        mgr = PluginManager()
        mgr.register(_BadPre())
        # Non-block exception logged and skipped; value passes through.
        assert await mgr.run_pre_hooks("pre_llm_call", ["kept"]) == ["kept"]

    async def test_run_pre_hooks_skips_plugins_without_override(self):
        # A plugin that does NOT override the hook is simply skipped.
        mgr = PluginManager()
        mgr.register(_CallbackPlugin("no_override"))
        assert await mgr.run_pre_hooks("pre_llm_call", "value") == "value"


class TestSyncPluginMethods:
    async def test_sync_callback_method_is_awaited_correctly(self):
        # _call_method handles both sync and async plugin methods.
        class _SyncCallback(BasePlugin):
            name = "sync"

            def __init__(self):
                super().__init__()
                self.fired = 0

            def on_interrupt(self):  # sync, not async
                self.fired += 1

        mgr = PluginManager()
        plugin = _SyncCallback()
        mgr.register(plugin)
        await mgr.notify("on_interrupt")
        assert plugin.fired == 1

    async def test_notify_skips_plugins_without_the_callback(self):
        # A plugin that doesn't define the callback name is skipped.
        mgr = PluginManager()
        mgr.register(_CallbackPlugin("cb"))
        # 'on_task_promoted' exists on BasePlugin but our plugin doesn't
        # override it — notify still runs without error.
        await mgr.notify("on_task_promoted", job_id="j1", tool_name="bash")

    async def test_should_proceed_skips_plugins_without_callback(self):
        # _CallbackPlugin doesn't define on_compact_start → no veto.
        mgr = PluginManager()
        mgr.register(_CallbackPlugin("cb"))
        assert await mgr.should_proceed("on_compact_start") is True
