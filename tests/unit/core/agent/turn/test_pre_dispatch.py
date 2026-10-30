"""Unit tests for :mod:`kohakuterrarium.core.agent_pre_dispatch`."""

import types
from pathlib import Path


from kohakuterrarium.core.agent_pre_dispatch import (
    run_pre_subagent_dispatch,
    run_pre_tool_dispatch,
)
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError
from kohakuterrarium.parsing import SubAgentCallEvent, ToolCallEvent

# ── stubs ────────────────────────────────────────────────────────


class _StubController:
    def __init__(self, tool_format="bracket"):
        self.events: list = []
        self.appended_messages: list = []
        self.config = types.SimpleNamespace(tool_format=tool_format)
        self.conversation = types.SimpleNamespace(
            append=lambda *args, **kwargs: self.appended_messages.append(
                {"args": args, "kwargs": kwargs}
            )
        )

    def push_event_sync(self, event):
        self.events.append(event)


class _StubPluginManager:
    def __init__(self, plugins=None, prehook_result=None, prehook_raise=None):
        self._plugins = plugins or [object()]  # truthy default
        self._app = plugins or []
        self._prehook_result = prehook_result
        self._prehook_raise = prehook_raise
        self.prehook_calls: list = []

    def _applicable_plugins(self):
        return list(self._app)

    async def run_pre_hooks(self, hook_name, value, **kwargs):
        self.prehook_calls.append((hook_name, value, kwargs))
        if self._prehook_raise is not None:
            raise self._prehook_raise
        return self._prehook_result if self._prehook_result is not None else value


class _PrePlugin(BasePlugin):
    """Plugin that rewrites the tool args."""

    def __init__(self, name, *, replacement=None, raises=None):
        self.name = name
        self._rep = replacement
        self._raises = raises

    async def pre_tool_dispatch(self, event, ctx):
        if self._raises is not None:
            raise self._raises
        return self._rep


class _NoOpPlugin(BasePlugin):
    def __init__(self):
        self.name = "noop"


def _agent(*, plugins=None, registry_tools=None):
    return types.SimpleNamespace(
        plugins=plugins,
        config=types.SimpleNamespace(name="alice"),
        executor=types.SimpleNamespace(_working_dir=Path(".")),
        llm=types.SimpleNamespace(model="m"),
        registry=types.SimpleNamespace(list_tools=lambda: list(registry_tools or [])),
    )


# ── run_pre_tool_dispatch ────────────────────────────────────────


