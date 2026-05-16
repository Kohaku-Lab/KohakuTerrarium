"""Unit tests for :mod:`kohakuterrarium.parsing.patterns`."""

from kohakuterrarium.parsing.format import BRACKET_FORMAT
from kohakuterrarium.parsing.patterns import (
    DEFAULT_COMMANDS,
    DEFAULT_CONTENT_ARG_MAP,
    DEFAULT_SUBAGENT_TAGS,
    ParserConfig,
    build_tool_args,
    is_command_tag,
    is_output_tag,
    is_subagent_tag,
    is_tool_tag,
    parse_attributes,
    parse_closing_tag,
    parse_opening_tag,
)

# ── ParserConfig defaults ─────────────────────────────────────────────


class TestParserConfig:
    def test_defaults(self):
        c = ParserConfig()
        assert c.emit_block_events is False
        assert c.buffer_text is True
        assert c.text_buffer_size == 1
        assert c.known_tools == set()
        # ``known_subagents`` defaults to a COPY of ``DEFAULT_SUBAGENT_TAGS``.
        assert c.known_subagents == DEFAULT_SUBAGENT_TAGS
        assert c.known_subagents is not DEFAULT_SUBAGENT_TAGS
        assert c.known_commands == DEFAULT_COMMANDS
        assert c.known_commands is not DEFAULT_COMMANDS
        # Content map copied too.
        assert c.content_arg_map == DEFAULT_CONTENT_ARG_MAP
        assert c.content_arg_map is not DEFAULT_CONTENT_ARG_MAP

    def test_default_format_is_bracket(self):
        c = ParserConfig()
        assert c.tool_format == BRACKET_FORMAT

    def test_each_instance_gets_own_collections(self):
        a = ParserConfig()
        b = ParserConfig()
        a.known_tools.add("bash")
        # Modifying ``a`` MUST NOT bleed into ``b``.
        assert b.known_tools == set()


# ── parse_attributes ──────────────────────────────────────────────────


class TestParseAttributes:
    def test_empty_string_returns_empty(self):
        assert parse_attributes("") == {}

    def test_single_attribute(self):
        assert parse_attributes('path="src/main.py"') == {"path": "src/main.py"}

    def test_multiple_attributes(self):
        out = parse_attributes(' path="x" limit="50"')
        assert out == {"path": "x", "limit": "50"}

    def test_attribute_with_spaces_around_equals(self):
        out = parse_attributes(' k  =  "value with spaces"')
        assert out == {"k": "value with spaces"}

    def test_no_attrs_no_match(self):
        # Garbage input produces no matches → empty dict.
        assert parse_attributes("not an attribute") == {}


# ── parse_opening_tag ────────────────────────────────────────────────


class TestParseOpeningTag:
    def test_bare_tag(self):
        out = parse_opening_tag("<bash>")
        assert out == ("bash", {}, False)

    def test_tag_with_attributes(self):
        out = parse_opening_tag('<edit path="x.py">')
        assert out == ("edit", {"path": "x.py"}, False)

    def test_self_closing_tag(self):
        out = parse_opening_tag("<read/>")
        assert out == ("read", {}, True)

    def test_self_closing_with_attrs(self):
        out = parse_opening_tag('<read path="x"/>')
        assert out == ("read", {"path": "x"}, True)

    def test_invalid_tag_returns_none(self):
        assert parse_opening_tag("<123>") is None
        assert parse_opening_tag("not a tag") is None

    def test_tag_with_dashes_rejected(self):
        # The regex only allows underscores in tag names — dash names
        # don't match.
        assert parse_opening_tag("<my-tag>") is None

    def test_multiple_attrs_order_preserved(self):
        out = parse_opening_tag('<x a="1" b="2" c="3">')
        assert out is not None
        name, attrs, _ = out
        assert name == "x"
        assert list(attrs.items()) == [("a", "1"), ("b", "2"), ("c", "3")]


# ── parse_closing_tag ────────────────────────────────────────────────


