"""Unit tests for :mod:`kohakuterrarium.prompt.plugins`.

Prompt composition is plugin-based; each plugin contributes one
prioritized section. Contract derived from docstrings + CLAUDE.md
"Prompt System Design":

- ``ToolListPlugin`` emits *name + one-line description* per tool,
  sorted alphabetically, and a pointer to the ``info`` tool. It must
  NOT emit full docs or call syntax.
- ``FrameworkHintsPlugin`` carries the call *syntax* (and only that for
  non-native; a short note for native).
- ``EnvInfoPlugin`` reports cwd / git / platform / date.
- ``ProjectInstructionsPlugin`` walks up to the git root, loading
  AGENTS.md / .kohaku.md / CLAUDE.md, deeper files appended later.
- ``create_plugin`` / ``get_default_plugins`` / ``get_swe_plugins``
  return the documented sets.
"""

import platform
from datetime import datetime

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.prompt.plugins import (
    BUILTIN_PLUGINS,
    EnvInfoPlugin,
    FrameworkHintsPlugin,
    PluginContext,
    ProjectInstructionsPlugin,
    ToolListPlugin,
    create_plugin,
    get_default_plugins,
    get_swe_plugins,
)


def _make_tool(name: str, description: str):
    class _T(BaseTool):
        @property
        def tool_name(self) -> str:
            return name

        @property
        def description(self) -> str:
            return description

        @property
        def execution_mode(self) -> ExecutionMode:
            return ExecutionMode.DIRECT

        async def _execute(self, args, **kwargs) -> ToolResult:
            return ToolResult(output="")

    return _T()


def _registry_with(*tools):
    reg = Registry()
    for t in tools:
        reg.register_tool(t)
    return reg