class TestRunPreToolDispatch:
    async def test_no_plugins_passthrough(self):
        evt = ToolCallEvent(name="bash", args={}, raw="")
        c = _StubController()
        result = await run_pre_tool_dispatch(_agent(plugins=None), evt, c)
        assert result is evt

    async def test_empty_plugin_manager(self):
        evt = ToolCallEvent(name="bash", args={}, raw="")
        c = _StubController()
        mgr = _StubPluginManager(plugins=[])
        mgr._plugins = []  # falsy → returns evt verbatim
        result = await run_pre_tool_dispatch(_agent(plugins=mgr), evt, c)
        assert result is evt

    async def test_no_overriding_plugins(self):
        evt = ToolCallEvent(name="bash", args={}, raw="")
        c = _StubController()
        mgr = _StubPluginManager(plugins=[_NoOpPlugin()])
        result = await run_pre_tool_dispatch(_agent(plugins=mgr), evt, c)
        assert result is evt

    async def test_plugin_rewrites_event(self):
        original = ToolCallEvent(name="bash", args={"command": "ls"}, raw="")
        rewritten = ToolCallEvent(name="bash", args={"command": "ls -la"}, raw="")
        plug = _PrePlugin("rewriter", replacement=rewritten)
        mgr = _StubPluginManager(plugins=[plug])
        c = _StubController()
        a = _agent(plugins=mgr, registry_tools=["bash"])
        result = await run_pre_tool_dispatch(a, original, c)
        assert result is rewritten

    async def test_block_synthesises_tool_result(self):
        evt = ToolCallEvent(name="bash", args={"command": "rm -rf /"}, raw="")
        plug = _PrePlugin("guard", raises=PluginBlockError("dangerous"))
        mgr = _StubPluginManager(plugins=[plug])
        c = _StubController()
        a = _agent(plugins=mgr, registry_tools=["bash"])
        result = await run_pre_tool_dispatch(a, evt, c)
        assert result is None
        # Synthetic tool_complete event injected.
        assert len(c.events) == 1
        injected = c.events[0]
        assert "guard" in injected.content
        assert "dangerous" in injected.content

    async def test_block_native_mode_appends_tool_message(self):
        evt = ToolCallEvent(
            name="bash",
            args={"command": "rm", "_tool_call_id": "call_42"},
            raw="",
        )
        plug = _PrePlugin("guard", raises=PluginBlockError("nope"))
        mgr = _StubPluginManager(plugins=[plug])
        c = _StubController(tool_format="native")
        a = _agent(plugins=mgr, registry_tools=["bash"])
        result = await run_pre_tool_dispatch(a, evt, c)
        assert result is None
        # Tool message appended to conversation.
        assert c.appended_messages
        msg = c.appended_messages[0]
        assert msg["args"] == ("tool", "[guard] nope")
        assert msg["kwargs"]["tool_call_id"] == "call_42"

    async def test_plugin_exception_skipped(self):
        evt = ToolCallEvent(name="bash", args={}, raw="")
        plug = _PrePlugin("flaky", raises=RuntimeError("boom"))
        ok = _PrePlugin(
            "ok", replacement=ToolCallEvent(name="bash", args={"x": 1}, raw="")
        )
        mgr = _StubPluginManager(plugins=[plug, ok])
        c = _StubController()
        a = _agent(plugins=mgr, registry_tools=["bash"])
        result = await run_pre_tool_dispatch(a, evt, c)
        # Flaky's exception swallowed; ok still ran.
        assert result.args == {"x": 1}

    async def test_non_event_return_ignored(self):
        evt = ToolCallEvent(name="bash", args={}, raw="")
        plug = _PrePlugin("weird", replacement="not an event")  # bogus type
        mgr = _StubPluginManager(plugins=[plug])
        c = _StubController()
        a = _agent(plugins=mgr, registry_tools=["bash"])
        result = await run_pre_tool_dispatch(a, evt, c)
        # Bogus return ignored — original event survives.
        assert result is evt

    async def test_rewrite_to_unknown_tool_blocked(self):
        evt = ToolCallEvent(name="bash", args={}, raw="")
        new_evt = ToolCallEvent(name="ghost_tool", args={}, raw="")
        plug = _PrePlugin("renamer", replacement=new_evt)
        mgr = _StubPluginManager(plugins=[plug])
        c = _StubController()
        a = _agent(plugins=mgr, registry_tools=["bash"])  # ghost_tool not present
        result = await run_pre_tool_dispatch(a, evt, c)
        assert result is None
        # Veto produced a synthesised result.
        assert c.events

    async def test_none_return_does_not_overwrite_current(self):
        # When plugin returns None, the event is left as-is, so the
        # original passes through.
        evt = ToolCallEvent(name="bash", args={}, raw="")
        plug = _PrePlugin("nop", replacement=None)
        mgr = _StubPluginManager(plugins=[plug])
        c = _StubController()
        a = _agent(plugins=mgr, registry_tools=["bash"])
        result = await run_pre_tool_dispatch(a, evt, c)
        assert result is evt


# ── run_pre_subagent_dispatch ────────────────────────────────────


class TestRunPreSubagentDispatch:
    async def test_no_plugins_passthrough(self):
        evt = SubAgentCallEvent(name="explore", args={"task": "do it"}, raw="")
        c = _StubController()
        result = await run_pre_subagent_dispatch(_agent(plugins=None), evt, c)
        assert result is evt

    async def test_empty_plugins_passthrough(self):
        evt = SubAgentCallEvent(name="explore", args={"task": "do it"}, raw="")
        c = _StubController()
        mgr = _StubPluginManager(plugins=[])
        mgr._plugins = []
        result = await run_pre_subagent_dispatch(_agent(plugins=mgr), evt, c)
        assert result is evt

    async def test_task_unchanged_returns_original(self):
        evt = SubAgentCallEvent(name="explore", args={"task": "do it"}, raw="")
        mgr = _StubPluginManager(prehook_result="do it")  # unchanged
        c = _StubController()
        result = await run_pre_subagent_dispatch(_agent(plugins=mgr), evt, c)
        assert result is evt

    async def test_task_rewritten(self):
        evt = SubAgentCallEvent(name="explore", args={"task": "old"}, raw="")
        mgr = _StubPluginManager(prehook_result="new task")
        c = _StubController()
        result = await run_pre_subagent_dispatch(_agent(plugins=mgr), evt, c)
        # New event with updated task.
        assert result is not evt
        assert result.args["task"] == "new task"

    async def test_content_field_rewritten(self):
        evt = SubAgentCallEvent(name="explore", args={"content": "old"}, raw="")
        mgr = _StubPluginManager(prehook_result="new content")
        c = _StubController()
        result = await run_pre_subagent_dispatch(_agent(plugins=mgr), evt, c)
        # ``task`` not present originally and ``content`` is — rewrite goes to content.
        assert result.args["content"] == "new content"
        assert "task" not in result.args

    async def test_block_synthesises_subagent_result(self):
        evt = SubAgentCallEvent(name="explore", args={"task": "x"}, raw="")
        mgr = _StubPluginManager(prehook_raise=PluginBlockError("policy violation"))
        c = _StubController()
        result = await run_pre_subagent_dispatch(_agent(plugins=mgr), evt, c)
        assert result is None
        # Synthetic event injected.
        assert c.events
        injected = c.events[0]
        assert "explore" in injected.content
        assert "policy violation" in injected.content
