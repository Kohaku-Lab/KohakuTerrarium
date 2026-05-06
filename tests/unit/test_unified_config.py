"""Tests for unified creature config and format-aware prompts."""

import re
from pathlib import Path

import pytest

from kohakuterrarium.parsing.format import (
    BRACKET_FORMAT,
    XML_FORMAT,
    ToolCallFormat,
    format_tool_call_example,
)
from kohakuterrarium.terrarium.config import load_terrarium_config

# ---------------------------------------------------------------------------
# format_tool_call_example
# ---------------------------------------------------------------------------


class TestFormatToolCallExample:
    """Test format_tool_call_example generates correct syntax for any format."""

    def test_bracket_simple(self):
        result = format_tool_call_example(BRACKET_FORMAT, "read", {"path": "f.py"})
        assert result == "[/read]\n@@path=f.py\n[read/]"

    def test_bracket_with_body(self):
        result = format_tool_call_example(BRACKET_FORMAT, "bash", body="echo hello")
        assert result == "[/bash]\necho hello\n[bash/]"

    def test_bracket_with_args_and_body(self):
        result = format_tool_call_example(
            BRACKET_FORMAT, "write", {"path": "out.txt"}, "content"
        )
        assert "[/write]" in result
        assert "@@path=out.txt" in result
        assert "content" in result
        assert "[write/]" in result

    def test_xml_simple(self):
        result = format_tool_call_example(XML_FORMAT, "read", {"path": "f.py"})
        assert '<read path="f.py">' in result
        assert "</read>" in result

    def test_xml_with_body(self):
        result = format_tool_call_example(XML_FORMAT, "bash", body="echo hello")
        assert "<bash>" in result
        assert "echo hello" in result
        assert "</bash>" in result

    def test_xml_multiple_args(self):
        result = format_tool_call_example(
            XML_FORMAT, "grep", {"pattern": "TODO", "path": "src/"}
        )
        assert "<grep" in result
        assert 'pattern="TODO"' in result
        assert 'path="src/"' in result
        assert "</grep>" in result

    def test_custom_format(self):
        """Custom format with different delimiters."""
        custom = ToolCallFormat(
            start_char="{",
            end_char="}",
            slash_means_open=True,
            arg_style="line",
            arg_prefix="--",
            arg_kv_sep=":",
        )
        result = format_tool_call_example(custom, "read", {"path": "f.py"})
        assert "{/read}" in result
        assert "--path:f.py" in result
        assert "{read/}" in result

    def test_no_args_no_body(self):
        result = format_tool_call_example(BRACKET_FORMAT, "jobs")
        assert result == "[/jobs]\n[jobs/]"


# ---------------------------------------------------------------------------
# Unified creature config in terrarium
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SWE_TEAM_DIR = PROJECT_ROOT / "terrariums" / "swe_team"
CODE_REVIEW_DIR = PROJECT_ROOT / "examples" / "terrariums" / "code_review_team"


class TestUnifiedCreatureConfig:
    """Terrarium creatures use the same config format as standalone."""

    @pytest.mark.skipif(
        not SWE_TEAM_DIR.exists(), reason="terrariums/swe_team not present"
    )
    def test_swe_team_creatures_have_config_data(self):
        config = load_terrarium_config(SWE_TEAM_DIR)
        for creature in config.creatures:
            assert isinstance(creature.config_data, dict)
            assert "base_config" in creature.config_data

    @pytest.mark.skipif(
        not SWE_TEAM_DIR.exists(), reason="terrariums/swe_team not present"
    )
    def test_swe_team_has_root(self):
        config = load_terrarium_config(SWE_TEAM_DIR)
        assert config.root is not None
        assert config.root.config_data.get("base_config") == "creatures/root"

    def test_code_review_team_has_root(self):
        config = load_terrarium_config(CODE_REVIEW_DIR)
        assert config.root is not None
        assert isinstance(config.root.config_data, dict)
        assert "base_config" in config.root.config_data

    def test_code_review_team_has_three_creatures(self):
        config = load_terrarium_config(CODE_REVIEW_DIR)
        assert len(config.creatures) == 3
        names = {c.name for c in config.creatures}
        assert names == {"developer", "reviewer", "tester"}

    @pytest.mark.skipif(
        not SWE_TEAM_DIR.exists(), reason="terrariums/swe_team not present"
    )
    def test_creature_config_data_matches_standalone_format(self):
        """A creature in terrarium has the same keys as a standalone config."""
        config = load_terrarium_config(SWE_TEAM_DIR)
        swe = config.creatures[0]
        # Must have base_config (standard agent inheritance)
        assert "base_config" in swe.config_data
        # Must NOT have terrarium-only fields in config_data
        assert "channels" not in swe.config_data
        assert "output_log" not in swe.config_data

    def test_backward_compat_config_key_to_base_config(self):
        """Old 'config:' key auto-converts to 'base_config:'."""
        from kohakuterrarium.terrarium.config import _parse_creature

        data = {
            "name": "test",
            "config": "./creatures/swe",
            "channels": {"listen": ["ch1"]},
        }
        creature = _parse_creature(data, Path("/fake"))
        assert "base_config" in creature.config_data
        assert creature.config_data["base_config"] == "./creatures/swe"
        assert "config" not in creature.config_data

    def test_creature_can_override_model(self):
        """Inline creature config can override controller settings."""
        from kohakuterrarium.terrarium.config import _parse_creature

        data = {
            "name": "custom_swe",
            "base_config": "creatures/swe",
            "controller": {"model": "my-custom-model"},
            "channels": {"listen": ["tasks"]},
        }
        creature = _parse_creature(data, Path("/fake"))
        assert creature.config_data["controller"]["model"] == "my-custom-model"
        assert creature.config_data["base_config"] == "creatures/swe"


