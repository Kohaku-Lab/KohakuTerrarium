"""Tests for Cluster 5 / E.1 — BaseTool.prompt_contribution() assembly.

Covers:
- Tools returning a string appear in the aggregated system prompt.
- Bucket ordering (first / normal / last) is honored.
- Alphabetical ordering inside a bucket.
- ``None`` return omits the contribution cleanly.
- A tool that doesn't override the method (returns ``None`` by
  default) is skipped without touching the prompt.
- The section is omitted entirely when no tool contributes.
"""

from typing import Any

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.prompt.aggregator import aggregate_system_prompt
from kohakuterrarium.prompt.tool_contributions import (
    build_tool_guidance_section,
    collect_tool_contributions,
)

# ---------------------------------------------------------------------------
# Test tool factories
# ---------------------------------------------------------------------------


def _make_tool(
    name: str,
    *,
    contribution: str | None,
    bucket: str = "normal",
) -> BaseTool:
    """Build a minimal BaseTool subclass instance for the test registry."""

    class _T(BaseTool):
        prompt_contribution_bucket = bucket

        @property
        def tool_name(self) -> str:
            return name

        @property
        def description(self) -> str:
            return f"fake {name}"

        @property
        def execution_mode(self) -> ExecutionMode:
            return ExecutionMode.DIRECT

        def prompt_contribution(self) -> str | None:
            return contribution

        async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
            return ToolResult(output="")

    return _T()


def _make_silent_tool(name: str) -> BaseTool:
    """A tool that does NOT override prompt_contribution (returns None)."""

    class _S(BaseTool):
        @property
        def tool_name(self) -> str:
            return name

        @property
        def description(self) -> str:
            return f"silent {name}"

        @property
        def execution_mode(self) -> ExecutionMode:
            return ExecutionMode.DIRECT

        async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
            return ToolResult(output="")

    return _S()


# ---------------------------------------------------------------------------
# Collection / bucket ordering
# ---------------------------------------------------------------------------


class TestCollectContributions:
    def test_empty_registry_returns_empty(self):
        reg = Registry()
        assert collect_tool_contributions(reg) == []

    def test_none_registry_safe(self):
        assert collect_tool_contributions(None) == []

    def test_skips_silent_tool(self):
        reg = Registry()
        reg.register_tool(_make_silent_tool("silent_one"))
        assert collect_tool_contributions(reg) == []

    def test_collects_tools_with_contribution(self):
        reg = Registry()
        reg.register_tool(_make_tool("alpha", contribution="use alpha wisely"))
        reg.register_tool(_make_silent_tool("silent"))
        triples = collect_tool_contributions(reg)
        assert triples == [("normal", "alpha", "use alpha wisely")]


class TestBucketOrdering:
    def test_first_appears_before_normal(self):
        reg = Registry()
        reg.register_tool(_make_tool("bbb", contribution="normal_bbb", bucket="normal"))
        reg.register_tool(_make_tool("aaa", contribution="first_aaa", bucket="first"))
        section = build_tool_guidance_section(reg)
        assert section.index("first_aaa") < section.index("normal_bbb")

    def test_last_appears_after_normal(self):
        reg = Registry()
        reg.register_tool(_make_tool("mid", contribution="normal_mid", bucket="normal"))
        reg.register_tool(_make_tool("end", contribution="last_end", bucket="last"))
        section = build_tool_guidance_section(reg)
        assert section.index("normal_mid") < section.index("last_end")

    def test_alphabetical_within_bucket(self):
        reg = Registry()
        reg.register_tool(_make_tool("zeta", contribution="z_note"))
        reg.register_tool(_make_tool("alpha", contribution="a_note"))
        reg.register_tool(_make_tool("beta", contribution="b_note"))
        section = build_tool_guidance_section(reg)
        assert section.index("a_note") < section.index("b_note")
        assert section.index("b_note") < section.index("z_note")

    def test_full_three_bucket_order(self):
        reg = Registry()
        reg.register_tool(_make_tool("last_tool", contribution="L", bucket="last"))
        reg.register_tool(_make_tool("normal_tool", contribution="N", bucket="normal"))
        reg.register_tool(_make_tool("first_tool", contribution="F", bucket="first"))
        section = build_tool_guidance_section(reg)
        f_pos = section.index("first_tool")
        n_pos = section.index("normal_tool")
        l_pos = section.index("last_tool")
        assert f_pos < n_pos < l_pos

    def test_unknown_bucket_falls_back_to_normal(self):
        """Typos in ``prompt_contribution_bucket`` must not crash."""
        reg = Registry()
        reg.register_tool(_make_tool("typo", contribution="T", bucket="thisisnotreal"))
        # Should not raise — falls back to "normal".
        section = build_tool_guidance_section(reg)
        assert "typo" in section


# ---------------------------------------------------------------------------
# None return is clean
# ---------------------------------------------------------------------------


class TestNoneReturn:
    def test_bare_none_omits_contribution(self):
        reg = Registry()
        reg.register_tool(_make_tool("quiet", contribution=None))
        section = build_tool_guidance_section(reg)
        assert section == ""

    def test_empty_string_omits_contribution(self):
        reg = Registry()
        reg.register_tool(_make_tool("empty", contribution=""))
        section = build_tool_guidance_section(reg)
        assert section == ""


# ---------------------------------------------------------------------------
# Aggregator integration — section is wired between tool list and hints.
# ---------------------------------------------------------------------------


class TestAggregatorIntegration:
    def test_section_present_in_aggregated_prompt(self):
        reg = Registry()
        reg.register_tool(_make_tool("alpha", contribution="alpha guidance"))
        prompt = aggregate_system_prompt(
            base_prompt="You are a helpful assistant.",
            registry=reg,
            include_tools=True,
            include_hints=True,
        )
        assert "Tool guidance" in prompt
        assert "alpha guidance" in prompt

    def test_section_between_tools_and_hints(self):
        reg = Registry()
        reg.register_tool(_make_tool("alpha", contribution="alpha guidance"))
        prompt = aggregate_system_prompt(
            base_prompt="base",
            registry=reg,
            include_tools=True,
            include_hints=True,
        )
        # Tool list header should appear before "Tool guidance"
        # and the framework-hint output model section after it.
        tools_idx = prompt.find("Available Functions")
        guidance_idx = prompt.find("Tool guidance")
        hints_idx = prompt.find("Calling Functions")
        assert tools_idx != -1 and guidance_idx != -1 and hints_idx != -1
        assert tools_idx < guidance_idx < hints_idx

    def test_section_omitted_when_no_contributions(self):
        reg = Registry()
        reg.register_tool(_make_silent_tool("only"))
        prompt = aggregate_system_prompt(
            base_prompt="base",
            registry=reg,
            include_tools=True,
            include_hints=True,
        )
        assert "Tool guidance" not in prompt
