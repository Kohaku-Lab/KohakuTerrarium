"""Unit tests for ``modules/ui_owner.py`` — the UI-owner interface."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.core.trigger_manager import TriggerManager
from kohakuterrarium.errors import InvalidRequestError
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.plugin.manager.manager import PluginManager
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.modules.ui_owner import (
    OwnerArgumentError,
    OwnerFailedError,
    OwnerHasNoUIError,
    OwnerNotLoadedError,
    UIOwnerMixin,
    accepts_context,
    bind_publisher,
    has_ui,
    owner_call,
    owner_state,
    parse_owner,
    resolve_runtime_owner,
)


class CounterPlugin(BasePlugin):
    name = "counter"

    def __init__(self):
        super().__init__()
        self.count = 0

    def ui_state(self):
        return {"count": self.count}

    async def ui_call(self, action, args):
        if action != "add":
            raise OwnerArgumentError(f"unknown action {action}")
        self.count += int(args.get("n", 1))
        self.ui_emit("state", self.ui_state())
        return self.count


class SilentPlugin(BasePlugin):
    name = "silent"


class EchoTool(BaseTool):
    @property
    def tool_name(self):
        return "echo"

    @property
    def description(self):
        return "echo"

    @property
    def execution_mode(self):
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(output="ok")

    def ui_call(self, action, args, context=None):
        return {"action": action, "args": args, "context": context}


class PingTrigger(BaseTrigger):
    ui_name = "ping"

    async def wait_for_trigger(self):
        return TriggerEvent(type="noop")

    async def ui_state(self):
        return "pong"


async def _noop(*_args, **_kwargs):
    return None


@pytest.fixture
async def agent():
    plugins = PluginManager()
    plugins.register(CounterPlugin())
    plugins.register(SilentPlugin())
    registry = Registry()
    registry.register_tool(EchoTool())
    triggers = TriggerManager(_noop)
    await triggers.add(PingTrigger(), trigger_id="trigger_abc", autostart=False)
    return SimpleNamespace(plugins=plugins, registry=registry, trigger_manager=triggers)


def test_parse_owner():
    assert parse_owner("plugin:budget") == ("plugin", "budget")
    assert parse_owner("package: kt-qqbot ") == ("package", "kt-qqbot")
    for bad in ("budget", "gadget:x", "plugin:", "", None):
        with pytest.raises(InvalidRequestError):
            parse_owner(bad)


def test_emit_is_noop_until_bound_then_reaches_publisher():
    plugin = CounterPlugin()
    plugin.ui_emit("state", {"x": 1})
    seen = []
    bind_publisher(plugin, lambda event, data, user_id=None: seen.append((event, data)))
    plugin.ui_emit("tick", 3)
    assert seen == [("tick", 3)]

    class Plain:
        def ui_state(self):
            return {}

    plain = Plain()
    bind_publisher(
        plain, lambda event, data, user_id=None: seen.append(("plain", event))
    )
    plain.ui_emit("x", None)
    assert seen[-1] == ("plain", "x")
    assert isinstance(plugin, UIOwnerMixin) and not isinstance(plain, UIOwnerMixin)


async def test_state_and_call_dispatch(agent):
    plugin = resolve_runtime_owner(agent, "plugin", "counter")
    pushes = []
    bind_publisher(
        plugin, lambda event, data, user_id=None: pushes.append((event, data))
    )
    assert await owner_state(plugin) == {"count": 0}
    assert await owner_call(plugin, "add", {"n": 2}) == 2
    assert pushes == [("state", {"count": 2})]
    with pytest.raises(OwnerArgumentError):
        await owner_call(plugin, "explode", {})

    tool = resolve_runtime_owner(agent, "tool", "echo")
    result = await owner_call(tool, "go", {"a": 1}, context="ctx")
    assert result == {"action": "go", "args": {"a": 1}, "context": "ctx"}
    assert await owner_state(tool) == {}

    trigger = resolve_runtime_owner(agent, "trigger", "trigger_abc")
    assert resolve_runtime_owner(agent, "trigger", "ping") is trigger
    assert await owner_state(trigger) == {"value": "pong"}
    with pytest.raises(InvalidRequestError):
        await owner_call(trigger, "x", {})


async def test_missing_disabled_and_ui_less_owners(agent):
    silent = resolve_runtime_owner(agent, "plugin", "silent")
    assert not has_ui(silent)
    with pytest.raises(OwnerHasNoUIError):
        await owner_state(silent)
    with pytest.raises(OwnerHasNoUIError):
        await owner_call(silent, "x", {})

    agent.plugins.disable("counter")
    with pytest.raises(OwnerNotLoadedError):
        resolve_runtime_owner(agent, "plugin", "counter")
    for kind, name in (("plugin", "nope"), ("tool", "nope"), ("trigger", "nope")):
        with pytest.raises(OwnerNotLoadedError):
            resolve_runtime_owner(agent, kind, name)
    with pytest.raises(OwnerNotLoadedError):
        resolve_runtime_owner(SimpleNamespace(), "plugin", "counter")


async def test_owner_errors_are_classified():
    class Owner(UIOwnerMixin):
        def ui_state(self, context=None):
            return {"ctx": context}

        def ui_call(self, action, args):
            if action == "args":
                raise OwnerArgumentError("need n")
            raise ValueError("bug inside the owner")

    owner = Owner()
    with pytest.raises(OwnerArgumentError):
        await owner_call(owner, "args", {})
    with pytest.raises(OwnerFailedError) as info:
        await owner_call(owner, "other", {})
    assert isinstance(info.value.__cause__, ValueError)
    assert await owner_state(owner, context="c") == {"ctx": "c"}
    assert accepts_context(owner.ui_state)
    assert not accepts_context(owner.ui_call)


def test_emit_forwards_the_target_user():
    plugin = CounterPlugin()
    seen = []
    bind_publisher(plugin, lambda event, data, user_id=None: seen.append(user_id))
    plugin.ui_emit("x", 1, user_id=7)
    plugin.ui_emit("x", 1)
    assert seen == [7, None]


def test_trigger_declares_ui_name():
    assert BaseTrigger.ui_name == ""
    assert PingTrigger.ui_name == "ping"
