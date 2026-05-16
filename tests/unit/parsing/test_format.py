"""Unit tests for :mod:`kohakuterrarium.parsing.format`.

``ToolCallFormat`` controls how the stream parser detects tool-call
boundaries; ``format_tool_call_example`` produces the prompt-side
example strings.  Mismatches between the two break tool calling
silently — every combination must produce a string the parser can
re-tokenise.
"""

import pytest

from kohakuterrarium.parsing.format import (
    BRACKET_FORMAT,
    XML_FORMAT,
    ToolCallFormat,
    format_tool_call_example,
)


class TestToolCallFormatDataclass:
    def test_default_is_bracket_style(self):
        f = ToolCallFormat()
        assert f.start_char == "["
        assert f.end_char == "]"
        assert f.slash_means_open is True
        assert f.arg_style == "line"
        assert f.arg_prefix == "@@"
        assert f.arg_kv_sep == "="

    def test_frozen_dataclass_rejects_mutation(self):
        f = ToolCallFormat()
        with pytest.raises(Exception):
            f.start_char = "X"

    def test_preset_bracket_matches_default(self):
        assert BRACKET_FORMAT == ToolCallFormat()

    def test_preset_xml(self):
        assert XML_FORMAT.start_char == "<"
        assert XML_FORMAT.end_char == ">"
        assert XML_FORMAT.slash_means_open is False
        assert XML_FORMAT.arg_style == "inline"
        assert XML_FORMAT.arg_prefix == ""


class TestFormatToolCallExampleBracket:
    def test_no_args_no_body(self):
        out = format_tool_call_example(BRACKET_FORMAT, "tool")
        assert out == "[/tool]\n[tool/]"

    def test_line_args(self):
        out = format_tool_call_example(BRACKET_FORMAT, "tool", {"k": "v", "x": "1"})
        assert out == "[/tool]\n@@k=v\n@@x=1\n[tool/]"

    def test_with_body(self):
        out = format_tool_call_example(BRACKET_FORMAT, "tool", body="hello")
        assert out == "[/tool]\nhello\n[tool/]"

    def test_args_and_body_together(self):
        out = format_tool_call_example(
            BRACKET_FORMAT, "tool", args={"k": "v"}, body="hello"
        )
        assert out == "[/tool]\n@@k=v\nhello\n[tool/]"

    def test_empty_args_dict_does_not_add_arg_lines(self):
        out = format_tool_call_example(BRACKET_FORMAT, "tool", args={}, body="body")
        # Empty dict → no @@ lines.
        assert out == "[/tool]\nbody\n[tool/]"


class TestFormatToolCallExampleXml:
    def test_xml_no_args(self):
        out = format_tool_call_example(XML_FORMAT, "tool")
        assert out == "<tool>\n</tool>"

    def test_xml_inline_args(self):
        out = format_tool_call_example(XML_FORMAT, "tool", args={"k": "v"})
        assert out == '<tool k="v">\n</tool>'

    def test_xml_inline_args_with_body(self):
        out = format_tool_call_example(
            XML_FORMAT, "tool", args={"k": "v"}, body="content"
        )
        assert out == '<tool k="v">\ncontent\n</tool>'

    def test_xml_multiple_inline_args_preserved_in_order(self):
        out = format_tool_call_example(XML_FORMAT, "tool", args={"a": "1", "b": "2"})
        assert out == '<tool a="1" b="2">\n</tool>'


class TestCustomFormat:
    def test_custom_bracket_with_inline_args(self):
        # An unusual combination: bracket delimiters + inline args.
        fmt = ToolCallFormat(arg_style="inline")
        out = format_tool_call_example(fmt, "tool", args={"k": "v"})
        # ``slash_means_open=True`` keeps the bracket open-form: [/name ...]
        assert out == '[/tool k="v"]\n[tool/]'

    def test_custom_xml_with_line_args(self):
        # Reverse pairing — XML delims + line-style args.
        fmt = ToolCallFormat(
            start_char="<",
            end_char=">",
            slash_means_open=False,
            arg_style="line",
            arg_prefix="@@",
            arg_kv_sep=":",
        )
        out = format_tool_call_example(fmt, "tool", args={"k": "v"})
        # Open tag is XML form (since slash_means_open=False), but
        # args are line-form.
        assert out == "<tool>\n@@k:v\n</tool>"
