"""Unit tests for the core output_wiring module.

Covers:

- ``OutputWiringEntry`` dataclass validation.
- ``parse_wiring_entry`` / ``parse_wiring_list`` shapes.
- ``render_prompt`` in simple + jinja modes, including defaults.
- ``NoopOutputWiringResolver`` behaviour (log-once then silent).
- ``create_creature_output_event`` helper shape.
"""

import asyncio

import pytest

from kohakuterrarium.core.events import (
    EventType,
    TriggerEvent,
    create_creature_output_event,
)
from kohakuterrarium.core.output_wiring import (
    DEFAULT_PROMPT_WITH_CONTENT,
    DEFAULT_PROMPT_WITHOUT_CONTENT,
    PROMPT_FORMAT_JINJA,
    PROMPT_FORMAT_SIMPLE,
    ROOT_TARGET,
    NoopOutputWiringResolver,
    OutputWiringEntry,
    parse_wiring_entry,
    parse_wiring_list,
    render_prompt,
    wiring_targets,
)

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


class TestOutputWiringEntry:
    def test_defaults(self):
        entry = OutputWiringEntry(to="coder")
        assert entry.to == "coder"
        assert entry.with_content is True
        assert entry.prompt is None
        assert entry.prompt_format == PROMPT_FORMAT_SIMPLE

    def test_root_target_constant(self):
        # Sanity: the magic string matches documented value.
        assert ROOT_TARGET == "root"
        entry = OutputWiringEntry(to=ROOT_TARGET, with_content=False)
        assert entry.to == "root"
        assert entry.with_content is False

    def test_empty_to_raises(self):
        with pytest.raises(ValueError, match="to must be a non-empty string"):
            OutputWiringEntry(to="")

    def test_non_string_to_raises(self):
        with pytest.raises(ValueError):
            OutputWiringEntry(to=None)  # type: ignore[arg-type]

    def test_invalid_prompt_format_raises(self):
        with pytest.raises(ValueError, match="prompt_format must be"):
            OutputWiringEntry(to="x", prompt_format="liquid")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestParseWiringEntry:
    def test_string_shorthand(self):
        entry = parse_wiring_entry("runner")
        assert entry.to == "runner"
        assert entry.with_content is True
        assert entry.prompt is None
        assert entry.prompt_format == PROMPT_FORMAT_SIMPLE

    def test_full_dict(self):
        entry = parse_wiring_entry(
            {
                "to": "root",
                "with_content": False,
                "prompt": "[{source}] done",
                "prompt_format": "simple",
            }
        )
        assert entry.to == "root"
        assert entry.with_content is False
        assert entry.prompt == "[{source}] done"
        assert entry.prompt_format == "simple"

    def test_dict_with_jinja_format(self):
        entry = parse_wiring_entry(
            {"to": "r", "prompt_format": "jinja", "prompt": "{{ source }}"}
        )
        assert entry.prompt_format == PROMPT_FORMAT_JINJA

    def test_missing_to_raises(self):
        with pytest.raises(ValueError, match="missing required 'to' field"):
            parse_wiring_entry({"with_content": True})

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="must be a string or mapping"):
            parse_wiring_entry(42)

    def test_parse_list_none_is_empty(self):
        assert parse_wiring_list(None) == []

    def test_parse_list_missing_key(self):
        assert parse_wiring_list([]) == []

    def test_parse_list_mixed(self):
        entries = parse_wiring_list(["coder", {"to": "root", "with_content": False}])
        assert len(entries) == 2
        assert entries[0].to == "coder"
        assert entries[1].to == "root"
        assert entries[1].with_content is False

    def test_parse_list_non_list_raises(self):
        with pytest.raises(ValueError, match="must be a list"):
            parse_wiring_list("runner")

    def test_wiring_targets_helper(self):
        entries = parse_wiring_list(["a", "b", "c"])
        assert wiring_targets(entries) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


RENDER_VARS = dict(
    source="coder",
    target="runner",
    content="compiled output",
    turn_index=3,
    source_event_type="user_input",
)


class TestRenderPromptSimple:
    def test_default_with_content(self):
        entry = OutputWiringEntry(to="runner", with_content=True)
        out = render_prompt(entry, **RENDER_VARS)
        assert out == "[output-wire from coder] compiled output"

    def test_default_without_content(self):
        entry = OutputWiringEntry(to="runner", with_content=False)
        out = render_prompt(entry, **RENDER_VARS)
        assert out == "[output-wire from coder] (turn-end signal, no content)"

    def test_custom_template(self):
        entry = OutputWiringEntry(
            to="runner",
            prompt="src={source} tgt={target} t={turn_index}",
        )
        out = render_prompt(entry, **RENDER_VARS)
        assert out == "src=coder tgt=runner t=3"

    def test_missing_key_renders_empty(self):
        entry = OutputWiringEntry(
            to="runner", prompt="{source}|{does_not_exist}|{content}"
        )
        out = render_prompt(entry, **RENDER_VARS)
        assert out == "coder||compiled output"

    def test_custom_template_with_literal_braces_isnt_supported_by_simple(self):
        # Simple format uses str.format_map; literal braces must be doubled.
        # We don't require users to know this — but we don't escape for them.
        entry = OutputWiringEntry(to="r", prompt="{{literal}} {source}")
        out = render_prompt(entry, **RENDER_VARS)
        assert out == "{literal} coder"