class TestToolListPlugin:
    def test_name_and_priority(self):
        p = ToolListPlugin()
        assert p.name == "tool_list"
        assert p.priority == 50

    def test_none_registry_yields_none(self):
        assert ToolListPlugin().get_content(PluginContext(registry=None)) is None

    def test_empty_registry_yields_none(self):
        assert ToolListPlugin().get_content(PluginContext(registry=Registry())) is None

    def test_lists_name_and_one_line_description(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = ToolListPlugin().get_content(PluginContext(registry=reg))
        assert "- `bash`: Execute shell commands" in out
        assert out.startswith("## Available Tools")

    def test_tools_sorted_alphabetically(self):
        reg = _registry_with(
            _make_tool("zoom", "z"),
            _make_tool("apex", "a"),
        )
        out = ToolListPlugin().get_content(PluginContext(registry=reg))
        assert out.index("`apex`") < out.index("`zoom`")

    def test_points_to_info_tool_for_full_docs(self):
        reg = _registry_with(_make_tool("read", "Read a file"))
        out = ToolListPlugin().get_content(PluginContext(registry=reg))
        assert "Use the `info` tool for full documentation" in out

    def test_does_not_embed_call_syntax(self):
        # CLAUDE.md: tool call SYNTAX comes from framework hints, not the list.
        reg = _registry_with(_make_tool("read", "Read a file"))
        out = ToolListPlugin().get_content(PluginContext(registry=reg))
        assert "[/read]" not in out
        assert "<read>" not in out


class TestFrameworkHintsPlugin:
    def test_name_and_priority(self):
        p = FrameworkHintsPlugin()
        assert p.name == "framework_hints"
        assert p.priority == 60

    def test_native_format_skips_syntax_examples(self):
        out = FrameworkHintsPlugin().get_content(PluginContext(tool_format="native"))
        assert "native function calling mechanism" in out
        assert "[/bash]" not in out
        assert "<bash" not in out

    def test_bracket_format_emits_bracket_syntax(self):
        out = FrameworkHintsPlugin().get_content(PluginContext(tool_format="bracket"))
        assert "[/bash]" in out
        assert "## Tool Call Syntax" in out

    def test_xml_format_emits_xml_syntax(self):
        out = FrameworkHintsPlugin().get_content(PluginContext(tool_format="xml"))
        assert "<bash>" in out
        assert "<read " in out

    def test_includes_info_command_example(self):
        out = FrameworkHintsPlugin().get_content(PluginContext(tool_format="bracket"))
        assert "[/info]" in out and "## Framework Commands" in out


class TestEnvInfoPlugin:
    def test_name_and_priority_is_early(self):
        p = EnvInfoPlugin()
        assert p.name == "env_info"
        assert p.priority == 10

    def test_reports_working_dir_platform_and_date(self, tmp_path):
        out = EnvInfoPlugin().get_content(PluginContext(working_dir=tmp_path))
        assert f"Working directory: {tmp_path}" in out
        assert f"Platform: {platform.system()}" in out
        assert f"Date: {datetime.now().strftime('%Y-%m-%d')}" in out

    def test_detects_non_git_directory(self, tmp_path):
        out = EnvInfoPlugin().get_content(PluginContext(working_dir=tmp_path))
        assert "Is git repo: No" in out

    def test_detects_git_directory(self, tmp_path):
        (tmp_path / ".git").mkdir()
        out = EnvInfoPlugin().get_content(PluginContext(working_dir=tmp_path))
        assert "Is git repo: Yes" in out


class TestProjectInstructionsPlugin:
    def test_name_and_priority(self):
        p = ProjectInstructionsPlugin()
        assert p.name == "project_instructions"
        assert p.priority == 20

    def test_no_instruction_files_yields_none(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert (
            ProjectInstructionsPlugin().get_content(PluginContext(working_dir=tmp_path))
            is None
        )

    def test_loads_claude_md_from_cwd(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "CLAUDE.md").write_text("project rules", encoding="utf-8")
        out = ProjectInstructionsPlugin().get_content(
            PluginContext(working_dir=tmp_path)
        )
        assert "## Project Instructions" in out
        assert "project rules" in out

    def test_deeper_file_appended_after_higher_level_file(self, tmp_path):
        # Repo root has a CLAUDE.md; a subdir has AGENTS.md. Walking stops
        # at the .git root; deeper (cwd) file must come *later* in output.
        (tmp_path / ".git").mkdir()
        (tmp_path / "CLAUDE.md").write_text("ROOT RULES", encoding="utf-8")
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "AGENTS.md").write_text("SUB RULES", encoding="utf-8")
        out = ProjectInstructionsPlugin().get_content(PluginContext(working_dir=sub))
        assert out.index("ROOT RULES") < out.index("SUB RULES")

    def test_unreadable_instruction_file_is_skipped_not_fatal(self, tmp_path):
        # A path named like an instruction file but unreadable (a dir)
        # must be skipped with a warning, not crash aggregation.
        (tmp_path / ".git").mkdir()
        (tmp_path / "CLAUDE.md").mkdir()  # exists() True, read_text() raises
        (tmp_path / "AGENTS.md").write_text("readable rules", encoding="utf-8")
        out = ProjectInstructionsPlugin().get_content(
            PluginContext(working_dir=tmp_path)
        )
        assert out is not None
        assert "readable rules" in out

    def test_walk_stops_at_git_root(self, tmp_path):
        # A CLAUDE.md *above* the git root must not be picked up.
        outer = tmp_path / "outer"
        repo = outer / "repo"
        repo.mkdir(parents=True)
        (repo / ".git").mkdir()
        (outer / "CLAUDE.md").write_text("OUTSIDE REPO", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("INSIDE REPO", encoding="utf-8")
        out = ProjectInstructionsPlugin().get_content(PluginContext(working_dir=repo))
        assert "INSIDE REPO" in out
        assert "OUTSIDE REPO" not in out


class TestPluginFactories:
    def test_create_plugin_known_name(self):
        assert isinstance(create_plugin("tool_list"), ToolListPlugin)
        assert isinstance(create_plugin("env_info"), EnvInfoPlugin)

    def test_create_plugin_unknown_returns_none(self):
        assert create_plugin("not_a_plugin") is None

    def test_builtin_plugins_registry_maps_all_four(self):
        assert set(BUILTIN_PLUGINS) == {
            "tool_list",
            "framework_hints",
            "env_info",
            "project_instructions",
        }

    def test_default_plugins_are_tool_list_and_hints(self):
        names = {p.name for p in get_default_plugins()}
        assert names == {"tool_list", "framework_hints"}

    def test_swe_plugins_add_env_and_project_instructions(self):
        names = {p.name for p in get_swe_plugins()}
        assert names == {
            "env_info",
            "project_instructions",
            "tool_list",
            "framework_hints",
        }
