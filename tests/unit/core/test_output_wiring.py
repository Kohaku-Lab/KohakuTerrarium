"""Unit tests for :mod:`kohakuterrarium.core.output_wiring` (entries +
parser + prompt renderer + noop resolver)."""

import pytest

from kohakuterrarium.core.output_wiring import (
    DEFAULT_PROMPT_WITHOUT_CONTENT,
    DEFAULT_PROMPT_WITH_CONTENT,
    NoopOutputWiringResolver,
    OutputWiringEntry,
    OutputWiringResolver,
    PROMPT_FORMAT_JINJA,
    PROMPT_FORMAT_SIMPLE,
    ROOT_TARGET,
    _EmissionContext,
    parse_wiring_entry,
    parse_wiring_list,
    render_prompt,
    wiring_targets,
)

# ── OutputWiringEntry ────────────────────────────────────────────────


class TestOutputWiringEntry:
    def test_minimal_ok(self):
        e = OutputWiringEntry(to="bob")
        assert e.to == "bob"
        assert e.with_content is True
        assert e.prompt is None
        assert e.prompt_format == PROMPT_FORMAT_SIMPLE
        assert e.allow_self_trigger is False

    def test_empty_to_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            OutputWiringEntry(to="")

    def test_non_string_to_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            OutputWiringEntry(to=None)

    def test_invalid_prompt_format_raises(self):
        with pytest.raises(ValueError, match="prompt_format"):
            OutputWiringEntry(to="x", prompt_format="yaml")

    def test_jinja_format_accepted(self):
        OutputWiringEntry(to="x", prompt_format=PROMPT_FORMAT_JINJA)

    def test_root_target_string_is_just_a_name(self):
        # ``ROOT_TARGET`` is sugar; nothing magical at the dataclass level.
        e = OutputWiringEntry(to=ROOT_TARGET)
        assert e.to == "root"


# ── parse_wiring_entry / parse_wiring_list ───────────────────────────


class TestParseWiringEntry:
    def test_string_shorthand(self):
        e = parse_wiring_entry("bob")
        assert e == OutputWiringEntry(to="bob")

    def test_full_dict_form(self):
        e = parse_wiring_entry(
            {
                "to": "bob",
                "with_content": False,
                "prompt": "hello {source}",
                "prompt_format": "jinja",
                "allow_self_trigger": True,
            }
        )
        assert e.to == "bob"
        assert e.with_content is False
        assert e.prompt == "hello {source}"
        assert e.prompt_format == "jinja"
        assert e.allow_self_trigger is True

    def test_missing_to_field_raises(self):
        with pytest.raises(ValueError, match="missing required 'to' field"):
            parse_wiring_entry({"with_content": True})

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="string or mapping"):
            parse_wiring_entry(42)


class TestParseWiringList:
    def test_none_yields_empty(self):
        assert parse_wiring_list(None) == []

    def test_empty_list_ok(self):
        assert parse_wiring_list([]) == []

    def test_list_of_strings(self):
        out = parse_wiring_list(["alice", "bob"])
        assert [e.to for e in out] == ["alice", "bob"]

    def test_list_of_mixed_shapes(self):
        out = parse_wiring_list(["alice", {"to": "bob", "with_content": False}])
        assert out[0].with_content is True
        assert out[1].with_content is False

    def test_non_list_raises(self):
        with pytest.raises(ValueError, match="must be a list"):
            parse_wiring_list({"to": "bob"})


# ── render_prompt ────────────────────────────────────────────────────


def _kw(**over):
    base = dict(
        source="alice",
        target="bob",
        content="hello",
        turn_index=1,
        source_event_type="user_input",
    )
    base.update(over)
    return base


