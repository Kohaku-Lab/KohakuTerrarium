"""Unit tests for :mod:`kohakuterrarium.prompt.aggregator`.

This is the heart of the prompt system. CLAUDE.md "Prompt System
Design (CRITICAL)" pins the structural invariants the aggregated output
must satisfy:

- The tool *list* is auto-generated: name + one-line description only.
- Tool call *syntax* lives in the framework hints, never in the list.
- Full tool docs are NOT in the system prompt in dynamic mode — they
  reach the model via the ``info`` command. Static mode is the
  documented exception that *does* embed them.
- ``{{ tools }}`` in the base prompt suppresses the auto-appended list
  (the author placed it themselves).

These tests assert those invariants on real ``Registry`` output.
"""

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.plugin.base import PluginContext as RuntimePluginContext
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.prompt.aggregator import (
    aggregate_system_prompt,
    aggregate_with_plugins,
    build_context_message,
)
from kohakuterrarium.prompt.plugins import BasePlugin as PromptBasePlugin
from kohakuterrarium.skills import Skill, SkillRegistry


def _make_tool(name: str, description: str, *, contribution: str | None = None):
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

        def prompt_contribution(self) -> str | None:
            return contribution

    return _T()


def _registry_with(*tools):
    reg = Registry()
    for t in tools:
        reg.register_tool(t)
    return reg


class _FakeSubagent:
    def __init__(self, description: str):
        self.description = description


