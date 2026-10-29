"""kt.ui inline markup: edge cases beyond the shared conformance file.

Covers :mod:`kohakuterrarium.builtins.kt_ui.markup` and
:mod:`kohakuterrarium.builtins.kt_ui.markup_lex`.
"""

import pytest

from kohakuterrarium.builtins.kt_ui.expr import evaluate, parse
from kohakuterrarium.builtins.kt_ui.markup import (
    MarkupError,
    check_block,
    convert_block,
    unparse,
)
from kohakuterrarium.builtins.kt_ui.markup_lex import lex, split_message
from kohakuterrarium.builtins.kt_ui.spec import SpecError


def _kinds(text):
    return [(s["kind"], s.get("closed")) for s in split_message(text)]


class TestSplit:
    def test_tilde_fences_and_longer_closing_fences(self):
        text = "~~~\n<ui><text>a</text></ui>\n~~~~\n<ui><text>b</text></ui>"
        assert _kinds(text) == [("markdown", None), ("ui", True)]

    def test_a_shorter_fence_does_not_close_a_longer_one(self):
        text = "````\n```\n<ui><text>a</text></ui>\n````"
        assert _kinds(text) == [("markdown", None)]

    def test_not_a_block_start(self):
        for text in ("<uix>a</uix>", "    <ui><text>a</text></ui>", "x <ui>a</ui>"):
            assert _kinds(text) == [("markdown", None)], text

    def test_a_second_block_on_the_same_line_is_text(self):
        text = "<ui><text>a</text></ui> <ui><text>b</text></ui>"
        segments = split_message(text)
        assert [s["kind"] for s in segments] == ["ui", "markdown"]
        assert segments[1]["text"] == " <ui><text>b</text></ui>"

    def test_self_closing_block(self):
        assert split_message("<ui/>\nafter") == [
            {"kind": "ui", "source": "<ui/>", "closed": True},
            {"kind": "markdown", "text": "\nafter"},
        ]

    def test_malformed_block_ends_at_the_next_close(self):
        text = "<ui><stat label=x/></ui>\nafter"
        segments = split_message(text)
        assert segments[0] == {
            "kind": "ui",
            "source": "<ui><stat label=x/></ui>",
            "closed": True,
        }
        assert segments[1]["text"] == "\nafter"
        malformed_open = split_message("<ui><stat label=x/>")
        assert malformed_open[0]["closed"] is False

    def test_empty_text(self):
        assert split_message("") == []


class TestLex:
    def test_unfinished_tag_and_string_are_reported(self):
        assert lex("<ui><stat label=").incomplete == "unfinished tag"
        assert lex('<ui><stat label="Ea').incomplete == "unterminated string"
        assert lex("<ui><stat value={fmt(").incomplete == "unterminated expression"
        assert lex("<ui></ui").incomplete == "unfinished tag"
        assert lex("<ui><stat /").incomplete == "unfinished tag"

    def test_bad_tags_raise(self):
        with pytest.raises(MarkupError, match="bad character"):
            lex("<ui><stat ?></ui>")
        with pytest.raises(MarkupError, match="bad closing tag"):
            lex("<ui></ui x>")

    def test_a_lone_angle_bracket_is_text(self):
        tokens = lex("<ui>a < b and 1<2</ui>").tokens
        assert [t.kind for t in tokens] == ["open", "text", "close"]
        assert tokens[1].text == "a < b and 1<2"

    def test_text_element_without_its_close_stops_at_another_close(self):
        tokens = lex("<ui><text>x</card></ui>").tokens
        assert tokens[2].text == "x" and tokens[3].name == "card"