class TestRenderPromptJinja:
    def test_basic_substitution(self):
        entry = OutputWiringEntry(
            to="runner",
            prompt="source={{ source }}, content={{ content }}",
            prompt_format=PROMPT_FORMAT_JINJA,
        )
        out = render_prompt(entry, **RENDER_VARS)
        assert out == "source=coder, content=compiled output"

    def test_filter_usage(self):
        entry = OutputWiringEntry(
            to="runner",
            prompt="{{ source | upper }}",
            prompt_format=PROMPT_FORMAT_JINJA,
        )
        out = render_prompt(entry, **RENDER_VARS)
        assert out == "CODER"

    def test_conditional(self):
        entry = OutputWiringEntry(
            to="runner",
            prompt=(
                "{% if with_content %}{{ content }}{% else %}"
                "turn {{ turn_index }}{% endif %}"
            ),
            prompt_format=PROMPT_FORMAT_JINJA,
        )
        out_true = render_prompt(entry, **RENDER_VARS)
        assert out_true == "compiled output"

        entry_false = OutputWiringEntry(
            to="runner",
            prompt=(
                "{% if with_content %}{{ content }}{% else %}"
                "turn {{ turn_index }}{% endif %}"
            ),
            prompt_format=PROMPT_FORMAT_JINJA,
            with_content=False,
        )
        out_false = render_prompt(entry_false, **RENDER_VARS)
        assert out_false == "turn 3"


class TestRenderPromptDefaultsConstants:
    def test_constants_match_docs(self):
        assert DEFAULT_PROMPT_WITH_CONTENT == "[output-wire from {source}] {content}"
        assert (
            DEFAULT_PROMPT_WITHOUT_CONTENT
            == "[output-wire from {source}] (turn-end signal, no content)"
        )


# ---------------------------------------------------------------------------
# Noop resolver
# ---------------------------------------------------------------------------


class TestNoopResolver:
    def test_emit_drops_without_raising(self):
        resolver = NoopOutputWiringResolver()
        entries = [OutputWiringEntry(to="runner")]
        # Should complete cleanly for multiple sources.
        asyncio.run(
            resolver.emit(
                source="a",
                content="x",
                source_event_type="user_input",
                turn_index=1,
                entries=entries,
            )
        )
        # Second emission from the same source — also no raise, no log.
        asyncio.run(
            resolver.emit(
                source="a",
                content="y",
                source_event_type="user_input",
                turn_index=2,
                entries=entries,
            )
        )

    def test_emit_tracks_sources_logged_once(self):
        resolver = NoopOutputWiringResolver()
        entries = [OutputWiringEntry(to="runner")]

        async def _fire():
            await resolver.emit(
                source="alpha",
                content="x",
                source_event_type="user_input",
                turn_index=1,
                entries=entries,
            )
            await resolver.emit(
                source="alpha",
                content="y",
                source_event_type="user_input",
                turn_index=2,
                entries=entries,
            )
            await resolver.emit(
                source="beta",
                content="z",
                source_event_type="user_input",
                turn_index=1,
                entries=entries,
            )

        asyncio.run(_fire())

        # Internal state tracks one entry per distinct source.
        # Each source has been logged exactly once.
        assert resolver._logged_sources == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# Event helper
# ---------------------------------------------------------------------------


class TestCreateCreatureOutputEvent:
    def test_shape(self):
        event = create_creature_output_event(
            source="coder",
            target="runner",
            content="final answer",
            source_event_type="user_input",
            turn_index=7,
            prompt_override="[Output from coder] final answer",
        )
        assert isinstance(event, TriggerEvent)
        assert event.type == EventType.CREATURE_OUTPUT
        assert event.type == "creature_output"
        assert event.content == "final answer"
        assert event.context["source"] == "coder"
        assert event.context["target"] == "runner"
        assert event.context["with_content"] is True
        assert event.context["source_event_type"] == "user_input"
        assert event.context["turn_index"] == 7
        assert event.prompt_override == "[Output from coder] final answer"
        assert event.stackable is True

    def test_with_content_false(self):
        event = create_creature_output_event(
            source="coder",
            target="root",
            content="",
            with_content=False,
        )
        assert event.content == ""
        assert event.context["with_content"] is False
        assert event.context["target"] == "root"
