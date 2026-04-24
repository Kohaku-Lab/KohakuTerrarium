"""Native tool surface for procedural skills."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.tools.info import InfoTool
from kohakuterrarium.builtins.tools.skill import SkillTool
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.core.session import Session
from kohakuterrarium.llm.tools import build_tool_schemas
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.skills import Skill, SkillRegistry


def _skill(name: str, **kw) -> Skill:
    return Skill(
        name=name,
        description=kw.pop("description", f"{name}-desc"),
        body=kw.pop("body", f"body of {name}"),
        origin=kw.pop("origin", "user"),
        **kw,
    )


def _context(
    registry: SkillRegistry, *, include_session_extra: bool = True
) -> ToolContext:
    session = Session(key="test-skills")
    if include_session_extra:
        session.extra["skills_registry"] = registry
    agent = SimpleNamespace(skills=registry, registry=Registry())
    return ToolContext(
        agent_name="test",
        session=session,
        working_dir=__import__("pathlib").Path.cwd(),
        agent=agent,
    )


def test_skill_schema_is_native_callable():
    registry = Registry()
    registry.register_tool(SkillTool())

    schemas = {schema.name: schema for schema in build_tool_schemas(registry)}

    assert "skill" in schemas
    params = schemas["skill"].parameters
    assert params["required"] == ["name"]
    assert "arguments" in params["properties"]


@pytest.mark.asyncio
async def test_skill_tool_returns_enabled_skill_body_and_args():
    registry = SkillRegistry()
    registry.add(_skill("git-commit-flow", body="commit safely"))

    result = await SkillTool().execute(
        {"name": "git-commit-flow", "arguments": "stage only my files"},
        _context(registry),
    )

    assert result.error is None
    assert "commit safely" in result.output
    assert "stage only my files" in result.output


@pytest.mark.asyncio
async def test_skill_tool_rejects_disabled_skill():
    registry = SkillRegistry()
    registry.add(_skill("quiet", enabled=False))

    result = await SkillTool().execute({"name": "quiet"}, _context(registry))

    assert result.error
    assert "disabled" in result.error


@pytest.mark.asyncio
async def test_info_tool_falls_through_to_runtime_skill():
    registry = SkillRegistry()
    registry.add(_skill("pdf-merge", body="merge instructions"))

    result = await InfoTool().execute({"name": "pdf-merge"}, _context(registry))

    assert result.error is None
    assert "merge instructions" in result.output
    assert "--- Skill: pdf-merge ---" in result.output


@pytest.mark.asyncio
async def test_info_tool_finds_runtime_skill_from_tool_context_agent():
    registry = SkillRegistry()
    registry.add(_skill("git-commit-flow", body="commit instructions"))

    result = await InfoTool().execute(
        {"name": "git-commit-flow"},
        _context(registry, include_session_extra=False),
    )

    assert result.error is None
    assert "commit instructions" in result.output
    assert "--- Skill: git-commit-flow ---" in result.output
