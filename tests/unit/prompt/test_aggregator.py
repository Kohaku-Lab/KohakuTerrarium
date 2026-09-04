"""Unit tests for ``prompt/aggregator.py`` — the framework prompt contract.

Every framework section is gated. The bugs this file exists to prevent are
*presence* bugs — a block emitted when its subject does not exist — so each
gate is asserted in both directions: present when true, absent when false.
"""

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolConfig
from kohakuterrarium.modules.tool.base import ToolResult
from kohakuterrarium.prompt.aggregator import (
    aggregate_system_prompt,
    build_context_message,
)


def _tool(name, desc, *, doc_mode=None, contribution=None):
    """Build a minimal registerable tool with the requested prompt behavior."""

    class _T(BaseTool):
        @property
        def tool_name(self):
            return name

        @property
        def description(self):
            return desc

        @property
        def execution_mode(self):
            return ExecutionMode.DIRECT

        def prompt_contribution(self):
            return contribution

        async def _execute(self, args, **kwargs):
            return ToolResult(output="")

    return _T(config=ToolConfig(doc_mode=doc_mode))


class _SubAgent:
    description = "Explores the codebase"


def _registry(*tools, subagents=()):
    reg = Registry()
    for tool in tools:
        reg.register_tool(tool)
    for name in subagents:
        reg.register_subagent(name, _SubAgent())
    return reg


class TestBasePrompt:
    def test_base_prompt_leads_the_result(self):
        out = aggregate_system_prompt("YOU ARE X.", _registry(_tool("read", "Read")))
        assert out.startswith("YOU ARE X.")

    def test_template_variables_render(self):
        out = aggregate_system_prompt(
            "I am {{ agent_name }}.", None, extra_context={"agent_name": "kt"}
        )
        assert "I am kt." in out

    def test_tools_variable_suppresses_the_generated_inventory(self):
        reg = _registry(_tool("read", "Read a file"))
        out = aggregate_system_prompt("{{ tools }}", reg, tool_format="bracket")
        assert "## Available Functions" not in out


class TestInventoryGate:
    def test_absent_in_native_mode(self):
        # The provider already receives name + description as schema; a second
        # copy in prose is pure duplication.
        reg = _registry(_tool("read", "Read a file"))
        out = aggregate_system_prompt("base", reg, tool_format="native")
        assert "## Available Functions" not in out
        assert "Read a file" not in out

    def test_present_for_text_formats(self):
        reg = _registry(_tool("read", "Read a file"))
        out = aggregate_system_prompt("base", reg, tool_format="bracket")
        assert "## Available Functions" in out
        assert "- `read`: Read a file" in out

    def test_present_in_native_when_doc_mode_is_full(self):
        reg = _registry(_tool("read", "Read a file"))
        out = aggregate_system_prompt(
            "base", reg, tool_format="native", tool_doc_mode="full"
        )
        assert "## Available Functions" in out

    def test_subagents_listed_for_text_formats(self):
        reg = _registry(_tool("read", "Read"), subagents=["explore"])
        out = aggregate_system_prompt("base", reg, tool_format="bracket")
        assert "**Sub-agents:**" in out
        assert "- `explore`: Explores the codebase" in out


class TestInlineDocsGate:
    def test_absent_in_standard_mode(self):
        reg = _registry(_tool("read", "Read"))
        out = aggregate_system_prompt("base", reg, tool_doc_mode="standard")
        assert "## Function Documentation" not in out

    def test_present_for_a_single_tool_opting_in(self):
        reg = _registry(
            _tool("read", "Read"), _tool("multi_edit", "Edit", doc_mode="full")
        )
        out = aggregate_system_prompt("base", reg, tool_doc_mode="standard")
        assert "## Function Documentation" in out
        # Only the opted-in tool's body is inlined.
        assert "multi_edit" in out.split("## Function Documentation")[1]

    def test_creature_wide_full_inlines_every_tool(self):
        reg = _registry(_tool("read", "Read"), _tool("grep", "Search"))
        out = aggregate_system_prompt("base", reg, tool_doc_mode="full")
        section = out.split("## Function Documentation")[1]
        assert "read" in section and "grep" in section

    def test_reference_tier_never_inlined(self):
        # Progressive disclosure: full inlines the usage tier only; reference
        # material stays behind ``info`` in every mode.
        reg = _registry(_tool("read", "Read"))
        out = aggregate_system_prompt("base", reg, tool_doc_mode="full")
        assert "## Reference" not in out


