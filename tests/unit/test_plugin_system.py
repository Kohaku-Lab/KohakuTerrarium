"""Tests for the plugin system (pre/post hooks, callbacks, manager)."""

import pytest

from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)
from kohakuterrarium.modules.plugin.manager import PluginManager

# ── Test plugins ──


class CounterPlugin(BasePlugin):
    name = "counter"
    priority = 10

    def __init__(self):
        self.calls: dict[str, int] = {}

    async def on_agent_start(self):
        self.calls["agent_start"] = self.calls.get("agent_start", 0) + 1

    async def on_event(self, event=None):
        self.calls["event"] = self.calls.get("event", 0) + 1

    async def pre_llm_call(self, messages, **kwargs):
        self.calls["pre_llm"] = self.calls.get("pre_llm", 0) + 1
        return None

    async def post_llm_call(self, messages, response, usage, **kwargs):
        self.calls["post_llm"] = self.calls.get("post_llm", 0) + 1

    async def pre_tool_execute(self, args, **kwargs):
        self.calls["pre_tool"] = self.calls.get("pre_tool", 0) + 1
        return None

    async def post_tool_execute(self, result, **kwargs):
        self.calls["post_tool"] = self.calls.get("post_tool", 0) + 1
        return None


class TransformPlugin(BasePlugin):
    name = "transform"
    priority = 20

    async def pre_llm_call(self, messages, **kwargs):
        return messages + [{"role": "system", "content": "[injected]"}]

    async def pre_tool_execute(self, args, **kwargs):
        return {**args, "_plugin_tag": True}

    async def post_tool_execute(self, result, **kwargs):
        if hasattr(result, "output") and result.output:
            result.output += " [filtered]"
            return result
        return None


class BlockerPlugin(BasePlugin):
    name = "blocker"
    priority = 5

    async def pre_tool_execute(self, args, **kwargs):
        if kwargs.get("tool_name") == "dangerous":
            raise PluginBlockError("Tool 'dangerous' blocked by policy")
        return None


class BuggyPlugin(BasePlugin):
    name = "buggy"
    priority = 50

    async def on_agent_start(self):
        raise RuntimeError("Plugin bug!")

    async def pre_tool_execute(self, args, **kwargs):
        raise ValueError("Bug in pre hook!")


# ── Manager basics ──


class TestPluginManager:
    def test_empty_is_falsy(self):
        assert not PluginManager()

    def test_register_is_truthy(self):
        mgr = PluginManager()
        mgr.register(CounterPlugin())
        assert mgr

    def test_priority_ordering(self):
        mgr = PluginManager()
        p1 = CounterPlugin()  # 10
        p2 = TransformPlugin()  # 20
        p3 = BlockerPlugin()  # 5
        mgr.register(p1)
        mgr.register(p2)
        mgr.register(p3)
        assert mgr._plugins == [p3, p1, p2]

    def test_enable_disable(self):
        mgr = PluginManager()
        mgr.register(CounterPlugin())
        assert mgr.is_enabled("counter")
        mgr.disable("counter")
        assert not mgr.is_enabled("counter")
        mgr.enable("counter")
        assert mgr.is_enabled("counter")


# ── Callbacks ──


class TestCallbacks:
    @pytest.mark.asyncio
    async def test_notify_fires_all(self):
        mgr = PluginManager()
        c1, c2 = CounterPlugin(), CounterPlugin()
        c2.name = "counter2"
        c2.priority = 20
        mgr.register(c1)
        mgr.register(c2)
        await mgr.notify("on_agent_start")
        assert c1.calls["agent_start"] == 1
        assert c2.calls["agent_start"] == 1

    @pytest.mark.asyncio
    async def test_notify_empty(self):
        await PluginManager().notify("on_agent_start")

    @pytest.mark.asyncio
    async def test_notify_with_data(self):
        mgr = PluginManager()
        c = CounterPlugin()
        mgr.register(c)
        await mgr.notify("on_event", event="test")
        assert c.calls["event"] == 1

    @pytest.mark.asyncio
    async def test_buggy_callback_skipped(self):
        mgr = PluginManager()
        mgr.register(BuggyPlugin())
        await mgr.notify("on_agent_start")  # No raise

    @pytest.mark.asyncio
    async def test_disabled_skipped(self):
        mgr = PluginManager()
        c = CounterPlugin()
        mgr.register(c)
        mgr.disable("counter")
        await mgr.notify("on_agent_start")
        assert "agent_start" not in c.calls


# ── Pre-hooks (standalone) ──


