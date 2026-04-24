"""``##info <skill_name>##`` must fall through to procedural skills when the
name doesn't match a registered tool / sub-agent."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.commands.read import InfoCommand
from kohakuterrarium.skills import Skill, SkillRegistry


def _skill(name: str, **kw) -> Skill:
    return Skill(
        name=name,
        description=kw.pop("description", f"{name}-desc"),
        body=kw.pop("body", f"how to use {name}"),
        origin=kw.pop("origin", "user"),
        **kw,
    )


def _context(reg: SkillRegistry | None = None):
    """Build a minimal ControllerContext-shaped object for tests."""
    return SimpleNamespace(
        agent_path=None,
        get_tool_info=lambda _name: None,
        get_tool=None,
        get_subagent_info=lambda _name: None,
        skills_registry=reg,
    )


@pytest.mark.asyncio
async def test_info_falls_through_to_skill_body():
    reg = SkillRegistry()
    reg.add(_skill("pdf-merge", body="Merge PDFs with qpdf."))
    cmd = InfoCommand()
    result = await cmd.execute("pdf-merge", _context(reg))
    assert result.error is None
    assert "Merge PDFs with qpdf." in result.content
    assert "pdf-merge" in result.content


@pytest.mark.asyncio
async def test_info_skill_fallback_includes_preamble():
    reg = SkillRegistry()
    reg.add(_skill("foo", body="body-body"))
    cmd = InfoCommand()
    result = await cmd.execute("foo", _context(reg))
    # Preamble shape documented in _render_skill_info.
    assert "--- Skill:" in result.content
    assert "Origin:" in result.content


@pytest.mark.asyncio
async def test_info_unknown_name_when_no_skill_or_tool():
    cmd = InfoCommand()
    result = await cmd.execute("does-not-exist", _context(SkillRegistry()))
    assert result.error
    assert "Not found" in result.error


@pytest.mark.asyncio
async def test_info_returns_skill_with_paths_metadata():
    reg = SkillRegistry()
    reg.add(_skill("pdf", paths=["*.pdf"]))
    cmd = InfoCommand()
    result = await cmd.execute("pdf", _context(reg))
    assert "Paths" in result.content
    assert "*.pdf" in result.content


@pytest.mark.asyncio
async def test_info_registry_lookup_from_session_extra():
    reg = SkillRegistry()
    reg.add(_skill("foo", body="from-session"))
    # Simulate context that carries registry via session.extra.
    ctx = SimpleNamespace(
        agent_path=None,
        get_tool_info=lambda _n: None,
        get_tool=None,
        get_subagent_info=lambda _n: None,
        session=SimpleNamespace(extra={"skills_registry": reg}),
    )
    cmd = InfoCommand()
    result = await cmd.execute("foo", ctx)
    assert result.error is None
    assert "from-session" in result.content