class TestCallSyntaxGate:
    def test_absent_in_native_mode(self):
        # Examples for a syntax the model must not use are worse than none.
        reg = _registry(_tool("read", "Read"))
        out = aggregate_system_prompt("base", reg, tool_format="native")
        assert "## Calling functions" not in out
        assert "[/read]" not in out

    def test_present_for_bracket(self):
        reg = _registry(_tool("read", "Read"), _tool("bash", "Shell"))
        out = aggregate_system_prompt("base", reg, tool_format="bracket")
        assert "## Calling functions" in out
        assert "[/read]" in out

    def test_examples_are_generated_from_the_active_format(self):
        reg = _registry(_tool("read", "Read"))
        bracket = aggregate_system_prompt("base", reg, tool_format="bracket")
        xml = aggregate_system_prompt("base", reg, tool_format="xml")
        assert "[/read]" in bracket and "<read" not in bracket
        assert "<read" in xml and "[/read]" not in xml


class TestOutputModelGate:
    def test_absent_in_native_mode(self):
        out = aggregate_system_prompt("base", _registry(), tool_format="native")
        assert "## Output format" not in out

    def test_present_for_text_formats(self):
        out = aggregate_system_prompt("base", _registry(), tool_format="bracket")
        assert "## Output format" in out

    def test_named_outputs_interpolated(self):
        out = aggregate_system_prompt(
            "base",
            _registry(),
            tool_format="bracket",
            known_outputs={"discord", "tts"},
        )
        assert "`discord`" in out and "`tts`" in out


class TestGraphSectionsAreNotOwnedHere:
    def test_no_channel_prose_for_a_solo_creature(self):
        # ``send_message`` being registered is not evidence of a graph; the
        # terrarium layer injects live topology when one actually exists.
        reg = _registry(_tool("send_message", "Send to a channel"))
        out = aggregate_system_prompt("base", reg, tool_format="native")
        assert "Internal Channels" not in out
        assert "Working with the group" not in out
        assert "Growing the group" not in out


class TestAlwaysOnSections:
    def test_untrusted_content_present_by_default(self):
        out = aggregate_system_prompt("base", _registry(), tool_format="native")
        assert "## Untrusted content" in out

    def test_execution_model_present_for_every_format(self):
        for fmt in ("native", "bracket", "xml"):
            out = aggregate_system_prompt("base", _registry(), tool_format=fmt)
            assert "## Execution model" in out

    def test_hints_suppressed_wholesale(self):
        out = aggregate_system_prompt(
            "base", _registry(), tool_format="bracket", include_hints=False
        )
        for heading in (
            "## Execution model",
            "## Untrusted content",
            "## Calling functions",
            "## Output format",
        ):
            assert heading not in out

    def test_tools_suppressed_wholesale(self):
        reg = _registry(_tool("read", "Read", contribution="Use me wisely."))
        out = aggregate_system_prompt(
            "base", reg, tool_format="bracket", include_tools=False
        )
        assert "## Available Functions" not in out
        assert "## Tool guidance" not in out


class TestToolGuidance:
    def test_contribution_rendered_when_present(self):
        reg = _registry(_tool("ask_user", "Ask", contribution="Waits for a reply."))
        out = aggregate_system_prompt("base", reg)
        assert "## Tool guidance" in out
        assert "Waits for a reply." in out

    def test_section_absent_when_no_tool_contributes(self):
        out = aggregate_system_prompt("base", _registry(_tool("read", "Read")))
        assert "## Tool guidance" not in out


class TestSectionOrder:
    def test_stable_order_keeps_the_cache_prefix_intact(self):
        reg = _registry(
            _tool("read", "Read", contribution="Read first."), subagents=["explore"]
        )
        out = aggregate_system_prompt("BASE.", reg, tool_format="bracket")
        order = [
            out.index("BASE."),
            out.index("## Available Functions"),
            out.index("## Tool guidance"),
            out.index("## Untrusted content"),
            out.index("## Calling functions"),
            out.index("## Output format"),
            out.index("## Execution model"),
        ]
        assert order == sorted(order)

    def test_repeated_builds_are_byte_identical(self):
        reg = _registry(_tool("read", "Read"), _tool("grep", "Search"))
        first = aggregate_system_prompt("base", reg)
        assert first == aggregate_system_prompt("base", reg)


class TestBuildContextMessage:
    def test_events_only(self):
        assert build_context_message("EVENTS") == "EVENTS"

    def test_job_status_leads(self):
        out = build_context_message("EVENTS", job_status="job-1 running")
        assert out.index("## Running Jobs") < out.index("EVENTS")
