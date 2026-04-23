"""Tests for kt_biome.tools.skill_manage — SkillManageTool."""

from __future__ import annotations

from pathlib import Path

from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.prompt.skill_loader import parse_frontmatter

from kt_biome.tools import _skill_activity
from kt_biome.tools.skill_manage import SkillManageTool


def _make_context(working_dir: Path) -> ToolContext:
    return ToolContext(
        agent_name="test_agent",
        session=None,
        working_dir=working_dir,
    )


def _make_tool(tmp_path: Path) -> SkillManageTool:
    return SkillManageTool(
        options={
            "default_scope": "user",
            "user_root": str(tmp_path / "user_skills"),
            "project_root": str(tmp_path / "project_skills"),
            "allow_overwrite": False,
        }
    )


class TestSkillManageCreate:
    async def test_create_writes_skill_with_frontmatter(self, tmp_path: Path):
        _skill_activity.clear()
        tool = _make_tool(tmp_path)
        context = _make_context(tmp_path)

        result = await tool.execute(
            {
                "action": "create",
                "name": "demo-skill",
                "description": "Demo skill for tests",
                "body": "# Demo\n\nStep 1. Do the thing.\n",
            },
            context=context,
        )
        assert result.success, f"create failed: {result.error}"

        target = tmp_path / "user_skills" / "demo-skill" / "SKILL.md"
        assert target.exists()
        text = target.read_text(encoding="utf-8")
        metadata, body = parse_frontmatter(text)
        assert metadata["name"] == "demo-skill"
        assert metadata["description"] == "Demo skill for tests"
        assert metadata["license"] == "internal"
        assert "created_at" in metadata
        assert "Step 1. Do the thing." in body

        # Tool's success should be recorded for the nudge coordination.
        assert _skill_activity.last_used_at("test_agent") is not None

    async def test_create_refuses_overwrite(self, tmp_path: Path):
        _skill_activity.clear()
        tool = _make_tool(tmp_path)
        context = _make_context(tmp_path)

        first = await tool.execute(
            {
                "action": "create",
                "name": "dup",
                "description": "first",
                "body": "body one",
            },
            context=context,
        )
        assert first.success

        second = await tool.execute(
            {
                "action": "create",
                "name": "dup",
                "description": "second",
                "body": "body two",
            },
            context=context,
        )
        assert not second.success
        assert "already exists" in (second.error or "")

    async def test_create_rejects_invalid_name(self, tmp_path: Path):
        _skill_activity.clear()
        tool = _make_tool(tmp_path)
        context = _make_context(tmp_path)

        result = await tool.execute(
            {
                "action": "create",
                "name": "Bad_Name",
                "description": "x",
                "body": "",
            },
            context=context,
        )
        assert not result.success
        assert "Invalid skill name" in (result.error or "")


class TestSkillManagePatch:
    async def test_patch_replace_replaces_body_preserves_frontmatter(
        self, tmp_path: Path
    ):
        _skill_activity.clear()
        tool = _make_tool(tmp_path)
        context = _make_context(tmp_path)

        await tool.execute(
            {
                "action": "create",
                "name": "patcher",
                "description": "Thing that patches",
                "body": "Original body line.\n",
            },
            context=context,
        )
        target = tmp_path / "user_skills" / "patcher" / "SKILL.md"
        original_meta, _ = parse_frontmatter(target.read_text(encoding="utf-8"))

        result = await tool.execute(
            {
                "action": "patch",
                "name": "patcher",
                "new_body": "Replaced body text.\n",
                "merge_mode": "replace",
            },
            context=context,
        )
        assert result.success, f"patch failed: {result.error}"

        metadata, body = parse_frontmatter(target.read_text(encoding="utf-8"))
        # Frontmatter preserved + updated_at added
        assert metadata["name"] == original_meta["name"]
        assert metadata["description"] == original_meta["description"]
        assert metadata["license"] == "internal"
        assert metadata["created_at"] == original_meta["created_at"]
        assert "updated_at" in metadata
        # Body fully replaced
        assert "Replaced body text." in body
        assert "Original body line." not in body

    async def test_patch_append_adds_heading(self, tmp_path: Path):
        _skill_activity.clear()
        tool = _make_tool(tmp_path)
        context = _make_context(tmp_path)

        await tool.execute(
            {
                "action": "create",
                "name": "appender",
                "description": "Thing that appends",
                "body": "Initial content.\n",
            },
            context=context,
        )
        result = await tool.execute(
            {
                "action": "patch",
                "name": "appender",
                "new_body": "Follow-up note.",
                "merge_mode": "append",
            },
            context=context,
        )
        assert result.success, f"append failed: {result.error}"

        target = tmp_path / "user_skills" / "appender" / "SKILL.md"
        _, body = parse_frontmatter(target.read_text(encoding="utf-8"))
        assert "Initial content." in body
        assert "## Update " in body
        assert "Follow-up note." in body


class TestSkillManageView:
    async def test_view_round_trips_created_file(self, tmp_path: Path):
        _skill_activity.clear()
        tool = _make_tool(tmp_path)
        context = _make_context(tmp_path)

        await tool.execute(
            {
                "action": "create",
                "name": "viewer",
                "description": "Can be viewed",
                "body": "Step A.\nStep B.\n",
            },
            context=context,
        )
        result = await tool.execute(
            {"action": "view", "name": "viewer"},
            context=context,
        )
        assert result.success, f"view failed: {result.error}"

        target = tmp_path / "user_skills" / "viewer" / "SKILL.md"
        expected = target.read_text(encoding="utf-8")
        assert result.get_text_output() == expected
        assert "name: viewer" in result.get_text_output()
        assert "Step A." in result.get_text_output()

    async def test_view_missing_returns_error(self, tmp_path: Path):
        _skill_activity.clear()
        tool = _make_tool(tmp_path)
        context = _make_context(tmp_path)

        result = await tool.execute(
            {"action": "view", "name": "ghost"},
            context=context,
        )
        assert not result.success
        assert "not found" in (result.error or "")


class TestSkillManageProjectScope:
    async def test_project_scope_uses_working_dir(self, tmp_path: Path):
        _skill_activity.clear()
        tool = SkillManageTool(
            options={
                "default_scope": "user",
                "user_root": str(tmp_path / "user_skills"),
                "project_root": ".kt/skills",  # relative — resolved vs cwd
                "allow_overwrite": False,
            }
        )
        context = _make_context(tmp_path)

        result = await tool.execute(
            {
                "action": "create",
                "name": "proj",
                "description": "Project-scoped",
                "body": "local use only",
                "scope": "project",
            },
            context=context,
        )
        assert result.success, f"project create failed: {result.error}"
        assert (tmp_path / ".kt" / "skills" / "proj" / "SKILL.md").exists()
