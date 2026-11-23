"""Unit tests for ``kohakuterrarium.browser.snapshot``: parse, clean, filter, render."""

from pathlib import Path

import pytest

from kohakuterrarium.browser import snapshot as snap
from kohakuterrarium.errors import NotFoundError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "browser" / "snapshots"
NAMES = ("controls", "controls_after", "edge", "frames", "hidden")


def recorded(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8").rstrip("\n")


def cleaned(name: str) -> list[snap.Node]:
    return snap.clean(snap.parse(recorded(name)))


def text_of(nodes, **filters) -> str:
    return snap.render(nodes, **filters).text


class TestParse:
    @pytest.mark.parametrize("name", NAMES)
    def test_recorded_snapshots_round_trip(self, name):
        raw = recorded(name)
        assert snap.to_text(snap.parse(raw)) == raw

    def test_fields_of_an_element_line(self):
        (node,) = snap.parse(
            '- checkbox "Subscribe" [checked] [ref=e9] [cursor=pointer]'
        )
        assert node.kind == "element"
        assert node.role == "checkbox"
        assert node.name == "Subscribe"
        assert node.ref == "e9"
        assert node.attr("checked") is None and node.has_attr("checked")
        assert node.attr("cursor") == "pointer"
        assert node.fingerprint == ("checkbox", "Subscribe")

    def test_children_props_and_inline_values(self):
        nodes = snap.parse(recorded("controls"))
        textbox = snap.index_refs(nodes)["e17"]
        assert [(c.kind, c.role, c.value) for c in textbox.children] == [
            ("prop", "/placeholder", "Your name"),
            ("text", "text", "Ada"),
        ]
        assert snap.index_refs(nodes)["e14"].value == (
            "Update your details below. Status: idle"
        )

    def test_quoted_key_and_escaped_name(self):
        nodes = snap.parse(recorded("controls"))
        button = snap.index_refs(nodes)["e53"]
        assert button.name == 'Quote "this": yes'
        assert button.value == "Q"

    def test_double_quoted_value_escapes_decode(self):
        (node,) = snap.parse(r'- paragraph: "tab\there \"q\" \x41 é back\\slash"')
        assert node.value == 'tab\there "q" A é back\\slash'
        assert snap.to_text([node]) == '- paragraph: tab\there "q" A é back\\slash'
        (quoted,) = snap.parse(r'- paragraph: "a: \"b\"\nc"')
        assert quoted.value == 'a: "b"\nc'
        assert snap.to_text([quoted]) == r'- paragraph: "a: \"b\"\nc"'

    def test_frame_refs_keep_their_prefix(self):
        refs = snap.index_refs(snap.parse(recorded("frames")))
        assert refs["f1e2"].name == "Frame button"
        assert refs["f2e2"].name == "Frame button"
        assert refs["e3"].role == "iframe"

    @pytest.mark.parametrize(
        "line",
        [
            '- button "unterminated name',
            "- ???weird [ref=e5",
            "- 'unterminated quoted key",
            '- paragraph: "unterminated value',
            "plain text without a dash",
            "- ",
            '- button "ok" trailing-garbage',
            '- button "ok" [ref=e1] junk:',
        ],
    )
    def test_unknown_line_shapes_survive_round_trip(self, line):
        text = f'- main [ref=e1]:\n  {line}\n  - button "After" [ref=e2]'
        nodes = snap.parse(text)
        assert snap.to_text(nodes) == text
        opaque = nodes[0].children[0]
        assert opaque.kind == "opaque"
        assert snap.index_refs(nodes)["e2"].name == "After"

    def test_opaque_line_with_children_keeps_them(self):
        text = '- ~~mystery~~:\n  - button "Inside" [ref=e4]'
        nodes = snap.parse(text)
        assert snap.to_text(nodes) == text
        assert snap.index_refs(nodes)["e4"].role == "button"

    def test_blank_lines_and_odd_indent(self):
        nodes = snap.parse(
            '- main:\n\n     - button "Deep" [ref=e7]\n- link "Top" [ref=e8]'
        )
        assert [n.role for n in nodes] == ["main", "link"]
        assert nodes[0].children[0].name == "Deep"

    def test_crlf_input(self):
        nodes = snap.parse('- main:\r\n  - button "X" [ref=e1]\r\n')
        assert snap.index_refs(nodes)["e1"].name == "X"

    def test_uid_keeps_parsed_ref_through_cleaning_but_never_renders(self):
        nodes = snap.clean(snap.parse(recorded("controls")))
        paragraph = next(n for n, _, _ in snap.walk(nodes) if n.role == "paragraph")
        assert paragraph.ref is None and paragraph.uid == "e14"
        assert "e14" not in snap.to_text(nodes)
        assert snap.Node(role="button") == snap.Node(role="button", uid="e1")

    def test_quoted_key_with_doubled_single_quote(self):
        line = "- 'button \"It''s: here\" [ref=e1]': v"
        (node,) = snap.parse(line)
        assert node.name == "It's: here" and node.value == "v"
        assert snap.to_text([node]) == line

    def test_quoted_key_with_leftover_is_opaque(self):
        line = "- 'button \"A: b\" junk': v"
        (node,) = snap.parse(line)
        assert node.kind == "opaque" and snap.to_text([node]) == line

    def test_nesting_beyond_max_depth_is_flattened_not_fatal(self):
        deep = "\n".join("  " * i + f"- group [ref=e{i}]:" for i in range(500))
        nodes = snap.parse(deep + "\n" + "  " * 500 + '- button "Bottom" [ref=e999]')
        depths = [depth for _, depth, _ in snap.walk(nodes)]
        assert max(depths) < snap.MAX_DEPTH
        assert snap.index_refs(nodes)["e999"].name == "Bottom"
        assert len(snap.index_refs(nodes)) == 501
        assert "Bottom" in snap.render(snap.clean(nodes)).text


class TestCleaning:
    def test_drop_aria_hidden_removes_subtree(self):
        nodes = snap.parse(
            '- main:\n  - generic [aria-hidden] [ref=e3]:\n    - button "Inner" [ref=e4]\n'
            '  - button "Kept" [ref=e5]'
        )
        out = snap.drop_aria_hidden(nodes)
        assert snap.to_text(out) == '- main:\n  - button "Kept" [ref=e5]'
        assert "e4" in snap.index_refs(nodes), "input tree is not mutated"

    def test_prune_refs_keeps_actionable_and_containers(self):
        out = snap.prune_refs(snap.parse(recorded("controls")))
        refs = snap.index_refs(out)
        for kept in (
            "e3",
            "e9",
            "e15",
            "e17",
            "e21",
            "e25",
            "e28",
            "e29",
            "e31",
            "e33",
            "e42",
            "e52",
        ):
            assert kept in refs, kept
        for dropped in ("e1", "e5", "e13", "e14", "e16", "e34", "e39", "e41", "e49"):
            assert dropped not in refs, dropped

    def test_prune_refs_keeps_state_bearing_unknown_roles(self):
        (node,) = snap.prune_refs(snap.parse('- widgetx "W" [expanded] [ref=e4]'))
        assert node.ref == "e4"
        (img,) = snap.prune_refs(snap.parse('- img "Logo" [ref=e5]'))
        assert img.ref is None

    def test_strip_noise_drops_cursor_and_inactive_focus(self):
        out = snap.strip_noise(
            snap.parse(
                "- generic [active] [ref=e1]:\n"
                '  - link "A" [ref=e2] [cursor=pointer]\n'
                '  - textbox "B" [active] [ref=e3]\n'
                "  - generic [ref=e4] [box=1,2,3,4]: x"
            )
        )
        assert snap.to_text(out) == (
            "- generic [ref=e1]:\n"
            '  - link "A" [ref=e2]\n'
            '  - textbox "B" [active] [ref=e3]\n'
            "  - generic [ref=e4] [box=1,2,3,4]: x"
        )

    def test_label_echo_text_is_dropped_only_next_to_its_control(self):
        out = snap.drop_label_echo(snap.parse(recorded("controls")))
        group = snap.to_text(out)
        assert "- text: Name" not in group
        assert "- text: Subscribe to news" not in group
        assert "- text: Second item with" in group, "unrelated text stays"
        lone = snap.drop_label_echo(
            snap.parse("- generic:\n  - text: Save\n  - paragraph: Save")
        )
        assert snap.to_text(lone) == "- generic:\n  - text: Save\n  - paragraph: Save"

    def test_collapse_wrappers_rules(self):
        nodes = snap.parse(
            "- generic:\n"
            "  - generic:\n"
            '    - button "One" [ref=e2]\n'
            "  - generic:\n"
            '    - button "Two" [ref=e3]\n'
            '    - button "Three" [ref=e4]\n'
            '  - generic "Named":\n'
            '    - button "Four" [ref=e5]\n'
            "  - generic [ref=e6]:\n"
            '    - button "Five" [ref=e7]'
        )
        assert snap.to_text(snap.collapse_wrappers(nodes)) == (
            '- button "One" [ref=e2]\n'
            "- generic:\n"
            '  - button "Two" [ref=e3]\n'
            '  - button "Three" [ref=e4]\n'
            '- generic "Named":\n'
            '  - button "Four" [ref=e5]\n'
            "- generic [ref=e6]:\n"
            '  - button "Five" [ref=e7]'
        )

    def test_collapse_keeps_generic_with_text(self):
        nodes = snap.parse("- generic: hello\n- generic:\n  - text: a")
        out = snap.collapse_wrappers(nodes)
        assert snap.to_text(out) == "- generic: hello\n- text: a"

    def test_clean_controls_end_to_end(self):
        text = snap.to_text(cleaned("controls"))
        assert text.startswith("- banner [ref=e2]:\n")
        assert "[cursor=pointer]" not in text
        assert "[active]" not in text
        assert '- textbox "Name" [ref=e17]:' in text
        assert "- generic [ref=e52]: Clickable card" in text
        assert (
            "[ref=e13]" not in text and '- heading "Account settings" [level=1]' in text
        )
        assert '- radio "Free" [checked] [ref=e25]' in text

    @pytest.mark.parametrize("name", NAMES)
    def test_cleaning_shrinks_every_fixture(self, name):
        raw = recorded(name)
        assert len(snap.to_text(snap.clean(snap.parse(raw)))) < len(raw)


class TestFilters:
    def test_ref_selects_subtree(self):
        out = text_of(cleaned("controls"), ref="e31")
        assert out.splitlines()[0] == "- table [ref=e31]:"
        assert "Alice" in out and "Account settings" not in out

    def test_unknown_ref_raises_not_found(self):
        with pytest.raises(snap.UnknownRefError) as err:
            snap.render(cleaned("controls"), ref="e999")
        assert isinstance(err.value, NotFoundError)
        assert "e999" in str(err.value)

    def test_ref_pruned_by_cleaning_is_unknown(self):
        with pytest.raises(snap.UnknownRefError):
            snap.render(cleaned("controls"), ref="e14")

    def test_depth_limits_levels_but_keeps_props(self):
        out = text_of(cleaned("controls"), depth=1)
        assert out.splitlines() == ["- banner [ref=e2]", "- main [ref=e9]"]
        links = text_of(
            snap.parse('- link "A" [ref=e1]:\n  - /url: /a\n  - img "x"'), depth=1
        )
        assert links == '- link "A" [ref=e1]:\n  - /url: /a'

    def test_depth_zero_or_negative_is_rejected(self):
        with pytest.raises(ValueError):
            snap.render(cleaned("controls"), depth=0)

    def test_query_keeps_ancestor_chain_and_match_subtree(self):
        out = text_of(cleaned("controls"), query="alice")
        assert out.splitlines() == [
            "- main [ref=e9]:",
            "  - table [ref=e31]:",
            "    - rowgroup:",
            "      - row [ref=e38]:",
            '        - cell "Alice"',
        ]

    def test_query_matches_url_and_value(self):
        assert "/docs?page=1&q=a" in text_of(cleaned("controls"), query="PAGE=1")
        out = text_of(cleaned("controls"), query="ada")
        assert '- textbox "Name" [ref=e17]:' in out and "- text: Ada" in out

    def test_query_with_no_match_is_empty(self):
        result = snap.render(cleaned("controls"), query="zzz-no-such-text")
        assert result.text == ""
        assert result.total_lines == 0 and result.cut_lines == 0

    def test_interactive_flattens_with_named_context(self):
        out = text_of(cleaned("controls"), interactive=True)
        lines = out.splitlines()
        assert '- navigation "Primary" [ref=e3]:' in lines
        assert '  - link "Home" [ref=e6]:' in lines
        assert '- form "Profile" [ref=e15]:' in lines
        assert '  - checkbox "Subscribe to news" [checked] [ref=e21]' in lines
        assert "- heading" not in out and "paragraph" not in out
        assert "- generic [ref=e52]: Clickable card" in lines
        assert "- row [ref=e38]:" in lines
        assert "  - text: Alice Admin" in lines, "unnamed rows carry their text"
        assert '  - button "Remove" [ref=e42]' in lines
        assert not [
            x for x in lines if x.lstrip().startswith("- list ")
        ], "unnamed containers are flattened away"
        assert "- listitem:\n  - text: Second item with\n  - link" in out
        assert "First item" not in out, "a list item with nothing to act on is gone"

    def test_interactive_keeps_frame_boundaries(self):
        out = text_of(cleaned("frames"), interactive=True)
        assert out.splitlines() == [
            "- iframe [ref=e3]:",
            '  - button "Frame button" [ref=f1e2]',
            '  - textbox "Frame input" [ref=f1e3]: inside',
            "- iframe [ref=e4]:",
            '  - button "Frame button" [ref=f2e2]',
            '  - textbox "Frame input" [ref=f2e3]: inside',
            '- button "Shadow button" [ref=e6]',
            '- textbox "Shadow input" [ref=e7]',
        ]

    def test_depth_two(self):
        out = text_of(cleaned("controls"), ref="e15", depth=2)
        assert out.splitlines()[:3] == [
            '- form "Profile" [ref=e15]:',
            '  - textbox "Name" [ref=e17]:',
            "    - /placeholder: Your name",
        ]

    def test_interactive_unnamed_clickable_carries_its_text(self):
        nodes = snap.clean(
            snap.parse(
                "- generic [ref=e4] [cursor=pointer]:\n"
                "  - paragraph [ref=e5]: Card title\n"
                '  - button "b" [ref=e6]'
            )
        )
        assert text_of(nodes, interactive=True).splitlines() == [
            "- generic [ref=e4]:",
            "  - text: Card title",
            '  - button "b" [ref=e6]',
        ]

    def test_long_summary_is_clipped(self):
        nodes = snap.parse(
            "- row [ref=e1]:\n  - cell: " + "w" * 100 + '\n  - button "Go" [ref=e2]'
        )
        summary = text_of(nodes, interactive=True).splitlines()[1]
        assert (
            summary.endswith("…")
            and len(summary) == len("  - text: ") + snap.SUMMARY_CHARS
        )

    def test_filters_compose(self):
        out = text_of(cleaned("controls"), ref="e9", query="remove", interactive=True)
        assert out.splitlines() == [
            "- row [ref=e38]:",
            "  - text: Alice Admin",
            '  - button "Remove" [ref=e42]',
            "- row [ref=e43]:",
            "  - text: Bob Viewer",
            '  - button "Remove" [ref=e47]',
        ]
        assert (
            text_of(cleaned("controls"), ref="e15", depth=1)
            == '- form "Profile" [ref=e15]'
        )


class TestTruncation:
    def test_cut_at_line_boundary_and_counts(self):
        nodes = cleaned("controls")
        full = snap.render(nodes)
        all_lines = full.text.splitlines()
        budget = len("\n".join(all_lines[:5])) + 3
        result = snap.render(nodes, max_chars=budget)
        assert result.text.splitlines() == all_lines[:5]
        assert result.cut_lines == len(all_lines) - 5
        assert result.total_lines == len(all_lines)
        cut_refs = sum("[ref=" in line for line in all_lines[5:])
        assert result.cut_refs == cut_refs > 0

    def test_exact_fit_is_not_truncated(self):
        nodes = cleaned("controls")
        full = snap.render(nodes)
        result = snap.render(nodes, max_chars=len(full.text))
        assert result.text == full.text and result.cut_lines == 0

    def test_first_line_too_long_cuts_everything(self):
        result = snap.render(
            snap.parse('- paragraph: "' + "x" * 50 + '"\n- button "B" [ref=e1]'),
            max_chars=10,
        )
        assert result.text == ""
        assert result.cut_lines == 2 and result.cut_refs == 1

    def test_shown_fingerprints_cover_only_emitted_lines(self):
        nodes = cleaned("controls")
        full = snap.render(nodes)
        assert full.fingerprints["e42"] == ("button", "Remove")
        partial = snap.render(nodes, max_chars=60)
        assert "e42" not in partial.fingerprints
        assert set(partial.fingerprints) == {
            r for r in full.fingerprints if f"[ref={r}]" in partial.text
        }


class TestHelpers:
    def test_walk_yields_depth_and_parent(self):
        nodes = snap.parse('- main:\n  - button "A" [ref=e1]')
        seen = [
            (n.role, depth, parent.role if parent else None)
            for n, depth, parent in snap.walk(nodes)
        ]
        assert seen == [("main", 0, None), ("button", 1, "main")]

    def test_is_interactive(self):
        button, generic, clickable = snap.parse(
            '- button "A"\n- generic [ref=e2]: x\n- generic [ref=e3] [cursor=pointer]: y'
        )
        assert snap.is_interactive(button)
        assert not snap.is_interactive(generic)
        assert snap.is_interactive(clickable)

    def test_line_of_renders_one_node(self):
        (node,) = snap.parse('- link "A" [ref=e1]:\n  - /url: /a')
        assert snap.line_of(node) == 'link "A" [ref=e1]:'

    def test_yaml_value_quoting_rules(self):
        for value, expected in [
            ("plain", "plain"),
            ("", '""'),
            (" lead", '" lead"'),
            ("on", '"on"'),
            ("3.5", '"3.5"'),
            ("a{b}", '"a{b}"'),
            ("a\x01b", '"a\\x01b"'),
        ]:
            assert snap.to_text([snap.Node(kind="text", role="text", value=value)]) == (
                f"- text: {expected}"
            )
