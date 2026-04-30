"""End-to-end test for the permgate plugin (Phase B exemplar).

Wires a real OutputRouter, a fake renderer that auto-replies, the
PluginContext, and the PermGatePlugin. Verifies that:

- A gated tool call emits a ``confirm`` event and waits for a reply.
- ``allow_once`` lets the call proceed.
- ``allow_session`` proceeds and short-circuits future calls for that
  tool name.
- ``deny`` raises ``PluginBlockError`` with a clear message.
- Timeout raises ``PluginBlockError`` with a "no response" message.
- Allow-listed tools never reach the gate.
"""

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.plugins.permgate.plugin import PermGatePlugin
from kohakuterrarium.modules.output.event import UIReply
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.modules.plugin.base import (
    PluginBlockError,
    PluginContext,
)
from kohakuterrarium.testing import OutputRecorder


class _AutoReplyOutput(OutputRecorder):
    """Test renderer that auto-submits a UIReply to the router as soon
    as it sees a confirm event. Configurable per-call via
    ``set_next_reply``.
    """

    def __init__(self):
        super().__init__()
        self._next_action_id: str | None = None
        self._delay_s: float = 0.01
        self._router_ref = None  # set by the router via _maybe_link_router

    def set_next_reply(self, action_id: str, delay_s: float = 0.01) -> None:
        self._next_action_id = action_id
        self._delay_s = delay_s

    async def emit(self, event):
        await super().emit(event)
        if event.type == "confirm" and self._next_action_id is not None:
            asyncio.create_task(self._deliver(event.id, self._next_action_id))

    async def _deliver(self, event_id: str, action_id: str) -> None:
        await asyncio.sleep(self._delay_s)
        router = getattr(self, "_router", None)
        if router is None:
            return
        router.submit_reply(UIReply(event_id=event_id, action_id=action_id, values={}))


def _make_context(plugin: PermGatePlugin, router: OutputRouter) -> PluginContext:
    """Build a PluginContext that exposes the router via host_agent."""
    fake_agent = SimpleNamespace(output_router=router)
    return PluginContext(
        agent_name="test",
        session_id="s1",
        model="test/model",
        _host_agent=fake_agent,
        _plugin_name="permgate",
    )


@pytest.mark.asyncio
async def test_permgate_allow_once_passes_through():
    rec = _AutoReplyOutput()
    rec.set_next_reply("allow_once")
    router = OutputRouter(default_output=rec)
    plugin = PermGatePlugin(timeout_s=2.0)
    ctx = _make_context(plugin, router)
    await plugin.on_load(ctx)

    result = await plugin.pre_tool_execute({"command": "ls"}, tool_name="bash")
    # Returning None means "pass through, don't modify args."
    assert result is None
    # The confirm event was actually emitted.
    assert any(e.type == "confirm" for e in rec.events)


@pytest.mark.asyncio
async def test_permgate_allow_session_remembers_choice():
    rec = _AutoReplyOutput()
    rec.set_next_reply("allow_session")
    router = OutputRouter(default_output=rec)
    plugin = PermGatePlugin(timeout_s=2.0)
    ctx = _make_context(plugin, router)
    await plugin.on_load(ctx)

    result1 = await plugin.pre_tool_execute({"x": 1}, tool_name="bash")
    assert result1 is None
    initial_event_count = sum(1 for e in rec.events if e.type == "confirm")

    # Second call to the same tool: no second prompt.
    result2 = await plugin.pre_tool_execute({"x": 2}, tool_name="bash")
    assert result2 is None
    second_event_count = sum(1 for e in rec.events if e.type == "confirm")
    assert second_event_count == initial_event_count


@pytest.mark.asyncio
async def test_permgate_deny_raises_block_error():
    rec = _AutoReplyOutput()
    rec.set_next_reply("deny")
    router = OutputRouter(default_output=rec)
    plugin = PermGatePlugin(timeout_s=2.0)
    ctx = _make_context(plugin, router)
    await plugin.on_load(ctx)

    with pytest.raises(PluginBlockError, match="blocked by user"):
        await plugin.pre_tool_execute({"command": "rm -rf /"}, tool_name="bash")


@pytest.mark.asyncio
async def test_permgate_timeout_raises_block_error():
    rec = OutputRecorder()  # plain recorder; no auto-reply.
    router = OutputRouter(default_output=rec)
    plugin = PermGatePlugin(timeout_s=0.05)
    ctx = _make_context(plugin, router)
    await plugin.on_load(ctx)

    with pytest.raises(PluginBlockError, match="no response"):
        await plugin.pre_tool_execute({"command": "ls"}, tool_name="bash")


@pytest.mark.asyncio
async def test_permgate_allowlist_skips_gate_entirely():
    rec = _AutoReplyOutput()
    rec.set_next_reply("deny")  # would deny if gate fired
    router = OutputRouter(default_output=rec)
    plugin = PermGatePlugin(allowlist=["read"], timeout_s=2.0)
    ctx = _make_context(plugin, router)
    await plugin.on_load(ctx)

    # ``read`` is allowlisted, so no event should fire.
    result = await plugin.pre_tool_execute({"path": "x.txt"}, tool_name="read")
    assert result is None
    assert not any(e.type == "confirm" for e in rec.events)


@pytest.mark.asyncio
async def test_permgate_only_gates_listed_tools_when_specified():
    rec = _AutoReplyOutput()
    rec.set_next_reply("deny")
    router = OutputRouter(default_output=rec)
    plugin = PermGatePlugin(gated_tools=["bash"], timeout_s=2.0)
    ctx = _make_context(plugin, router)
    await plugin.on_load(ctx)

    # ``write`` is not in gated list — pass through silently.
    result = await plugin.pre_tool_execute({"x": 1}, tool_name="write")
    assert result is None
    assert not any(e.type == "confirm" for e in rec.events)
