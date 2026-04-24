"""Unit tests for the ``/skill`` user command and ``/<skill-name>`` wildcard."""

from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from kohakuterrarium.builtins.user_commands import get_builtin_user_command
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config_types import AgentConfig, ToolConfigItem
from kohakuterrarium.modules.input.base import BaseInputModule
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.skills import Skill, SkillRegistry, build_user_skill_turn
from kohakuterrarium.testing import ScriptedLLM


def _skill(name: str, **kw) -> Skill:
    return Skill(
        name=name,
        description=kw.pop("description", f"{name}-desc"),
        body=kw.pop("body", f"body of {name}"),
        origin=kw.pop("origin", "user"),
        **kw,
    )


@dataclass
class _FakeAgent:
    skills: Any = None
    output: Any = None


@pytest.mark.asyncio
async def test_slash_skill_list_outputs_skills():
    reg = SkillRegistry()
    reg.add(_skill("foo"))
    reg.add(_skill("bar", enabled=False))

    cmd = get_builtin_user_command("skill")
    ctx = UserCommandContext(agent=_FakeAgent(skills=reg))
    result = await cmd.execute("list", ctx)
    assert result.error is None
    assert "foo" in result.output
    assert "bar" in result.output
    assert "enabled" in result.output
    assert "disabled" in result.output


@pytest.mark.asyncio
async def test_slash_skill_enable_disable_roundtrip():
    reg = SkillRegistry()
    reg.add(_skill("foo", enabled=False))
    cmd = get_builtin_user_command("skill")
    ctx = UserCommandContext(agent=_FakeAgent(skills=reg))

    out = await cmd.execute("enable foo", ctx)
    assert out.error is None
    assert reg.get("foo").enabled is True

    out = await cmd.execute("disable foo", ctx)
    assert out.error is None
    assert reg.get("foo").enabled is False


@pytest.mark.asyncio
async def test_slash_skill_toggle():
    reg = SkillRegistry()
    reg.add(_skill("foo", enabled=True))
    cmd = get_builtin_user_command("skill")
    ctx = UserCommandContext(agent=_FakeAgent(skills=reg))

    out = await cmd.execute("toggle foo", ctx)
    assert out.error is None
    assert reg.get("foo").enabled is False


@pytest.mark.asyncio
async def test_slash_skill_show_emits_body():
    reg = SkillRegistry()
    reg.add(_skill("foo", body="full body here"))
    cmd = get_builtin_user_command("skill")
    ctx = UserCommandContext(agent=_FakeAgent(skills=reg))
    out = await cmd.execute("show foo", ctx)
    assert out.error is None
    assert "full body here" in out.output
    assert "Origin" in out.output


@pytest.mark.asyncio
async def test_slash_skill_unknown_name_errors():
    cmd = get_builtin_user_command("skill")
    ctx = UserCommandContext(agent=_FakeAgent(skills=SkillRegistry()))
    out = await cmd.execute("enable nothing", ctx)
    assert out.error and "Unknown" in out.error


@pytest.mark.asyncio
async def test_slash_skill_no_agent_errors():
    cmd = get_builtin_user_command("skill")
    ctx = UserCommandContext(agent=None)
    out = await cmd.execute("list", ctx)
    assert out.error


# ---------------------------------------------------------------------------
# Wildcard /<skill-name> dispatcher
# ---------------------------------------------------------------------------


class _TestInput(BaseInputModule):
    """Concrete input module for BaseInputModule tests."""

    async def get_input(self):  # pragma: no cover — unused
        return None


@pytest.mark.asyncio
async def test_wildcard_slash_resolves_to_registered_skill():
    reg = SkillRegistry()
    reg.add(_skill("pdf-merge", body="merge pdfs"))
    module = _TestInput()
    agent = _FakeAgent(skills=reg)
    module.set_user_commands({}, UserCommandContext(agent=agent))

    result = await module.try_user_command("/pdf-merge one.pdf two.pdf")
    assert result is not None
    assert result.error is None
    assert "merge pdfs" in result.output
    assert "pdf-merge" in result.output
    assert "one.pdf two.pdf" in result.output
    # Wildcard skill results must NOT be consumed — they feed the LLM
    # as a user turn, per Qd's user-invoke semantics.
    assert result.consumed is False