class TestConvert:
    def test_set_map_keeps_expressions_as_source(self):
        spec = convert_block(
            '<ui><state n={0} s="a"/>'
            '<button set={{n: n + 1, "s": s + "!"}}>Add</button></ui>'
        )
        button = spec["root"]
        assert button["label"] == "Add"
        assert button["set"] == {"n": "(n + 1)", "s": '(s + "!")'}
        check_block(
            '<ui><state n={0} s="a"/>'
            '<button set={{n: n + 1, "s": s + "!"}}>Add</button></ui>'
        )

    def test_set_map_needs_an_object(self):
        with pytest.raises(MarkupError, match="needs"):
            convert_block("<ui><state n={0}/><button set={n}>x</button></ui>")
        with pytest.raises(MarkupError, match="needs"):
            convert_block('<ui><button set="n">x</button></ui>')

    def test_form_send_becomes_a_message_template(self):
        source = (
            '<ui><state day=""/><form send="Book {day}" submit="Book">'
            '<date label="Day" bind="day"/></form></ui>'
        )
        form = check_block(source).spec["root"]
        assert form["message"] == "Book {{ day }}" and "send" not in form

    def test_form_needs_send_or_message(self):
        with pytest.raises(SpecError, match="a form needs 'send' or 'message'"):
            check_block('<ui><state d=""/><form><date bind="d"/></form></ui>')

    def test_unknown_component_is_kept_for_the_validator(self):
        spec = convert_block('<ui><blink speed="2">hi</blink></ui>')
        assert spec["root"] == {
            "type": "blink",
            "speed": "2",
            "children": [{"type": "text", "value": "hi"}],
        }
        with pytest.raises(SpecError, match="unknown component 'blink'"):
            check_block('<ui><blink speed="2">hi</blink></ui>')

    def test_structure_errors(self):
        cases = {
            "<ui><ui></ui></ui>": "cannot be nested",
            "<ui><card><state a={1}/></card></ui>": "directly inside <ui>",
            "<ui><text>a</text></ui><text>b</text>": "content after </ui>",
            "<card></card>": "a block starts with <ui>",
            "plain": "a block starts with <ui>",
            "<ui><number bind='a'><text>x</text></number></ui>": "takes no children",
            "<ui><card title={1/0}></card></ui>": "bad expression",
            "<ui><card title={)}></card></ui>": "bad expression",
            "<ui><card children={1}></card></ui>": "set by child elements",
        }
        for source, fragment in cases.items():
            with pytest.raises(MarkupError, match=fragment):
                convert_block(source)

    def test_whitespace_around_and_after_the_block_is_allowed(self):
        assert convert_block("  \n<ui><text>a</text></ui>\n  ") == {
            "root": {"type": "text", "value": "a"}
        }

    def test_a_closed_block_with_an_open_element_is_an_error(self):
        with pytest.raises(MarkupError, match="mismatched"):
            convert_block("<ui><card></ui>")

    def test_streaming_text_drops_an_unfinished_expression(self):
        assert convert_block("<ui><text>Total {fmt(")["root"] == {
            "type": "text",
            "value": "Total",
        }
        with pytest.raises(MarkupError, match="unterminated expression in text"):
            convert_block("<ui><text>Total {fmt(</text></ui>")

    def test_attribute_values(self):
        spec = convert_block(
            "<ui><state n={1}/>"
            '<chart kind="line" series={[{name: "x", data: [1.5, 2]}]} height={120}'
            ' title="n is {n}"/></ui>'
        )
        chart = spec["root"]
        assert chart["series"] == [{"name": "x", "data": [1.5, 2]}]
        assert chart["height"] == 120 and isinstance(chart["height"], int)
        assert chart["title"] == "n is {{ n }}"
        big = convert_block("<ui><stat value={2 ** 60}/></ui>")["root"]["value"]
        assert big == 2.0**60 and isinstance(big, float)

    def test_root_attributes(self):
        spec = convert_block('<ui title="T {x}" layout="page" wide extra={2}/>')
        assert spec == {
            "title": "T {{ x }}",
            "layout": "page",
            "wide": True,
            "extra": 2,
        }
        assert convert_block("<ui note={x}/>") == {"note": "x"}
        with pytest.raises(SpecError, match="root"):
            check_block("<ui/>")

    def test_declarations(self):
        spec = convert_block(
            '<ui><state on name="Ada" n={-1}/><let flag ok={on and n < 0}/>'
            "<text>x</text></ui>"
        )
        assert spec["state"] == {"on": True, "name": "Ada", "n": -1}
        assert spec["computed"] == {"flag": "true", "ok": "on and n < 0"}
        with pytest.raises(MarkupError, match="must be a constant"):
            convert_block("<ui><state a={b}/></ui>")

    def test_unknown_props_keep_their_values(self):
        spec = convert_block('<ui><text glow tint="{c}" size={big}>x</text></ui>')
        assert spec["root"] == {
            "type": "text",
            "glow": True,
            "tint": "{{ c }}",
            "size": "{{ big }}",
            "value": "x",
        }

    def test_empty_children_and_blank_text(self):
        assert convert_block("<ui><card>  \n  </card></ui>")["root"] == {
            "type": "card",
            "children": [],
        }
        assert convert_block("<ui><each items={xs}></each></ui>")["root"] == {
            "type": "each",
            "items": "xs",
        }
        assert convert_block("<ui><text>  </text></ui>")["root"] == {"type": "text"}


EXPRESSIONS = [
    "1 + 2 * 3",
    "-(a) + +b",
    "not a and b or c",
    "a ? 'yes' : \"no\"",
    "{k: [1, 2.5, true, null], 'a b': x.y[0]}",
    "fmt(sum([a, b]), 2)",
    "a ** 2 >= 4",
]


@pytest.mark.parametrize("source", EXPRESSIONS)
def test_unparse_keeps_meaning(source):
    scope = {"a": 2, "b": 3, "c": False, "x": {"y": [7]}}
    node = parse(source)
    assert evaluate(unparse(node), scope) == evaluate(node, scope)