class TestParseClosingTag:
    def test_valid_closing_tag(self):
        assert parse_closing_tag("</bash>") == "bash"

    def test_with_underscores(self):
        assert parse_closing_tag("</send_message>") == "send_message"

    def test_invalid_returns_none(self):
        assert parse_closing_tag("not a tag") is None
        assert parse_closing_tag("<bash>") is None  # opening, not closing

    def test_dash_in_name_rejected(self):
        assert parse_closing_tag("</my-tag>") is None


# ── build_tool_args ──────────────────────────────────────────────────


class TestBuildToolArgs:
    def test_bash_content_becomes_command(self):
        out = build_tool_args("bash", {}, "ls -la")
        assert out == {"command": "ls -la"}

    def test_python_content_becomes_code(self):
        out = build_tool_args("python", {}, "print('hi')")
        assert out == {"code": "print('hi')"}

    def test_edit_content_becomes_diff(self):
        out = build_tool_args("edit", {"path": "x.py"}, "...diff...")
        assert out == {"path": "x.py", "diff": "...diff..."}

    def test_unknown_tool_content_becomes_content(self):
        out = build_tool_args("custom_tool", {}, "body")
        assert out == {"content": "body"}

    def test_content_stripped(self):
        out = build_tool_args("bash", {}, "   ls  \n  ")
        assert out == {"command": "ls"}

    def test_empty_content_omitted(self):
        out = build_tool_args("bash", {"command": "preset"}, "")
        assert out == {"command": "preset"}

    def test_existing_attribute_not_overridden(self):
        # If ``command`` is already in attributes, content does NOT
        # override.  Attribute wins.
        out = build_tool_args("bash", {"command": "via_attr"}, "via_content")
        assert out == {"command": "via_attr"}

    def test_custom_content_arg_map(self):
        custom_map = {"toot": "noise"}
        out = build_tool_args("toot", {}, "honk", content_arg_map=custom_map)
        assert out == {"noise": "honk"}

    def test_custom_map_falls_back_to_default_for_unknown(self):
        # Custom map doesn't have ``bash``; map does NOT auto-merge with
        # the default.  Unknown tool → ``content``.
        custom_map = {"toot": "noise"}
        out = build_tool_args("bash", {}, "ls", content_arg_map=custom_map)
        # ``content_arg_map.get(tag_name, "content")`` → defaults to
        # ``"content"`` since ``bash`` not in the custom map.
        assert out == {"content": "ls"}


# ── is_tool_tag / is_subagent_tag / is_command_tag ───────────────────


class TestTagClassifiers:
    def test_is_tool_tag_requires_registry(self):
        assert is_tool_tag("bash") is False
        assert is_tool_tag("bash", known_tools={"bash"}) is True

    def test_is_tool_tag_unknown_returns_false(self):
        assert is_tool_tag("ghost", known_tools={"bash"}) is False

    def test_is_subagent_tag_default_set(self):
        # No registry → falls back to DEFAULT_SUBAGENT_TAGS.
        assert is_subagent_tag("agent") is True
        assert is_subagent_tag("not_an_agent") is False

    def test_is_subagent_tag_custom_set(self):
        assert is_subagent_tag("planner", known_subagents={"planner"}) is True
        assert is_subagent_tag("agent", known_subagents={"planner"}) is False

    def test_is_command_tag_default(self):
        for cmd in DEFAULT_COMMANDS:
            assert is_command_tag(cmd) is True
        assert is_command_tag("not_a_command") is False

    def test_is_command_tag_custom(self):
        assert is_command_tag("foo", known_commands={"foo"}) is True
        assert is_command_tag("info", known_commands={"foo"}) is False


# ── is_output_tag ────────────────────────────────────────────────────


class TestIsOutputTag:
    def test_non_output_prefix_returns_false(self):
        assert is_output_tag("bash") == (False, "")

    def test_just_prefix_no_target_rejected(self):
        assert is_output_tag("output_") == (False, "")

    def test_unknown_target_rejected_when_registry_provided(self):
        assert is_output_tag("output_discord", known_outputs={"tts"}) == (False, "")

    def test_known_target_allowed(self):
        assert is_output_tag("output_discord", known_outputs={"discord"}) == (
            True,
            "discord",
        )

    def test_any_target_allowed_when_registry_is_none(self):
        # None registry → permissive; any non-empty target accepted.
        assert is_output_tag("output_discord") == (True, "discord")