# ---------------------------------------------------------------------------
# Format-aware prompts
# ---------------------------------------------------------------------------

BRACKET_PATTERN = re.compile(r"\[/[a-z_]+\]|@@[a-z]+=")


class TestFormatAwarePrompts:
    """Verify no bracket syntax leaks into native mode prompts."""

    def test_native_aggregated_prompt_no_brackets(self):
        from kohakuterrarium.core.registry import Registry
        from kohakuterrarium.prompt.aggregator import aggregate_system_prompt

        prompt = aggregate_system_prompt(
            "You are a test agent.",
            Registry(),
            include_tools=True,
            include_hints=True,
            skill_mode="dynamic",
            tool_format="native",
        )
        matches = BRACKET_PATTERN.findall(prompt)
        assert matches == [], f"Bracket syntax leaked into native prompt: {matches}"

    def test_bracket_aggregated_prompt_has_brackets(self):
        from kohakuterrarium.core.registry import Registry
        from kohakuterrarium.prompt.aggregator import aggregate_system_prompt

        prompt = aggregate_system_prompt(
            "You are a test agent.",
            Registry(),
            include_tools=True,
            include_hints=True,
            skill_mode="dynamic",
            tool_format="bracket",
        )
        matches = BRACKET_PATTERN.findall(prompt)
        assert len(matches) > 0, "Bracket mode should contain bracket syntax"

    def test_channel_hints_native_no_brackets(self):
        from kohakuterrarium.core.registry import Registry
        from kohakuterrarium.prompt.aggregator import _build_channel_hints

        registry = Registry()
        # Register send_message tool to trigger channel hints
        from kohakuterrarium.builtins.tools import get_builtin_tool

        tool = get_builtin_tool("send_message")
        if tool:
            registry.register_tool(tool)

        hints = _build_channel_hints(registry, tool_format="native")
        matches = BRACKET_PATTERN.findall(hints)
        assert matches == [], f"Bracket leaked in native channel hints: {matches}"

    def test_channel_hints_bracket_has_description(self):
        from kohakuterrarium.core.registry import Registry
        from kohakuterrarium.prompt.aggregator import _build_channel_hints

        registry = Registry()
        from kohakuterrarium.builtins.tools import get_builtin_tool

        tool = get_builtin_tool("send_message")
        if tool:
            registry.register_tool(tool)

        hints = _build_channel_hints(registry, tool_format="bracket")
        assert "send_message" in hints

    def test_subagent_hints_native_no_brackets(self):
        from kohakuterrarium.modules.subagent.base import build_subagent_framework_hints

        hints = build_subagent_framework_hints("native")
        matches = BRACKET_PATTERN.findall(hints)
        assert matches == [], f"Bracket leaked in native subagent hints: {matches}"

    def test_subagent_hints_bracket_has_bracket_syntax(self):
        from kohakuterrarium.modules.subagent.base import build_subagent_framework_hints

        hints = build_subagent_framework_hints("bracket", BRACKET_FORMAT)
        assert "[/tool_name]" in hints
        assert "@@arg=value" in hints

    def test_subagent_hints_xml_has_xml_syntax(self):
        from kohakuterrarium.modules.subagent.base import build_subagent_framework_hints

        hints = build_subagent_framework_hints("xml", XML_FORMAT)
        assert "<tool_name" in hints
        assert "</tool_name>" in hints
        # Should NOT contain bracket syntax
        assert "[/tool_name]" not in hints

    def test_subagent_hints_custom_format(self):
        """Custom format generates correct examples."""
        from kohakuterrarium.modules.subagent.base import build_subagent_framework_hints

        custom = ToolCallFormat(
            start_char="{",
            end_char="}",
            slash_means_open=True,
            arg_style="line",
            arg_prefix="--",
            arg_kv_sep=":",
        )
        hints = build_subagent_framework_hints("custom", custom)
        assert "{/tool_name}" in hints
        assert "--arg:value" in hints
        assert "{tool_name/}" in hints


# TerrariumToolManager + the legacy ``terrarium_*`` tool surface were
# removed in the group-tools refactor; their replacements live in
# ``terrarium.tools_group`` and are tested in the group-tools suite.