class TestPreHooks:
    @pytest.mark.asyncio
    async def test_transforms(self):
        mgr = PluginManager()
        mgr.register(TransformPlugin())
        result = await mgr.run_pre_hooks(
            "pre_llm_call", [{"role": "user", "content": "hi"}], model="x"
        )
        assert len(result) == 2
        assert result[1]["content"] == "[injected]"

    @pytest.mark.asyncio
    async def test_none_keeps_original(self):
        mgr = PluginManager()
        mgr.register(CounterPlugin())
        msgs = [{"role": "user", "content": "hi"}]
        assert await mgr.run_pre_hooks("pre_llm_call", msgs) is msgs

    @pytest.mark.asyncio
    async def test_empty_passthrough(self):
        assert await PluginManager().run_pre_hooks("pre_llm_call", "x") == "x"

    @pytest.mark.asyncio
    async def test_block_error_propagates(self):
        mgr = PluginManager()
        mgr.register(BlockerPlugin())
        with pytest.raises(PluginBlockError):
            await mgr.run_pre_hooks("pre_tool_execute", {}, tool_name="dangerous")

    @pytest.mark.asyncio
    async def test_buggy_skipped(self):
        mgr = PluginManager()
        mgr.register(BuggyPlugin())
        assert await mgr.run_pre_hooks("pre_tool_execute", {"a": 1}) == {"a": 1}


# ── wrap_method ──


class TestWrapMethod:
    @pytest.mark.asyncio
    async def test_pre_and_post_called(self):
        mgr = PluginManager()
        counter = CounterPlugin()
        mgr.register(counter)

        async def real(args, **kw):
            return type("R", (), {"output": "ok", "success": True})()

        wrapped = mgr.wrap_method("pre_tool_execute", "post_tool_execute", real)
        await wrapped({"cmd": "ls"}, tool_name="bash", job_id="j1")
        assert counter.calls.get("pre_tool") == 1
        assert counter.calls.get("post_tool") == 1

    @pytest.mark.asyncio
    async def test_pre_transforms_input(self):
        mgr = PluginManager()
        mgr.register(TransformPlugin())
        received = {}

        async def real(args, **kw):
            received.update(args)
            return type("R", (), {"output": "ok", "success": True})()

        wrapped = mgr.wrap_method("pre_tool_execute", "post_tool_execute", real)
        await wrapped({"cmd": "ls"}, tool_name="bash", job_id="j1")
        assert received.get("_plugin_tag") is True

    @pytest.mark.asyncio
    async def test_post_transforms_output(self):
        mgr = PluginManager()
        mgr.register(TransformPlugin())

        async def real(args, **kw):
            return type("R", (), {"output": "data", "success": True})()

        wrapped = mgr.wrap_method("pre_tool_execute", "post_tool_execute", real)
        result = await wrapped({}, tool_name="bash", job_id="j1")
        assert result.output == "data [filtered]"

    @pytest.mark.asyncio
    async def test_block_error(self):
        mgr = PluginManager()
        mgr.register(BlockerPlugin())

        async def real(args, **kw):
            return "unreachable"

        wrapped = mgr.wrap_method("pre_tool_execute", "post_tool_execute", real)
        with pytest.raises(PluginBlockError):
            await wrapped({}, tool_name="dangerous", job_id="j1")

    @pytest.mark.asyncio
    async def test_no_plugins_returns_original(self):
        mgr = PluginManager()

        async def real(args):
            return "direct"

        assert mgr.wrap_method("pre_tool_execute", "post_tool_execute", real) is real

    @pytest.mark.asyncio
    async def test_no_overrides_returns_original(self):
        mgr = PluginManager()
        mgr.register(BasePlugin())  # No overrides

        async def real(args):
            return "direct"

        assert mgr.wrap_method("pre_tool_execute", "post_tool_execute", real) is real

    @pytest.mark.asyncio
    async def test_buggy_pre_falls_through(self):
        mgr = PluginManager()
        mgr.register(BuggyPlugin())

        async def real(args, **kw):
            return "ok"

        wrapped = mgr.wrap_method("pre_tool_execute", "post_tool_execute", real)
        assert await wrapped({"a": 1}, tool_name="bash", job_id="j1") == "ok"


# ── Lifecycle ──


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_load_all(self):
        mgr = PluginManager()
        loaded = []

        class P(BasePlugin):
            name = "p"

            async def on_load(self, context):
                loaded.append(context.agent_name)

        mgr.register(P())
        await mgr.load_all(PluginContext(agent_name="test"))
        assert loaded == ["test"]

    @pytest.mark.asyncio
    async def test_unload_reverse(self):
        mgr = PluginManager()
        order = []

        class P1(BasePlugin):
            name = "a"
            priority = 10

            async def on_unload(self):
                order.append("a")

        class P2(BasePlugin):
            name = "b"
            priority = 20

            async def on_unload(self):
                order.append("b")

        mgr.register(P1())
        mgr.register(P2())
        await mgr.unload_all()
        assert order == ["b", "a"]


# ── Bootstrap ──