class TestRenderPromptSimple:
    def test_default_with_content_template(self):
        e = OutputWiringEntry(to="bob")
        out = render_prompt(e, **_kw())
        # Same as the documented default.
        assert out == DEFAULT_PROMPT_WITH_CONTENT.format(
            source="alice", content="hello"
        )

    def test_default_without_content_template(self):
        e = OutputWiringEntry(to="bob", with_content=False)
        out = render_prompt(e, **_kw(content=""))
        assert out == DEFAULT_PROMPT_WITHOUT_CONTENT.format(source="alice")

    def test_custom_template_simple(self):
        e = OutputWiringEntry(to="bob", prompt="hi {source}, said: {content}")
        out = render_prompt(e, **_kw())
        assert out == "hi alice, said: hello"

    def test_missing_key_renders_as_empty(self):
        # The _SafeFormatDict default — ``{missing}`` → "" instead of KeyError.
        e = OutputWiringEntry(to="bob", prompt="x:{missing}:y")
        out = render_prompt(e, **_kw())
        assert out == "x::y"

    def test_all_variables_available(self):
        e = OutputWiringEntry(
            to="bob",
            prompt=(
                "s={source} t={target} c={content} i={turn_index} "
                "evt={source_event_type} wc={with_content}"
            ),
        )
        out = render_prompt(e, **_kw())
        assert "s=alice" in out
        assert "t=bob" in out
        assert "c=hello" in out
        assert "i=1" in out
        assert "evt=user_input" in out
        assert "wc=True" in out


class TestRenderPromptJinja:
    def test_jinja_template(self):
        e = OutputWiringEntry(
            to="bob",
            prompt="Hello {{ source }}!",
            prompt_format=PROMPT_FORMAT_JINJA,
        )
        out = render_prompt(e, **_kw())
        assert out == "Hello alice!"


# ── wiring_targets ───────────────────────────────────────────────────


class TestWiringTargets:
    def test_preserves_order(self):
        out = wiring_targets(
            [
                OutputWiringEntry(to="alice"),
                OutputWiringEntry(to="bob"),
                OutputWiringEntry(to="carol"),
            ]
        )
        assert out == ["alice", "bob", "carol"]

    def test_empty(self):
        assert wiring_targets([]) == []


# ── NoopOutputWiringResolver ─────────────────────────────────────────


class TestNoopResolver:
    async def test_first_call_logs(self, caplog):
        import logging

        r = NoopOutputWiringResolver()
        with caplog.at_level(logging.INFO, logger="kohakuterrarium.core.output_wiring"):
            await r.emit(
                source="alice",
                content="x",
                source_event_type="user_input",
                turn_index=1,
                entries=[OutputWiringEntry(to="bob")],
            )
        # Caplog won't catch records that propagate=False blocks; rely on the
        # internal _logged_sources set instead.
        assert "alice" in r._logged_sources

    async def test_second_call_per_source_is_silent(self):
        r = NoopOutputWiringResolver()
        await r.emit(
            source="alice",
            content="x",
            source_event_type="user_input",
            turn_index=1,
            entries=[OutputWiringEntry(to="bob")],
        )
        await r.emit(
            source="alice",
            content="y",
            source_event_type="user_input",
            turn_index=2,
            entries=[OutputWiringEntry(to="bob")],
        )
        # Still only one source logged — set unchanged size.
        assert r._logged_sources == {"alice"}

    async def test_per_source_independence(self):
        r = NoopOutputWiringResolver()
        await r.emit(
            source="alice",
            content="x",
            source_event_type="t",
            turn_index=1,
            entries=[OutputWiringEntry(to="bob")],
        )
        await r.emit(
            source="carol",
            content="x",
            source_event_type="t",
            turn_index=1,
            entries=[OutputWiringEntry(to="bob")],
        )
        # Both got logged once.
        assert r._logged_sources == {"alice", "carol"}


class TestResolverProtocol:
    def test_noop_satisfies_protocol(self):
        r = NoopOutputWiringResolver()
        assert isinstance(r, OutputWiringResolver)


class TestEmissionContext:
    def test_construct(self):
        ctx = _EmissionContext(
            source="alice", content="x", source_event_type="t", turn_index=1
        )
        assert ctx.source == "alice"
        assert ctx.entries == []
