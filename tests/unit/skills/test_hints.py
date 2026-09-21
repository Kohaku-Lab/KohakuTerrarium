"""Path hints remain stable while a user turn runs its tool continuations."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.user_commands.clear import ClearCommand
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.llm.responses_ws import ResponsesWSSession
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.skills.hints import inject_skill_path_hint
from kohakuterrarium.skills.paths import SkillPathScanner
from kohakuterrarium.skills.registry import Skill, SkillRegistry
from kohakuterrarium.testing.llm import ScriptedLLM


def configure_skills(agent, tmp_path):
    (tmp_path / "example.py").write_text("pass\n")
    agent.skills = SkillRegistry()
    skill = Skill("inspect", "Inspect Python files", "Instructions", paths=["*.py"])
    agent.skills.add(skill)
    agent.skill_path_scanner = SkillPathScanner()
    return skill


@pytest.fixture
async def agent(tmp_path):
    agent = await Agent.build(
        AgentConfig(name="hint-test", system_prompt="Test"),
        llm=ScriptedLLM(["OK"]),
        io="headless",
        pwd=tmp_path,
    )
    configure_skills(agent, tmp_path)
    await agent.start()
    try:
        yield agent
    finally:
        await agent.stop()


async def test_no_match_clears_previous_hint_and_flush_drops_it(agent):
    inject_skill_path_hint(agent)
    assert "**inspect**" in agent.controller._skill_path_hint
    agent.skills.disable("inspect")
    inject_skill_path_hint(agent)
    assert agent.controller._skill_path_hint is None
    agent.skills.enable("inspect")
    inject_skill_path_hint(agent)
    agent.controller.flush()
    assert agent.controller._skill_path_hint is None


async def test_rerun_and_folded_user_refresh_current_guidance(agent):
    # A resumed controller has no in-memory snapshot before its first rerun.
    rerun = create_user_input_event("rerun")
    rerun.context = {"rerun": True}
    await agent._begin_batch([rerun])
    assert "Inspect Python files" in agent.controller._skill_path_hint
    agent.skills.get("inspect").description = "Changed guidance"
    await agent._begin_batch([rerun])
    assert "Changed guidance" in agent.controller._skill_path_hint
    agent.skills.get("inspect").description = "Folded user guidance"
    background = TriggerEvent(type="tool_complete", content="done")
    await agent._begin_batch([background, create_user_input_event("new user")])
    assert "Folded user guidance" in agent.controller._skill_path_hint


async def test_clear_command_removes_hint_before_background_round(agent):
    await agent.run("Original user")
    assert agent.controller._skill_path_hint
    result = await ClearCommand().execute("--force", UserCommandContext(agent=agent))
    assert result.error is None
    await agent._process_event(TriggerEvent(type="trigger", content="Background"))
    assert "## Skill Context" not in str(agent.llm.last_messages)
    assert agent.controller._skill_path_hint is None


class _Connection:
    def __init__(self):
        self.sent = []

    async def enter(self):
        return self

    async def close(self):
        pass

    async def send(self, event):
        self.sent.append(deepcopy(event))

    def __aiter__(self):
        async def events():
            n = len(self.sent)
            if n <= 2:
                yield SimpleNamespace(
                    type="response.output_item.done",
                    item=SimpleNamespace(
                        type="function_call",
                        call_id=f"call_{n}",
                        name="inspect",
                        arguments="{}",
                    ),
                )
            else:
                yield SimpleNamespace(type="response.output_text.delta", delta="Done")
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(id=f"response_{n}", usage=None),
            )

        return events()


class _InspectTool(BaseTool):
    def __init__(self, skill):
        super().__init__()
        self.skill = skill
        self.calls = 0

    @property
    def tool_name(self):
        return "inspect"

    @property
    def description(self):
        return "Inspect synthetic files"

    @property
    def execution_mode(self):
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        self.calls += 1
        self.skill.description = "Changed between tool calls"
        return ToolResult(output="Result", exit_code=0)


async def test_native_tool_continuations_keep_hint_and_send_only_delta(tmp_path):
    provider = OpenAIProvider(api_key="test", model="synthetic", websocket_mode=True)
    connection = _Connection()
    provider._ws_session = ResponsesWSSession(lambda: connection)
    agent = await Agent.build(
        AgentConfig(name="hint-ws", system_prompt="Test", tool_format="native"),
        llm=provider,
        io="headless",
        pwd=tmp_path,
    )
    skill = configure_skills(agent, tmp_path)
    tool = _InspectTool(skill)
    agent.registry.register_tool(tool)
    agent.executor.register_tool(tool)
    await agent.start()
    try:
        result = await agent.run("Inspect the files")
        assert result.text == "Done"
        assert tool.calls == 2
        assert len(connection.sent) == 3
        hint = connection.sent[0]["input"][0]
        assert "Inspect Python files" in str(hint)
        for n, request in enumerate(connection.sent[1:], 1):
            assert request.get("previous_response_id") == f"response_{n}"
            assert [item["type"] for item in request["input"]] == [
                "function_call_output"
            ]
            assert request["input"][0]["call_id"] == f"call_{n}"
        # A new user turn refreshes changed guidance, requiring one full send.
        await agent.run("Follow up")
        assert "previous_response_id" not in connection.sent[3]
        assert "Changed between tool calls" in str(connection.sent[3]["input"][0])
        await agent.run("Unchanged guidance")
        assert connection.sent[4]["previous_response_id"] == "response_4"
        # Disabling the matched skill clears its snapshot on the next user turn.
        agent.skills.disable("inspect")
        await agent.run("No skill hint")
        assert "previous_response_id" not in connection.sent[5]
        assert "## Skill Context" not in str(connection.sent[5]["input"])
    finally:
        await agent.stop()
        await provider.close()


@pytest.mark.parametrize("cancel", [False, True])
async def test_next_user_refreshes_hint_after_error_or_cancellation(tmp_path, cancel):
    class InterruptedLLM(ScriptedLLM):
        def __init__(self):
            super().__init__(["Done"])
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def chat(self, messages, **kwargs):
            if not self.started.is_set():
                self.started.set()
                await self.release.wait()
                raise RuntimeError("Synthetic failure")
            async for piece in super().chat(messages, **kwargs):
                yield piece

    llm = InterruptedLLM()
    agent = await Agent.build(
        AgentConfig(name="hint-interrupt", system_prompt="Test"),
        llm=llm,
        io="headless",
        pwd=tmp_path,
    )
    configure_skills(agent, tmp_path)
    await agent.start()
    try:
        turn = asyncio.create_task(agent.run("First", raise_on_error=False))
        await asyncio.wait_for(llm.started.wait(), 2)
        agent.skills.disable("inspect")
        if cancel:
            await agent.interrupt_and_wait()
        else:
            llm.release.set()
        first = await asyncio.wait_for(turn, 2)
        assert first.status == ("interrupted" if cancel else "error")
        assert (await agent.run("Next user")).text == "Done"
        assert agent.controller._skill_path_hint is None
        assert "## Skill Context" not in str(llm.last_messages)
    finally:
        llm.release.set()
        await agent.stop()
