"""
Tests for H.9 - ##info## / built-in skill readers strip YAML frontmatter.

Verifies that:
- ``InfoCommand`` strips frontmatter from agent-local override files.
- An override file without frontmatter passes through unchanged.
- ``get_builtin_tool_doc`` returns the body of a real built-in (``bash.md``)
  without its ``---...---`` preamble.
- A leading ``# Heading`` on the first content line is preserved after
  frontmatter is stripped.
"""

from dataclasses import dataclass
from pathlib import Path

from kohakuterrarium.builtin_skills import (
    BUILTIN_SKILLS_DIR,
    get_builtin_tool_doc,
    read_skill_body,
)
from kohakuterrarium.commands.read import InfoCommand


@dataclass
class _FakeContext:
    """Minimal context exposing only ``agent_path``."""

    agent_path: Path


def _write_override(agent_path: Path, kind: str, name: str, content: str) -> Path:
    """Write an override file under ``prompts/{kind}/{name}.md``."""
    target_dir = agent_path / "prompts" / kind
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


class TestInfoCommandFrontmatterStrip:
    async def test_override_with_frontmatter_strips_yaml(self, tmp_path: Path):
        """Override file with frontmatter returns only the body."""
        override_content = (
            "---\n"
            "name: my_tool\n"
            "description: Custom tool doc\n"
            "category: custom\n"
            "---\n"
            "\n"
            "# My Tool\n"
            "\n"
            "Custom content body.\n"
        )
        _write_override(tmp_path, "tools", "my_tool", override_content)

        command = InfoCommand()
        result = await command.execute("my_tool", _FakeContext(agent_path=tmp_path))

        assert result.success
        assert not result.content.startswith("---")
        assert "name: my_tool" not in result.content
        assert "description: Custom tool doc" not in result.content
        # The body must still be there, heading intact.
        assert "# My Tool" in result.content
        assert "Custom content body." in result.content

    async def test_override_without_frontmatter_unchanged(self, tmp_path: Path):
        """Override file without frontmatter passes through unchanged (modulo strip)."""
        raw = "# Plain Doc\n\nJust a plain markdown file.\n"
        _write_override(tmp_path, "tools", "plain_tool", raw)

        command = InfoCommand()
        result = await command.execute("plain_tool", _FakeContext(agent_path=tmp_path))

        assert result.success
        # load_skill_doc strips outer whitespace; content must be preserved verbatim
        # aside from surrounding whitespace.
        assert result.content.strip() == raw.strip()
        assert result.content.startswith("# Plain Doc")

    async def test_override_subagent_strips_frontmatter(self, tmp_path: Path):
        """Override subagent docs also get frontmatter stripped."""
        override_content = (
            "---\n"
            "name: my_subagent\n"
            "description: Custom subagent\n"
            "---\n"
            "\n"
            "# My Subagent\n"
            "\n"
            "Body text.\n"
        )
        _write_override(tmp_path, "subagents", "my_subagent", override_content)

        command = InfoCommand()
        result = await command.execute("my_subagent", _FakeContext(agent_path=tmp_path))

        assert result.success
        assert not result.content.lstrip().startswith("---")
        assert "name: my_subagent" not in result.content
        assert "# My Subagent" in result.content

    async def test_leading_heading_preserved_after_strip(self, tmp_path: Path):
        """After frontmatter is stripped, the first real content line (a heading) is preserved."""
        override_content = (
            "---\n"
            "name: heading_first\n"
            "description: Heading as first body line\n"
            "---\n"
            "# First Heading\n"
            "\n"
            "Body.\n"
        )
        _write_override(tmp_path, "tools", "heading_first", override_content)

        command = InfoCommand()
        result = await command.execute(
            "heading_first", _FakeContext(agent_path=tmp_path)
        )

        assert result.success
        # First non-whitespace line of content must be the heading.
        first_line = result.content.lstrip().splitlines()[0]
        assert first_line == "# First Heading"


class TestBuiltinSkillBodyOnly:
    def test_bash_doc_has_no_frontmatter(self):
        """`get_builtin_tool_doc('bash')` returns the body only."""
        # Sanity: the built-in file actually carries frontmatter.
        raw = (BUILTIN_SKILLS_DIR / "tools" / "bash.md").read_text(encoding="utf-8")
        assert raw.lstrip().startswith("---"), "precondition: bash.md has frontmatter"

        doc = get_builtin_tool_doc("bash")

        assert doc is not None
        assert not doc.lstrip().startswith("---")
        # YAML field keys must not leak into the doc body.
        assert "\nname: bash" not in doc
        assert "\ndescription:" not in doc
        # The actual content should still be there.
        assert "# bash" in doc

    def test_missing_tool_returns_none(self):
        """Requesting an unknown built-in tool returns None."""
        assert get_builtin_tool_doc("this_tool_does_not_exist_zzzz") is None


class TestReadSkillBody:
    def test_no_frontmatter_returns_full_content(self, tmp_path: Path):
        """A file without frontmatter round-trips through read_skill_body."""
        path = tmp_path / "plain.md"
        body = "# Heading\n\nJust body.\n"
        path.write_text(body, encoding="utf-8")

        assert read_skill_body(path) == body.strip()

    def test_missing_path_returns_none(self, tmp_path: Path):
        """A non-existent path returns None, not a raise."""
        assert read_skill_body(tmp_path / "does_not_exist.md") is None

    def test_malformed_frontmatter_degrades_gracefully(self, tmp_path: Path):
        """A malformed YAML frontmatter does not raise; caller still gets a string."""
        path = tmp_path / "bad.md"
        # Open ``---`` with no closing ``---`` -> load_skill_doc treats whole
        # file as content (no frontmatter match). Should still return body.
        raw = "---\nname: broken\n\nno closer here\n"
        path.write_text(raw, encoding="utf-8")

        result = read_skill_body(path)

        assert result is not None
        # At minimum, the real content fragment survives.
        assert "no closer here" in result