class TestBootstrap:
    def test_empty_config_discovers_packages(self):
        """Empty config still discovers plugins from installed packages."""
        from kohakuterrarium.bootstrap.plugins import init_plugins

        mgr = init_plugins([])
        # May find package plugins (discovered but disabled)
        for p in mgr.list_plugins():
            assert p["enabled"] is False  # All discovered = disabled

    def test_missing_module_not_loaded(self):
        """A config entry with empty module/class is skipped."""
        from kohakuterrarium.bootstrap.plugins import init_plugins

        mgr = init_plugins([{"name": "bad", "module": "", "class": ""}])
        # "bad" should not appear as enabled
        names = [p["name"] for p in mgr.list_plugins() if p["enabled"]]
        assert "bad" not in names


# ── Context ──


class TestContext:
    def test_defaults(self):
        assert PluginContext().agent_name == ""

    def test_state_no_agent(self):
        ctx = PluginContext(_plugin_name="t")
        assert ctx.get_state("k") is None
        ctx.set_state("k", "v")


# ── Public accessor API (H.5) ──


class _FakeStore:
    def __init__(self):
        self.state: dict = {}


class _FakeController:
    def __init__(self):
        self._pending_injections: list[dict] = []


class _FakeAgent:
    """Minimal stand-in for ``Agent`` used by context accessor tests."""

    def __init__(self, *, with_store: bool = True):
        self.session_store = _FakeStore() if with_store else None
        self.session_memory = None
        self.registry = object()
        self.scratchpad = object()
        self.compact_manager = object()
        self.budgets = object()
        self.controller = _FakeController()
        self.subagent_manager = object()


class TestContextPublicAPI:
    def test_host_agent_returns_agent(self):
        agent = _FakeAgent()
        ctx = PluginContext(_host_agent=agent, _plugin_name="t")
        assert ctx.host_agent is agent

    def test_session_store_returns_store(self):
        agent = _FakeAgent(with_store=True)
        ctx = PluginContext(_host_agent=agent, _plugin_name="t")
        assert ctx.session_store is agent.session_store

    def test_session_store_none_when_not_attached(self):
        agent = _FakeAgent(with_store=False)
        ctx = PluginContext(_host_agent=agent, _plugin_name="t")
        assert ctx.session_store is None

    def test_session_memory_none_when_absent(self):
        agent = _FakeAgent()
        ctx = PluginContext(_host_agent=agent, _plugin_name="t")
        assert ctx.session_memory is None

    def test_registry_scratchpad_compact_subagent_accessors(self):
        agent = _FakeAgent()
        ctx = PluginContext(_host_agent=agent, _plugin_name="t")
        assert ctx.registry is agent.registry
        assert ctx.scratchpad is agent.scratchpad
        assert ctx.compact_manager is agent.compact_manager
        assert ctx.budgets is agent.budgets
        assert ctx.controller is agent.controller
        assert ctx.subagent_manager is agent.subagent_manager

    def test_accessors_none_without_agent(self):
        ctx = PluginContext()
        assert ctx.host_agent is None
        assert ctx.session_store is None
        assert ctx.session_memory is None
        assert ctx.registry is None
        assert ctx.scratchpad is None
        assert ctx.compact_manager is None
        assert ctx.budgets is None
        assert ctx.controller is None
        assert ctx.subagent_manager is None

    def test_private_agent_alias_removed(self):
        """Cluster 7.2 — ``PluginContext._agent`` was hard-removed.

        Accessing the deprecated alias must raise AttributeError (not a
        DeprecationWarning), and the legacy ``_agent=`` constructor
        kwarg is no longer accepted.
        """
        agent = _FakeAgent()
        ctx = PluginContext(_host_agent=agent, _plugin_name="legacy")
        with pytest.raises(AttributeError):
            _ = ctx._agent

    def test_legacy_agent_kwarg_rejected(self):
        agent = _FakeAgent()
        with pytest.raises(TypeError):
            PluginContext(_agent=agent, _plugin_name="legacy")

    def test_inject_message_before_llm_queues_on_controller(self):
        agent = _FakeAgent()
        ctx = PluginContext(_host_agent=agent, _plugin_name="t")
        ctx.inject_message_before_llm("user", "hello")
        assert agent.controller._pending_injections == [
            {"role": "user", "content": "hello"}
        ]

    def test_inject_message_before_llm_noop_without_agent(self):
        ctx = PluginContext(_plugin_name="t")
        # Must not raise — silently drops when no host agent is bound.
        ctx.inject_message_before_llm("user", "hi")


# ── pre_llm_call sees injected messages ──