class TestToolExamplesInHints:
    def test_glob_example_used_when_no_read_tool(self):
        reg = _registry_with(_make_tool("glob", "Glob files"))
        out = aggregate_system_prompt("base", reg, tool_format="bracket")
        assert "[/glob]" in out
        assert "pattern=**/*.py" in out

    def test_send_message_example_used_when_no_write_tool(self):
        reg = _registry_with(_make_tool("send_message", "Send to channel"))
        out = aggregate_system_prompt("base", reg, tool_format="bracket")
        assert "[/send_message]" in out

    def test_write_example_used_when_write_tool_present(self):
        reg = _registry_with(_make_tool("write", "Write a file"))
        out = aggregate_system_prompt("base", reg, tool_format="bracket")
        assert "[/write]" in out
        assert "path=out.txt" in out

    def test_subagent_example_included_when_subagent_registered(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        reg.register_subagent("explore", _FakeSubagent("explore the code"))
        out = aggregate_system_prompt("base", reg, tool_format="bracket")
        # The first sorted sub-agent name is shown as a call example.
        assert "[/explore]" in out


class TestToolListSubagents:
    def test_subagents_listed_with_descriptions(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        reg.register_subagent("planner", _FakeSubagent("plans the work"))
        out = aggregate_system_prompt("base", reg, skill_mode="dynamic")
        assert "**Sub-agents:**" in out
        assert "- `planner`: plans the work" in out

    def test_subagent_without_description_falls_back(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        reg.register_subagent("nodesc", object())
        out = aggregate_system_prompt("base", reg, skill_mode="dynamic")
        assert "- `nodesc`: Sub-agent" in out


class TestStaticModeFullDocs:
    def test_static_mode_includes_subagent_docs_section(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        reg.register_subagent("planner", _FakeSubagent("plans the work"))
        out = aggregate_system_prompt("base", reg, skill_mode="static")
        assert "## Function Documentation" in out
        # Sub-agent with no builtin doc falls back to a heading + description.
        assert "### planner" in out
        assert "plans the work" in out

    def test_static_mode_subagent_without_description_falls_back(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        reg.register_subagent("nodesc", object())
        out = aggregate_system_prompt("base", reg, skill_mode="static")
        assert "### nodesc\nSub-agent" in out

    def test_static_mode_tool_without_builtin_doc_falls_back_to_description(self):
        # A custom tool name has no builtin SKILL.md -> heading + description.
        reg = _registry_with(_make_tool("custom_tool_xyz", "a bespoke tool"))
        out = aggregate_system_prompt("base", reg, skill_mode="static")
        assert "### custom_tool_xyz\na bespoke tool" in out

    def test_static_mode_pulls_builtin_subagent_doc(self):
        # 'explore' ships a real builtin subagent SKILL.md -> its body is
        # embedded verbatim (not the heading+description fallback).
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        reg.register_subagent("explore", _FakeSubagent("explore the code"))
        out = aggregate_system_prompt("base", reg, skill_mode="static")
        assert "## Function Documentation" in out
        # The builtin doc body is richer than a one-line "### explore" stub.
        explore_section = out.split("explore", 1)[1]
        assert len(explore_section) > len("the code")

    def test_static_mode_empty_registry_omits_function_docs(self):
        out = aggregate_system_prompt("base", Registry(), skill_mode="static")
        assert "## Function Documentation" not in out


class TestEmptyRegistry:
    def test_empty_registry_omits_available_functions(self):
        # A registry with zero tools/subagents -> no tool-list section.
        out = aggregate_system_prompt("base", Registry(), skill_mode="dynamic")
        assert "## Available Functions" not in out


class TestToolListInvariants:
    def test_dynamic_mode_lists_name_and_description(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt("You are an agent.", reg, skill_mode="dynamic")
        assert "You are an agent." in out
        assert "- `bash`: Execute shell commands" in out

    def test_dynamic_mode_does_not_embed_full_tool_docs(self):
        # 'read' has a real builtin skill doc; dynamic mode must NOT pull it in.
        reg = _registry_with(_make_tool("read", "Read file contents"))
        out = aggregate_system_prompt("base", reg, skill_mode="dynamic")
        assert "## Function Documentation" not in out
        # The one-line description is allowed; the multi-section body is not.
        assert "## Available Functions" in out

    def test_static_mode_embeds_full_tool_docs(self):
        reg = _registry_with(_make_tool("read", "Read file contents"))
        out = aggregate_system_prompt("base", reg, skill_mode="static")
        assert "## Function Documentation" in out

    def test_tools_placeholder_suppresses_auto_appended_list(self):
        # The literal token ``{{ tools }}`` in the base prompt signals the
        # author placed the inventory themselves -> no auto-appended list.
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        base = "Tools: {{ tools }}"
        out = aggregate_system_prompt(base, reg, skill_mode="dynamic")
        assert "## Available Functions" not in out

    def test_include_tools_false_omits_list_section(self):
        # include_tools=False drops the tool LIST section; framework-hint
        # examples are built independently and may still mention tools.
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt("base", reg, include_tools=False)
        assert "## Available Functions" not in out
        assert "## Tool guidance" not in out

    def test_no_registry_still_appends_framework_hints(self):
        # With no registry the tool list is skipped, but include_hints
        # defaults True so the output-model block is still appended.
        out = aggregate_system_prompt("only this")
        assert out.startswith("only this")
        assert "## Output Format" in out
        assert "## Available Functions" not in out


class TestFrameworkHintInvariants:
    def test_bracket_mode_carries_call_syntax(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt("base", reg, tool_format="bracket")
        assert "## Calling Functions" in out
        assert "[/function_name]" in out

    def test_xml_mode_carries_xml_call_syntax(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt("base", reg, tool_format="xml")
        assert "<function_name" in out

    def test_native_mode_skips_syntax_examples(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt("base", reg, tool_format="native")
        # Native: API handles formatting -> no bracket/xml example markers.
        assert "## Calling Functions" not in out
        assert "[/function_name]" not in out

    def test_include_hints_false_omits_syntax(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt("base", reg, include_hints=False)
        assert "## Calling Functions" not in out
        assert "## Output Format" not in out

    def test_call_syntax_is_in_hints_not_in_tool_list_section(self):
        reg = _registry_with(_make_tool("read", "Read file contents"))
        out = aggregate_system_prompt("base", reg, tool_format="bracket")
        list_section = out.split("## Available Functions")[1].split("##")[0]
        # The tool list section itself must not contain call-syntax markers.
        assert "[/function_name]" not in list_section


class TestOutputHints:
    def test_no_known_outputs_basic_output_model(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt("base", reg)
        assert "## Output Format" in out
        # No named outputs -> the "Available:" line is not present.
        assert "Available:" not in out

    def test_known_outputs_listed_and_sorted(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt("base", reg, known_outputs={"discord", "console"})
        assert "Available: `console`, `discord`" in out

    def test_output_model_override_used_verbatim(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt(
            "base",
            reg,
            framework_hint_overrides={"framework.output_model": "CUSTOM OUTPUT BLOCK"},
        )
        assert "CUSTOM OUTPUT BLOCK" in out
        assert "## Output Format" not in out

    def test_empty_output_model_override_suppresses_block(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt(
            "base", reg, framework_hint_overrides={"framework.output_model": ""}
        )
        assert "## Output Format" not in out

    def test_native_mode_skips_output_model_block(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt("base", reg, tool_format="native")
        assert "## Output Format" not in out


class TestExecutionModelOverrides:
    def test_dynamic_execution_block_present_by_default(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt("base", reg, skill_mode="dynamic")
        assert "## Execution Model" in out

    def test_empty_dynamic_execution_override_suppresses_block(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt(
            "base",
            reg,
            skill_mode="dynamic",
            framework_hint_overrides={"framework.execution_model.dynamic": ""},
        )
        assert "## Execution Model" not in out
        # Calling-syntax header is independent and must still be present.
        assert "## Calling Functions" in out


class TestChannelHints:
    def test_send_message_tool_triggers_internal_channels_hint(self):
        reg = _registry_with(_make_tool("send_message", "Send to a channel"))
        out = aggregate_system_prompt("base", reg)
        assert "## Internal Channels" in out

    def test_no_send_message_tool_means_no_channel_hint(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt("base", reg)
        assert "## Internal Channels" not in out

    def test_channels_provided_suppresses_generic_hint(self):
        # When channels are pre-declared (terrarium creature), the topology
        # prompt covers them, so the generic block is dropped.
        reg = _registry_with(_make_tool("send_message", "Send to a channel"))
        out = aggregate_system_prompt(
            "base", reg, channels=[{"name": "team", "description": "the team"}]
        )
        assert "## Internal Channels" not in out


class TestToolGuidanceSection:
    def test_tool_contribution_inserted_between_list_and_hints(self):
        reg = _registry_with(
            _make_tool("bash", "Execute shell commands", contribution="prefer -x")
        )
        out = aggregate_system_prompt("base", reg, tool_format="bracket")
        assert "## Tool guidance" in out
        # Ordering: tool list -> tool guidance -> framework hints.
        assert (
            out.index("## Available Functions")
            < out.index("## Tool guidance")
            < out.index("## Calling Functions")
        )

    def test_no_contributions_drops_guidance_section(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt("base", reg)
        assert "## Tool guidance" not in out


class TestSkillIndex:
    def test_skill_registry_index_appended(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        skills = SkillRegistry()
        skills.add(Skill(name="deploy", description="how to deploy", body="step 1..."))
        out = aggregate_system_prompt("base", reg, skill_registry=skills)
        assert "## Skills" in out
        assert "deploy" in out

    def test_no_skill_registry_means_no_skills_section(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_system_prompt("base", reg)
        assert "## Skills" not in out

    def test_invocation_blocked_skill_hidden_from_index(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        skills = SkillRegistry()
        skills.add(
            Skill(
                name="hidden",
                description="secret",
                body="...",
                disable_model_invocation=True,
            )
        )
        out = aggregate_system_prompt("base", reg, skill_registry=skills)
        assert "## Skills" not in out


class TestRuntimePluginContributions:
    def test_runtime_plugin_prose_inserted(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))

        class _P(BasePlugin):
            @property
            def name(self) -> str:
                return "my_runtime_plugin"

            def get_prompt_content(self, context) -> str | None:
                return "RUNTIME PLUGIN PROSE"

        mgr = PluginManager()
        mgr.register(_P())
        out = aggregate_system_prompt(
            "base",
            reg,
            runtime_plugins=mgr,
            plugin_context=RuntimePluginContext(agent_name="a"),
        )
        assert "RUNTIME PLUGIN PROSE" in out

    def test_runtime_plugins_without_context_are_ignored(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))

        class _P(BasePlugin):
            @property
            def name(self) -> str:
                return "p"

            def get_prompt_content(self, context) -> str | None:
                return "SHOULD NOT APPEAR"

        mgr = PluginManager()
        mgr.register(_P())
        out = aggregate_system_prompt("base", reg, runtime_plugins=mgr)
        assert "SHOULD NOT APPEAR" not in out


class TestBuildContextMessage:
    def test_events_only(self):
        assert build_context_message("event text") == "event text"

    def test_with_job_status_prepends_running_jobs_section(self):
        out = build_context_message("event text", job_status="job_1 running")
        assert out == "## Running Jobs\njob_1 running\n\nevent text"


class TestAggregateWithPlugins:
    def test_default_plugins_used_when_none_passed(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))
        out = aggregate_with_plugins("base prompt", registry=reg)
        # Default plugins = tool_list + framework_hints.
        assert "## Available Tools" in out
        assert "## Tool Call Syntax" in out

    def test_plugins_appended_in_priority_order(self):
        reg = _registry_with(_make_tool("bash", "Execute shell commands"))

        class _Early(PromptBasePlugin):
            @property
            def name(self) -> str:
                return "early"

            @property
            def priority(self) -> int:
                return 1

            def get_content(self, context) -> str | None:
                return "EARLY SECTION"

        class _Late(PromptBasePlugin):
            @property
            def name(self) -> str:
                return "late"

            @property
            def priority(self) -> int:
                return 99

            def get_content(self, context) -> str | None:
                return "LATE SECTION"

        out = aggregate_with_plugins("base", plugins=[_Late(), _Early()], registry=reg)
        assert out.index("EARLY SECTION") < out.index("LATE SECTION")

    def test_base_prompt_always_comes_first(self):
        class _P(PromptBasePlugin):
            @property
            def name(self) -> str:
                return "p"

            def get_content(self, context) -> str | None:
                return "PLUGIN PART"

        out = aggregate_with_plugins("BASE PART", plugins=[_P()])
        assert out.index("BASE PART") < out.index("PLUGIN PART")

    def test_plugin_returning_none_contributes_nothing(self):
        class _P(PromptBasePlugin):
            @property
            def name(self) -> str:
                return "p"

            def get_content(self, context) -> str | None:
                return None

        assert aggregate_with_plugins("just base", plugins=[_P()]) == "just base"

    def test_failing_plugin_is_isolated_not_fatal(self):
        class _Bad(PromptBasePlugin):
            @property
            def name(self) -> str:
                return "bad"

            def get_content(self, context) -> str | None:
                raise RuntimeError("boom")

        class _Good(PromptBasePlugin):
            @property
            def name(self) -> str:
                return "good"

            def get_content(self, context) -> str | None:
                return "GOOD SECTION"

        out = aggregate_with_plugins("base", plugins=[_Bad(), _Good()])
        assert "GOOD SECTION" in out
        assert "base" in out

    def test_base_prompt_template_rendered_with_extra_context(self):
        out = aggregate_with_plugins(
            "Hello {{ who }}", plugins=[], extra_context={"who": "agent"}
        )
        assert out == "Hello agent"
