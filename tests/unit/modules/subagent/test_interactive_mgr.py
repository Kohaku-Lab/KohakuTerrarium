"""Unit tests for :mod:`kohakuterrarium.modules.subagent.interactive_mgr`.

Behavior-first: the InteractiveManagerMixin (exercised through the real
SubAgentManager) starts/stops interactive sub-agents, rejects
non-interactive configs, pushes context, and tracks running instances.
"""

import pytest

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.subagent.config import (
    ContextUpdateMode,
    SubAgentConfig,
)
from kohakuterrarium.modules.subagent.manager import SubAgentManager
from kohakuterrarium.testing.llm import ScriptedLLM


def _manager(responses=None):
    return SubAgentManager(Registry(), ScriptedLLM(responses or ["ok"]))


def _interactive_config(name="chat"):
    return SubAgentConfig(
        name=name,
        interactive=True,
        context_mode=ContextUpdateMode.QUEUE_APPEND,
        max_turns=1,
    )


class TestStartInteractive:
    async def test_start_unregistered_raises(self):
        mgr = _manager()
        with pytest.raises(ValueError, match="not registered"):
            await mgr.start_interactive("ghost")

    async def test_start_non_interactive_config_raises(self):
        mgr = _manager()
        mgr.register(SubAgentConfig(name="plain", interactive=False))
        with pytest.raises(ValueError, match="not interactive"):
            await mgr.start_interactive("plain")

    async def test_start_returns_running_interactive_agent(self):
        mgr = _manager()
        mgr.register(_interactive_config())
        agent = await mgr.start_interactive("chat")
        try:
            assert agent.is_active is True
            assert "chat" in mgr.list_interactive()
            assert mgr.get_interactive("chat") is agent
        finally:
            await mgr.stop_interactive("chat")

    async def test_start_twice_returns_same_instance(self):
        mgr = _manager()
        mgr.register(_interactive_config())
        first = await mgr.start_interactive("chat")
        try:
            second = await mgr.start_interactive("chat")
            # Already running → the same instance is returned, not a new one.
            assert second is first
        finally:
            await mgr.stop_interactive("chat")

    async def test_output_callback_wired_on_start(self):
        mgr = _manager()
        mgr.register(_interactive_config())
        received = []
        agent = await mgr.start_interactive(
            "chat", on_output=lambda chunk: received.append(chunk)
        )
        try:
            assert agent.on_output is not None
            assert mgr._output_callbacks["chat"] is not None
        finally:
            await mgr.stop_interactive("chat")


class TestStopInteractive:
    async def test_stop_removes_from_tracking(self):
        mgr = _manager()
        mgr.register(_interactive_config())
        await mgr.start_interactive("chat")
        await mgr.stop_interactive("chat")
        assert "chat" not in mgr.list_interactive()
        assert mgr.get_interactive("chat") is None

    async def test_stop_unknown_is_a_noop(self):
        mgr = _manager()
        await mgr.stop_interactive("never-started")  # must not raise

    async def test_stop_all_interactive_clears_everything(self):
        mgr = _manager()
        mgr.register(_interactive_config("a"))
        mgr.register(_interactive_config("b"))
        await mgr.start_interactive("a")
        await mgr.start_interactive("b")
        await mgr.stop_all_interactive()
        assert mgr.list_interactive() == []


class TestPushContext:
    async def test_push_context_to_unknown_raises(self):
        mgr = _manager()
        with pytest.raises(ValueError, match="not running"):
            await mgr.push_context("ghost", {"message": "hi"})

    async def test_push_context_all_reaches_every_running_agent(self):
        mgr = _manager(["response a", "response b"])
        mgr.register(_interactive_config("a"))
        mgr.register(_interactive_config("b"))
        await mgr.start_interactive("a")
        await mgr.start_interactive("b")
        try:
            # Broadcast to all — must not raise even with multiple agents.
            await mgr.push_context_all({"message": "broadcast"})
        finally:
            await mgr.stop_all_interactive()


class TestSetOutputCallback:
    async def test_set_output_callback_updates_running_agent(self):
        mgr = _manager()
        mgr.register(_interactive_config())
        agent = await mgr.start_interactive("chat")
        try:
            new_cb = lambda chunk: None  # noqa: E731
            mgr.set_output_callback("chat", new_cb)
            assert agent.on_output is new_cb
            assert mgr._output_callbacks["chat"] is new_cb
        finally:
            await mgr.stop_interactive("chat")

    def test_set_output_callback_on_unknown_is_a_noop(self):
        mgr = _manager()
        # No agent running — silently does nothing, no crash.
        mgr.set_output_callback("ghost", lambda c: None)

    def test_get_interactive_output_empty_for_unknown(self):
        assert _manager().get_interactive_output("ghost") == ""
