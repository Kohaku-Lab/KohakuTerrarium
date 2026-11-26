"""Unit tests for ``prompt/framework_hints.py`` — canonical hint blocks.

The contract: six canonical keys, each resolvable to built-in prose, replaceable
by an override, and suppressible with an empty override. Unknown keys are
tolerated (warn, ignore) rather than fatal, so a stale creature config cannot
break prompt assembly.
"""

import pytest

from kohakuterrarium.prompt.framework_hints import (
    HINT_CALL_SYNTAX,
    HINT_EXECUTION_MODEL,
    HINT_GROUP_GROWTH,
    HINT_GROUP_MODEL,
    HINT_OUTPUT_MODEL,
    HINT_UNTRUSTED_CONTENT,
    call_discipline,
    canonical_keys,
    execution_model,
    get_framework_hint,
    is_default_hint,
    merge_overrides,
)

ALL_KEYS = (
    HINT_EXECUTION_MODEL,
    HINT_CALL_SYNTAX,
    HINT_OUTPUT_MODEL,
    HINT_UNTRUSTED_CONTENT,
    HINT_GROUP_MODEL,
    HINT_GROUP_GROWTH,
)


class TestCanonicalKeys:
    def test_all_six_keys_exposed_in_definition_order(self):
        assert canonical_keys() == ALL_KEYS

    def test_every_key_resolves_to_non_empty_prose(self):
        for key in ALL_KEYS:
            assert get_framework_hint(key).strip()

    def test_execution_model_is_one_block_not_three(self):
        # The dynamic/static/native variants were ~80% identical prose; a fix
        # landing in one and not the others was the standing maintenance trap.
        for stale in (
            "framework.execution_model.dynamic",
            "framework.execution_model.static",
            "framework.execution_model.native",
        ):
            assert stale not in canonical_keys()
            assert get_framework_hint(stale) is None


class TestOverrides:
    def test_override_replaces_builtin(self):
        out = get_framework_hint(HINT_EXECUTION_MODEL, {HINT_EXECUTION_MODEL: "MINE"})
        assert out == "MINE"

    def test_empty_override_suppresses_block(self):
        assert get_framework_hint(HINT_GROUP_MODEL, {HINT_GROUP_MODEL: ""}) == ""

    def test_unknown_override_key_is_ignored_not_fatal(self):
        out = get_framework_hint(HINT_OUTPUT_MODEL, {"framework.nope": "x"})
        assert is_default_hint(HINT_OUTPUT_MODEL, out)

    def test_creature_override_beats_package_override(self):
        merged = merge_overrides(
            {HINT_UNTRUSTED_CONTENT: "pkg"}, {HINT_UNTRUSTED_CONTENT: "creature"}
        )
        assert get_framework_hint(HINT_UNTRUSTED_CONTENT, merged) == "creature"

    def test_merge_keeps_unknown_keys_for_consistent_reporting(self):
        assert merge_overrides({"bogus": "a"}, None) == {"bogus": "a"}


class TestIsDefaultHint:
    def test_true_only_for_the_builtin_template(self):
        # Interpolation must never run over user prose, or a literal ``{x}``
        # in an override would raise KeyError at prompt-build time.
        assert is_default_hint(HINT_CALL_SYNTAX, get_framework_hint(HINT_CALL_SYNTAX))
        assert not is_default_hint(HINT_CALL_SYNTAX, "custom {braces} here")
        assert not is_default_hint(HINT_CALL_SYNTAX, None)
        assert not is_default_hint("framework.nope", "anything")


class TestCallDiscipline:
    @pytest.mark.parametrize("fmt", ["bracket", "xml", "custom"])
    def test_text_formats_get_the_block_only_rule(self, fmt):
        assert "function-call block and nothing else" in call_discipline(fmt)

    def test_native_gets_no_textual_formatting_rule(self):
        out = call_discipline("native")
        assert "function-call block" not in out
        assert "Call only the tools" in out


class TestTemplatePlaceholders:
    def test_execution_model_carries_the_call_discipline_slot(self):
        assert "{call_discipline}" in get_framework_hint(HINT_EXECUTION_MODEL)

    def test_call_syntax_carries_generated_example_slots(self):
        template = get_framework_hint(HINT_CALL_SYNTAX)
        for slot in ("{format_example}", "{examples}", "{info_example}"):
            assert slot in template

    def test_group_growth_carries_the_population_slot(self):
        # The cap is read live from the engine; hard-coding it in prose would
        # state a limit the engine may not be enforcing.
        assert "{population}" in get_framework_hint(HINT_GROUP_GROWTH)

    def test_subagent_isolation_is_stated_here_not_per_schema(self):
        block = get_framework_hint(HINT_EXECUTION_MODEL)
        assert "context-isolated" in block
        assert "continue the previous task" in block


class TestExecutionModelParts:
    def test_all_parts_render_the_canonical_default(self):
        for fmt in ("native", "bracket"):
            full = execution_model(fmt, subagents=True, background_tools=True)
            assert full == get_framework_hint(HINT_EXECUTION_MODEL).format(
                call_discipline=call_discipline(fmt)
            )

    def test_no_subject_keeps_only_results_and_discipline(self):
        out = execution_model("native", subagents=False, background_tools=False)
        assert out == (
            "## Execution model\n\n"
            "Tool results are delivered after your response ends; you are invoked "
            "again with them.\n\n" + call_discipline("native")
        )

    def test_subagent_parts_follow_subagents(self):
        with_sub = execution_model("native", subagents=True, background_tools=False)
        without = execution_model("native", subagents=False, background_tools=True)
        for part in ("Sub-agents run in the background", "context-isolated"):
            assert part in with_sub and part not in without

    def test_background_tool_parts_follow_background_tools(self):
        with_bg = execution_model("native", subagents=False, background_tools=True)
        without = execution_model("native", subagents=True, background_tools=False)
        for part in ("Tools that accept `run_in_background`", "daemon"):
            assert part in with_bg and part not in without

    def test_background_work_paragraph_follows_either_subject(self):
        para = "Starting background work returns no result"
        assert para in execution_model("xml", subagents=True, background_tools=False)
        assert para in execution_model("xml", subagents=False, background_tools=True)
        assert para not in execution_model(
            "xml", subagents=False, background_tools=False
        )

    def test_tool_sentence_stands_alone_without_subagents(self):
        # "the same way" refers back to the sub-agent sentence.
        solo = execution_model("native", subagents=False, background_tools=True)
        both = execution_model("native", subagents=True, background_tools=True)
        assert "the same way" not in solo and "start in the background" in solo
        assert "the same way" in both
