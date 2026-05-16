"""Unit tests for :mod:`kohakuterrarium.prompt.tool_contributions`.

Tools may return a short ``prompt_contribution()`` string. The
assembler partitions them into ``first`` / ``normal`` / ``last`` buckets
and sorts alphabetically by tool name *within* a bucket so the prompt
prefix is deterministic (cache-stable). Contract:

- ``None`` / empty contributions are dropped.
- Tools that aren't ``BaseTool`` subclasses are skipped.
- An unknown bucket value falls back to ``normal`` (warned).
- ``build_tool_guidance_section`` returns ``""`` when nothing contributes.
"""

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.prompt.tool_contributions import (
    build_tool_guidance_section,
    collect_tool_contributions,
)


def _make_tool(name: str, contribution: str | None, bucket: str = "normal"):
    """Construct a minimal BaseTool with a fixed prompt contribution."""

    class _T(BaseTool):
        prompt_contribution_bucket = bucket

        @property
        def tool_name(self) -> str:
            return name

        @property
        def description(self) -> str:
            return f"desc of {name}"

        @property
        def execution_mode(self) -> ExecutionMode:
            return ExecutionMode.DIRECT

        async def _execute(self, args, **kwargs) -> ToolResult:
            return ToolResult(output="")

        def prompt_contribution(self) -> str | None:
            return contribution

    return _T()


class TestCollectToolContributions:
    def test_none_registry_returns_empty_list(self):
        assert collect_tool_contributions(None) == []

    def test_empty_registry_returns_empty_list(self):
        assert collect_tool_contributions(Registry()) == []

    def test_collects_bucket_name_contribution_triple(self):
        reg = Registry()
        reg.register_tool(_make_tool("alpha", "use alpha wisely", bucket="first"))
        triples = collect_tool_contributions(reg)
        assert triples == [("first", "alpha", "use alpha wisely")]

    def test_contribution_is_stripped(self):
        reg = Registry()
        reg.register_tool(_make_tool("alpha", "  padded text  \n"))
        assert collect_tool_contributions(reg) == [("normal", "alpha", "padded text")]

    def test_none_contribution_is_dropped(self):
        reg = Registry()
        reg.register_tool(_make_tool("silent", None))
        assert collect_tool_contributions(reg) == []

    def test_empty_string_contribution_is_dropped(self):
        reg = Registry()
        reg.register_tool(_make_tool("blank", ""))
        assert collect_tool_contributions(reg) == []

    def test_unknown_bucket_falls_back_to_normal(self):
        reg = Registry()
        reg.register_tool(_make_tool("weird", "hint", bucket="middle-ish"))
        assert collect_tool_contributions(reg) == [("normal", "weird", "hint")]

    def test_non_basetool_protocol_tool_is_skipped(self):
        # A pure Tool-protocol implementation (no BaseTool) has no
        # prompt_contribution() method -> skipped, not crashed.
        class _ProtocolTool:
            @property
            def tool_name(self) -> str:
                return "proto"

            @property
            def description(self) -> str:
                return "protocol-only tool"

            @property
            def execution_mode(self) -> ExecutionMode:
                return ExecutionMode.DIRECT

            async def execute(self, args, context=None) -> ToolResult:
                return ToolResult(output="")

        reg = Registry()
        reg.register_tool(_ProtocolTool())
        assert collect_tool_contributions(reg) == []


class TestBuildToolGuidanceSection:
    def test_no_contributions_returns_empty_string(self):
        reg = Registry()
        reg.register_tool(_make_tool("quiet", None))
        assert build_tool_guidance_section(reg) == ""

    def test_none_registry_returns_empty_string(self):
        assert build_tool_guidance_section(None) == ""

    def test_section_has_header_and_entry(self):
        reg = Registry()
        reg.register_tool(_make_tool("bash", "shell hint"))
        out = build_tool_guidance_section(reg)
        assert out == "## Tool guidance\n\n- **bash**: shell hint"

    def test_alphabetical_within_normal_bucket(self):
        reg = Registry()
        reg.register_tool(_make_tool("zeta", "z hint"))
        reg.register_tool(_make_tool("alpha", "a hint"))
        out = build_tool_guidance_section(reg)
        assert out == ("## Tool guidance\n\n- **alpha**: a hint\n- **zeta**: z hint")

    def test_bucket_order_first_then_normal_then_last(self):
        reg = Registry()
        reg.register_tool(_make_tool("n_tool", "n", bucket="normal"))
        reg.register_tool(_make_tool("l_tool", "l", bucket="last"))
        reg.register_tool(_make_tool("f_tool", "f", bucket="first"))
        out = build_tool_guidance_section(reg)
        # first bucket entry must precede normal, which precedes last.
        assert out.index("f_tool") < out.index("n_tool") < out.index("l_tool")
