"""Unit tests for the ``think`` builtin tool.

Covers:
- Direct registration via @register_builtin decorator
- tool_name / description / execution_mode properties
- Happy path: thought echoed back as ToolResult.output
- Fallback arg keys (body, content)
- Empty / whitespace-only thought yields an error ToolResult
- Catalog lookup round-trips to a fresh ThinkTool instance
- Skill manifest (builtin_skills/tools/think.md) is present and wired up via
  get_full_documentation()
"""

from kohakuterrarium.builtin_skills import get_builtin_tool_doc
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool, is_builtin_tool
from kohakuterrarium.builtins.tools.think import ThinkTool
from kohakuterrarium.modules.tool.base import ExecutionMode


class TestThinkToolProperties:
    """Static properties the framework relies on for prompt aggregation."""

    def test_tool_name(self):
        tool = ThinkTool()
        assert tool.tool_name == "think"

    def test_description_is_non_empty(self):
        tool = ThinkTool()
        assert tool.description
        assert isinstance(tool.description, str)

    def test_execution_mode_is_direct(self):
        tool = ThinkTool()
        assert tool.execution_mode is ExecutionMode.DIRECT

    def test_does_not_need_context(self):
        assert ThinkTool.needs_context is False


class TestThinkToolRegistration:
    """Catalog lookup and is_builtin checks."""

    def test_registered_in_catalog(self):
        assert is_builtin_tool("think") is True

    def test_get_builtin_returns_think_tool(self):
        instance = get_builtin_tool("think")
        assert instance is not None
        assert isinstance(instance, ThinkTool)


class TestThinkToolExecution:
    """Execution behaviour: echo the thought, reject empties."""

    async def test_echoes_thought_arg(self):
        tool = ThinkTool()
        result = await tool.execute({"thought": "Plan: do the thing."})
        assert result.success
        assert result.exit_code == 0
        assert result.output == "Plan: do the thing."

    async def test_falls_back_to_body_arg(self):
        tool = ThinkTool()
        result = await tool.execute({"body": "Body-style content."})
        assert result.success
        assert result.output == "Body-style content."

    async def test_falls_back_to_content_arg(self):
        tool = ThinkTool()
        result = await tool.execute({"content": "Content-style content."})
        assert result.success
        assert result.output == "Content-style content."

    async def test_prefers_thought_over_body(self):
        tool = ThinkTool()
        result = await tool.execute(
            {"thought": "primary", "body": "secondary", "content": "tertiary"}
        )
        assert result.success
        assert result.output == "primary"

    async def test_strips_surrounding_whitespace(self):
        tool = ThinkTool()
        result = await tool.execute({"thought": "  indented thought \n"})
        assert result.success
        assert result.output == "indented thought"

    async def test_preserves_multiline_body(self):
        tool = ThinkTool()
        thought = "Line one.\nLine two.\nLine three."
        result = await tool.execute({"thought": thought})
        assert result.success
        assert result.output == thought

    async def test_empty_thought_returns_error(self):
        tool = ThinkTool()
        result = await tool.execute({"thought": ""})
        assert not result.success
        assert result.error is not None
        assert result.exit_code == 1

    async def test_whitespace_only_thought_returns_error(self):
        tool = ThinkTool()
        result = await tool.execute({"thought": "   \n\t  "})
        assert not result.success
        assert "required" in result.error.lower()

    async def test_no_args_returns_error(self):
        tool = ThinkTool()
        result = await tool.execute({})
        assert not result.success
        assert result.error is not None

    async def test_non_string_thought_does_not_raise(self):
        """Non-string ``thought`` should fail cleanly rather than raise.

        If an upstream parser ever hands us a non-string value, the tool
        must still behave like a tool (return a ``ToolResult``), not blow
        up with an uncaught TypeError mid-stream.
        """
        tool = ThinkTool()
        result = await tool.execute({"thought": 123})
        # Either rejected as empty-ish or accepted. Must not raise.
        assert result is not None


class TestThinkToolDocumentation:
    """Skill manifest is present and wired to ``info``/``get_full_documentation``."""

    def test_builtin_skill_manifest_exists(self):
        doc = get_builtin_tool_doc("think")
        assert doc is not None
        assert "# think" in doc

    def test_get_full_documentation_returns_skill_manifest(self):
        tool = ThinkTool()
        doc = tool.get_full_documentation()
        assert "# think" in doc
        # Must include the key sections promised to users
        assert "WHEN TO USE" in doc
        assert "Arguments" in doc