@pytest.mark.asyncio
async def test_wildcard_slash_defers_to_registered_command():
    """If the slash name matches a real command, that command wins."""

    class _Dummy:
        name = "dummy"
        aliases: list[str] = []
        description = "d"

        async def execute(self, args, ctx):
            from kohakuterrarium.modules.user_command.base import UserCommandResult

            return UserCommandResult(output=f"real-dummy:{args}")

    reg = SkillRegistry()
    reg.add(_skill("dummy", body="skill body"))
    module = _TestInput()
    module.set_user_commands(
        {"dummy": _Dummy()}, UserCommandContext(agent=_FakeAgent(skills=reg))
    )

    result = await module.try_user_command("/dummy hi")
    assert result is not None
    assert result.output == "real-dummy:hi"
    assert "skill body" not in result.output


@pytest.mark.asyncio
async def test_wildcard_slash_unknown_name_returns_none():
    reg = SkillRegistry()
    module = _TestInput()
    module.set_user_commands({}, UserCommandContext(agent=_FakeAgent(skills=reg)))
    assert await module.try_user_command("/nothing-like-this") is None


@pytest.mark.asyncio
async def test_wildcard_slash_disabled_skill_errors():
    reg = SkillRegistry()
    reg.add(_skill("pdf-merge", enabled=False))
    module = _TestInput()
    module.set_user_commands({}, UserCommandContext(agent=_FakeAgent(skills=reg)))
    result = await module.try_user_command("/pdf-merge")
    assert result is not None
    assert result.error
    assert "disabled" in result.error


def test_build_user_skill_turn_with_args():
    skill = _skill("foo", body="procedure body")
    turn = build_user_skill_turn(skill, "hello world")
    assert "foo" in turn
    assert "procedure body" in turn
    assert "hello world" in turn


def test_build_user_skill_turn_without_args_omits_line():
    skill = _skill("foo", body="proc")
    turn = build_user_skill_turn(skill, "")
    assert "Arguments the user provided" not in turn


@pytest.mark.asyncio
async def test_programmatic_slash_skill_injects_skill_turn_for_frontend_path():
    llm = ScriptedLLM(["done"])
    config = AgentConfig(
        name="skill-slash-agent",
        model="test-model",
        api_key_env="TEST_API_KEY",
        tool_format="native",
        tools=[ToolConfigItem(name="info")],
    )

    discovered = [_skill("pdf-merge", body="merge pdf instructions")]
    with (
        patch(
            "kohakuterrarium.bootstrap.agent_init.create_llm_provider",
            return_value=llm,
        ),
        patch(
            "kohakuterrarium.bootstrap.agent_init.discover_skills",
            return_value=discovered,
        ),
    ):
        agent = Agent(config)

    await agent.start()
    try:
        await agent.inject_input("/pdf-merge a.pdf b.pdf", source="chat")
    finally:
        await agent.stop()

    assert "merge pdf instructions" in llm.last_user_message
    assert "a.pdf b.pdf" in llm.last_user_message


def test_agent_with_skills_exposes_skill_tool_to_controller_and_native_schema():
    llm = ScriptedLLM(["done"])
    config = AgentConfig(
        name="skill-tool-agent",
        model="test-model",
        api_key_env="TEST_API_KEY",
        tool_format="native",
        tools=[ToolConfigItem(name="info")],
    )

    discovered = [_skill("git-commit-flow", body="commit instructions")]
    with (
        patch(
            "kohakuterrarium.bootstrap.agent_init.create_llm_provider",
            return_value=llm,
        ),
        patch(
            "kohakuterrarium.bootstrap.agent_init.discover_skills",
            return_value=discovered,
        ),
    ):
        agent = Agent(config)

    assert "skill" in agent.registry.list_tools()
    assert "skill" in agent.executor.list_tools()
    assert "skill" in agent.controller._commands
    assert "- `skill`:" in agent.controller.config.system_prompt
    assert {schema.name for schema in agent.controller._get_native_tool_schemas()} >= {
        "info",
        "skill",
    }
