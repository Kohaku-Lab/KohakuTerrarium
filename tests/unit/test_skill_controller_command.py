"""Unit tests for the ``##skill <name>##`` controller command."""

import pytest

from kohakuterrarium.skills import Skill, SkillCommand, SkillRegistry


def _skill(name: str, **kw) -> Skill:
    return Skill(
        name=name,
        description=kw.pop("description", f"{name}-desc"),
        body=kw.pop("body", f"body of {name}"),
        origin=kw.pop("origin", "user"),
        **kw,
    )


@pytest.mark.asyncio
async def test_skill_command_returns_body_for_enabled_skill():
    reg = SkillRegistry()
    reg.add(_skill("pdf-merge", body="merge pdfs safely"))
    cmd = SkillCommand(reg)
    result = await cmd.execute("pdf-merge", context=None)
    assert result.error is None
    assert "merge pdfs safely" in result.content
    assert "pdf-merge" in result.content


@pytest.mark.asyncio
async def test_skill_command_requires_name():
    reg = SkillRegistry()
    cmd = SkillCommand(reg)
    result = await cmd.execute("", context=None)
    assert result.error
    assert "No skill name" in result.error


@pytest.mark.asyncio
async def test_skill_command_unknown_skill():
    reg = SkillRegistry()
    reg.add(_skill("foo"))
    cmd = SkillCommand(reg)
    result = await cmd.execute("bar", context=None)
    assert result.error
    assert "Unknown skill" in result.error


@pytest.mark.asyncio
async def test_skill_command_disabled_skill_is_rejected():
    reg = SkillRegistry()
    reg.add(_skill("foo", enabled=False))
    cmd = SkillCommand(reg)
    result = await cmd.execute("foo", context=None)
    assert result.error
    assert "disabled" in result.error


@pytest.mark.asyncio
async def test_skill_command_passes_arguments_into_output():
    reg = SkillRegistry()
    reg.add(_skill("greet", body="say hello"))
    cmd = SkillCommand(reg)
    result = await cmd.execute("greet  world from user", context=None)
    assert result.error is None
    assert "world from user" in result.content


@pytest.mark.asyncio
async def test_disable_model_invocation_still_callable_explicitly():
    # spec 4.4: hidden from auto-invoke index but explicit model call works.
    reg = SkillRegistry()
    reg.add(_skill("quiet", disable_model_invocation=True))
    cmd = SkillCommand(reg)
    result = await cmd.execute("quiet", context=None)
    assert result.error is None
    assert "body of quiet" in result.content
