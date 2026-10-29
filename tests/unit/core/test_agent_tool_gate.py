"""Unit tests for :mod:`kohakuterrarium.core.agent_tool_gate`.

A tool requiring a renderer stays out of the system prompt until an attached
output reports that renderer. A renderer change refreshes the prompt only
when the visible tool set changes, at once when idle and at the next LLM
request while a turn is running.
"""

import kohakuterrarium as kt
from kohakuterrarium.core.config_types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.testing.llm import ScriptedLLM


class _ShowApp(BaseTool):
    requires_renderers = frozenset({"kt.app"})

    @property
    def tool_name(self) -> str:
        return "show_app"

    @property
    def description(self) -> str:
        return "Show an interactive app."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def prompt_contribution(self) -> str | None:
        return "APP-GUIDANCE-MARKER"

    async def _execute(self, args, **kwargs):
        return ToolResult(output="shown")


def _cfg(tmp_path):
    return AgentConfig(
        name="gate_agent",
        system_prompt="You are a test agent.",
        include_hints_in_prompt=False,
        tool_format="bracket",
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="none"),
    )


class _RendererFeed:
    """Stand-in for the router's renderer report and change subscription."""

    def __init__(self, router):
        self.renderers: dict[str, int] = {}
        self.listeners = []
        router.ui_renderers = lambda: dict(self.renderers)
        router.add_renderers_listener = self.listeners.append

    def set(self, renderers):
        old, self.renderers = self.renderers, dict(renderers)
        for listener in self.listeners:
            listener(old, dict(renderers))


async def _agent(tmp_path, responses=("ok",)):
    agent = await kt.Agent.build(
        _cfg(tmp_path), llm=ScriptedLLM(list(responses)), tools=[_ShowApp()]
    )
    feed = _RendererFeed(agent.output_router)
    agent.watch_renderers()
    return agent, feed


def _count_refreshes(agent):
    calls = []
    original = agent.refresh_system_prompt

    def counting():
        calls.append(1)
        original()

    agent.refresh_system_prompt = counting
    return calls


class TestInitialPrompt:
    async def test_gated_tool_absent_from_the_initial_prompt(self, tmp_path):
        agent, _ = await _agent(tmp_path)
        prompt = agent.get_system_prompt()
        assert "show_app" not in prompt
        assert "APP-GUIDANCE-MARKER" not in prompt


class TestRendererChanges:
    async def test_attach_while_idle_refreshes_at_once(self, tmp_path):
        agent, feed = await _agent(tmp_path)
        feed.set({"kt.app": 1})
        prompt = agent.get_system_prompt()
        assert "`show_app`" in prompt and "APP-GUIDANCE-MARKER" in prompt

    async def test_detach_removes_the_tool_again(self, tmp_path):
        agent, feed = await _agent(tmp_path)
        feed.set({"kt.app": 1})
        feed.set({})
        assert "show_app" not in agent.get_system_prompt()

    async def test_unrelated_renderer_change_does_not_refresh(self, tmp_path):
        agent, feed = await _agent(tmp_path)
        calls = _count_refreshes(agent)
        feed.set({"kt.ui": 1})
        feed.set({"kt.ui": 1, "pkg.board": 2})
        assert calls == []

    async def test_same_tool_set_twice_refreshes_once(self, tmp_path):
        agent, feed = await _agent(tmp_path)
        calls = _count_refreshes(agent)
        feed.set({"kt.app": 1})
        feed.set({"kt.app": 2})
        assert calls == [1]

    async def test_change_during_a_turn_waits_for_the_next_request(self, tmp_path):
        agent, feed = await _agent(tmp_path)
        await agent._processing_lock.acquire()
        try:
            feed.set({"kt.app": 1})
            assert "show_app" not in agent.get_system_prompt()
            assert agent.apply_pending_prompt_refresh() is True
            assert "`show_app`" in agent.get_system_prompt()
            assert agent.apply_pending_prompt_refresh() is False
        finally:
            agent._processing_lock.release()

    async def test_change_reverted_during_a_turn_needs_no_refresh(self, tmp_path):
        agent, feed = await _agent(tmp_path)
        calls = _count_refreshes(agent)
        await agent._processing_lock.acquire()
        try:
            feed.set({"kt.app": 1})
            feed.set({})
            assert agent.apply_pending_prompt_refresh() is False
        finally:
            agent._processing_lock.release()
        assert calls == []

    async def test_controller_applies_the_deferred_refresh_before_its_call(
        self, tmp_path
    ):
        agent, feed = await _agent(tmp_path, responses=("hello",))
        await agent._processing_lock.acquire()
        feed.set({"kt.app": 1})
        agent._processing_lock.release()
        await agent.start()
        try:
            assert "show_app" not in agent.get_system_prompt()
            await agent.run("hi")
        finally:
            await agent.stop()
        assert "`show_app`" in agent.get_system_prompt()
        assert agent._prompt_refresh_pending is False

    async def test_router_without_change_events_is_left_alone(self, tmp_path):
        agent = await kt.Agent.build(
            _cfg(tmp_path), llm=ScriptedLLM(["ok"]), tools=[_ShowApp()]
        )
        agent.watch_renderers()
        assert "show_app" not in agent.get_system_prompt()
        assert agent.apply_pending_prompt_refresh() is False