class _InjectorPlugin(BasePlugin):
    """Injects a message via the context at on_load, then captures the
    messages list it sees on pre_llm_call for assertion."""

    name = "injector"
    priority = 10

    def __init__(self):
        self.captured: list[dict] = []
        self._ctx: PluginContext | None = None

    async def on_load(self, context: PluginContext) -> None:
        self._ctx = context
        context.inject_message_before_llm("system", "[critical] remember to be concise")

    async def pre_llm_call(self, messages, **kwargs):
        self.captured = list(messages)
        return None


@pytest.mark.asyncio
async def test_injected_message_visible_in_pre_llm_call_hook():
    """Emulates the controller drain + pre_llm_call dispatch."""
    from kohakuterrarium.core.controller import Controller, ControllerConfig

    # Build a minimal controller without running LLM — we drive the
    # drain + run_pre_hooks path manually exactly as run_once does.
    class _DummyLLM:
        model = "test-model"

        async def close(self):
            pass

    mgr = PluginManager()
    injector = _InjectorPlugin()
    mgr.register(injector)

    controller = Controller(llm=_DummyLLM(), config=ControllerConfig())
    controller.plugins = mgr

    agent = _FakeAgent()
    agent.controller = controller
    ctx = PluginContext(_host_agent=agent, _plugin_name="injector")
    await mgr.load_all(ctx)

    # Mirror controller._run_once: build messages, drain injections,
    # run pre_llm_call hooks.
    messages = [
        {"role": "system", "content": "you are a helper"},
        {"role": "user", "content": "hi"},
    ]
    if controller._pending_injections:
        injected = controller._pending_injections
        controller._pending_injections = []
        messages = [messages[0]] + list(injected) + messages[1:]

    messages = await mgr.run_pre_hooks("pre_llm_call", messages, model="test")

    # The injector plugin saw the injected system-level message.
    roles = [m["role"] for m in injector.captured]
    assert roles.count("system") == 2
    assert any(
        m["content"] == "[critical] remember to be concise" for m in injector.captured
    )


# ── Compact hook veto (H.6) ──


class _VetoPlugin(BasePlugin):
    name = "vetoer"
    priority = 10

    def __init__(self, veto: bool):
        self._veto = veto
        self.end_called = False

    async def on_compact_start(self, context_length: int):
        return False if self._veto else None

    async def on_compact_end(self, summary: str, messages_removed: int):
        self.end_called = True


class _PlainCallbackPlugin(BasePlugin):
    name = "observer"
    priority = 20

    def __init__(self):
        self.starts = 0
        self.ends = 0

    async def on_compact_start(self, context_length: int):
        self.starts += 1
        # Legacy return — treated as "proceed".
        return None

    async def on_compact_end(self, summary: str, messages_removed: int):
        self.ends += 1


@pytest.mark.asyncio
async def test_should_proceed_true_when_no_plugin_vetoes():
    mgr = PluginManager()
    mgr.register(_PlainCallbackPlugin())
    assert await mgr.should_proceed("on_compact_start", context_length=100)


@pytest.mark.asyncio
async def test_should_proceed_false_when_plugin_vetoes():
    mgr = PluginManager()
    mgr.register(_VetoPlugin(veto=True))
    assert not await mgr.should_proceed("on_compact_start", context_length=100)


@pytest.mark.asyncio
async def test_veto_wins_over_proceed():
    mgr = PluginManager()
    mgr.register(_PlainCallbackPlugin())
    mgr.register(_VetoPlugin(veto=True))
    # Even though one plugin returns None, another returning False vetoes.
    assert not await mgr.should_proceed("on_compact_start", context_length=100)


@pytest.mark.asyncio
async def test_all_none_proceeds():
    mgr = PluginManager()
    mgr.register(_PlainCallbackPlugin())
    assert await mgr.should_proceed("on_compact_start", context_length=1)


@pytest.mark.asyncio
async def test_compact_manager_skips_on_veto():
    """CompactManager consults ``should_proceed`` and skips when vetoed."""
    from kohakuterrarium.core.compact import CompactConfig, CompactManager

    class _FakeConversation:
        def __init__(self, msgs):
            self._m = msgs

        def get_messages(self):
            return self._m

        def get_context_length(self):
            return 999

    class _MsgStub:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    class _FakeControllerForCompact:
        def __init__(self, msgs):
            self.conversation = _FakeConversation(msgs)

    messages = [_MsgStub("system", "sys")]
    for i in range(20):
        messages.append(_MsgStub("user", f"u{i}"))
        messages.append(_MsgStub("assistant", f"a{i}"))

    mgr = PluginManager()
    veto = _VetoPlugin(veto=True)
    mgr.register(veto)

    compact = CompactManager(CompactConfig(keep_recent_turns=2))
    compact._controller = _FakeControllerForCompact(messages)
    compact._plugins = mgr
    compact._agent_name = "test"

    await compact._run_compact()

    # ``on_compact_end`` must NOT fire when vetoed.
    assert veto.end_called is False
