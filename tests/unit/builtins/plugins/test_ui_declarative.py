"""Unit tests for the ``ui.declarative`` plugin (inline UI).

Covers :mod:`kohakuterrarium.builtins.plugins.ui_declarative.plugin` and
``reference``: the renderer-gated prompt section, the component reference
generated from the component table, and the failure note.
"""

import asyncio
from contextlib import closing
from types import SimpleNamespace

from kohakuterrarium.builtins.kt_ui.components import COMPONENTS
from kohakuterrarium.builtins.kt_ui.markup import check_block, split_message
from kohakuterrarium.builtins.plugin_catalog import lookup_plugin
from kohakuterrarium.builtins.plugins.ui_declarative.plugin import (
    UiDeclarativePlugin,
    block_failures,
    failure_note,
)
from kohakuterrarium.builtins.plugins.ui_declarative.reference import (
    EXAMPLES,
    ON_DEMAND,
    component_doc,
    inline_ui_section,
)
from kohakuterrarium.builtins.tools.info import InfoTool
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.session.store import SessionStore
from tests.unit.test_tool_doc_shape import DISCOURAGING

GOOD = '<ui><state n={1}/><stat label="N">{n}</stat></ui>'
BAD = '<ui><stat label="N"/></ui>'


class _Router:
    def __init__(self, renderers):
        self.renderers = renderers

    def ui_renderers(self):
        return dict(self.renderers)


def _context(renderers, *, info=False, tool_format="native"):
    registry = Registry()
    if info:
        registry.register_tool(InfoTool())
    agent = SimpleNamespace(
        output_router=_Router(renderers),
        registry=registry,
        config=SimpleNamespace(tool_format=tool_format),
        _visible_catalog=lambda: None,
    )
    return PluginContext(_host_agent=agent)


def _run(coro):
    return asyncio.run(coro)


class TestSection:
    def test_present_only_with_the_kt_ui_renderer(self):
        plugin = UiDeclarativePlugin()
        assert plugin.presentation_contribution(_context({})) is None
        assert plugin.presentation_contribution(_context({"kt.app": 1})) is None
        part = plugin.presentation_contribution(_context({"kt.ui": 1}))
        assert part == inline_ui_section(on_demand=False, html=False)
        assert part.startswith("### Inline components")
        assert "show_app" not in part, "the full-page pointer belongs to ui.apps"
        assert plugin.presentation_contribution(PluginContext()) is None
        assert plugin.get_prompt_content(_context({"kt.ui": 1})) is None

    def test_on_demand_components_are_named_when_info_is_callable(self):
        plugin = UiDeclarativePlugin()
        for context in (
            _context({"kt.ui": 1, "kt.app": 1}, info=True),
            _context({"kt.ui": 1, "kt.app": 1}, tool_format="bracket"),
        ):
            part = plugin.presentation_contribution(context)
            assert part == inline_ui_section()
            more = next(line for line in part.splitlines() if "- More" in line)
            for name in ON_DEMAND:
                assert f"- `{name}`:" not in part, name
                assert f"`{name}`" in more, name
            assert "call `info` with `ui:<name>`" in more
            for name in set(COMPONENTS) - set(ON_DEMAND):
                assert f"- `{name}`" in part, name
        assert len(inline_ui_section()) < len(inline_ui_section(on_demand=False))

    def test_html_is_taught_only_with_a_client_that_runs_app_frames(self):
        plugin = UiDeclarativePlugin()
        without = plugin.presentation_contribution(_context({"kt.ui": 1}, info=True))
        assert "`html`" not in without and "→ `html`" not in without
        assert "`math` and `copy` take raw text" in without
        both = plugin.presentation_contribution(
            _context({"kt.ui": 1, "kt.app": 1}, info=True)
        )
        assert both == inline_ui_section()
        assert "→ `html` (a sandboxed frame" in both and "`html`, `math`" in both
        assert plugin.presentation_contribution(_context({"kt.app": 1})) is None

    def test_without_info_every_component_is_inline(self):
        part = UiDeclarativePlugin().presentation_contribution(_context({"kt.ui": 1}))
        assert "ui:<name>" not in part and "- More" not in part
        assert "- `quiz`: question*" in part

    def test_every_on_demand_component_has_a_valid_example(self):
        assert set(ON_DEMAND) <= set(EXAMPLES) <= set(COMPONENTS)
        for name in EXAMPLES:
            doc = component_doc(name)
            assert doc.startswith(f"# ui:{name}") and f"- `{name}`" in doc
            source = doc.split("Example:\n", 1)[1]
            types = _types(check_block(source).spec["root"])
            assert name in types, (name, types)

    def test_doc_topics_cover_every_component(self):
        plugin = UiDeclarativePlugin()
        assert plugin.doc_prefix == "ui"
        assert plugin.doc_topics(None) == list(COMPONENTS)
        assert plugin.doc_topic("table", None) == component_doc("table")
        assert "Example:" not in plugin.doc_topic("table", None)
        assert plugin.doc_topic("nope", None) is None

    def test_lists_every_component_and_leads_with_when_to_use(self):
        section = inline_ui_section(on_demand=False)
        for name in COMPONENTS:
            assert f"- `{name}`" in section, name
        intro = section.split("### Writing a block", 1)[0]
        for trigger in ("`table`", "`chart`", "`diagram`", "`tabs`", "send="):
            assert trigger in intro
        assert "in place of ASCII art" in intro
        lowered = section.lower()
        for phrase in DISCOURAGING:
            assert phrase not in lowered, phrase

    def test_send_is_documented_instead_of_internal_props(self):
        section = inline_ui_section()
        button = next(line for line in section.splitlines() if "- `button`" in line)
        assert "send: text sent as the user's message" in button
        assert "message:" not in button and "data:" not in button

    def test_the_example_is_a_valid_block(self):
        blocks = [s for s in split_message(inline_ui_section()) if s["kind"] == "ui"]
        assert len(blocks) == 1 and blocks[0]["closed"]
        root = check_block(blocks[0]["source"]).spec["root"]
        types = [child["type"] for child in root["children"]]
        assert types == ["grid", "stat", "chart", "diagram", "button"]

    def test_catalog_entry_and_renderer_dependency(self):
        assert lookup_plugin("ui.declarative") is not None
        plugin = UiDeclarativePlugin(default_surface="canvas")
        assert plugin.prompt_renderers == frozenset({"kt.ui", "kt.app"})
        assert plugin.contribute_tools() == []


def _types(node):
    found = [node.get("type")]
    for child in node.get("children", []) or []:
        found += _types(child)
    return found


class TestListAttributes:
    def test_the_syntax_says_list_entries_are_attributes(self):
        section = inline_ui_section()
        rule = next(
            line for line in section.splitlines() if "never as child tags" in line
        )
        for piece in (
            "`items={[…]}`",
            "`compare`",
            "`fields={[…]}`",
            "`rows={[…]}`",
            "`options={[…]}`",
        ):
            assert piece in rule, piece
        assert (
            "- `compare`: items*: [{title, pros: [text], cons: [text], verdict}]"
            in section
        )

    def test_the_note_teaches_the_component_it_names(self):
        failures = block_failures('<ui><compare><item title="A"/></compare></ui>')
        assert failures == [
            (1, "<compare> takes no children; set items={[…]} as attributes")
        ]
        note = failure_note(failures)
        assert (
            "- `compare`: items*: [{title, pros: [text], cons: [text], verdict}]"
            in note
        )
        assert "e.g. <compare items={[{title:" in note
        assert "- `item`" not in note, "only real components are described"

    def test_the_note_describes_at_most_three_components(self):
        reasons = [
            (i, f"<{n}> takes no children")
            for i, n in enumerate(["compare", "table", "steps", "tree", "quiz"], 1)
        ]
        note = failure_note(reasons)
        assert note.count("\n- `") == 3
        assert "- `tree`" not in note and "- `quiz`" not in note

    def test_a_note_without_component_names_has_no_props(self):
        note = failure_note([(1, "root.value: is required")])
        assert "How to write them" not in note


class TestFailureNote:
    def test_block_failures_number_every_block(self):
        text = f"a\n{GOOD}\nb\n{BAD}\n{GOOD}"
        failures = block_failures(text)
        assert [n for n, _ in failures] == [2]
        assert "root.value: is required" in failures[0][1]
        assert block_failures("no blocks") == []

    def test_long_reasons_are_cut(self):
        many = "".join(f"<slider bind='g{i}'/>" for i in range(30))
        ((_, reason),) = block_failures(f"<ui><card>{many}</card></ui>")
        assert len(reason) <= 300 and reason.endswith("…")

    def test_note_is_added_to_the_last_message_once(self):
        plugin = UiDeclarativePlugin()
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": f"see\n{BAD}"},
            {"role": "user", "content": "and?"},
        ]
        out = _run(plugin.pre_llm_call(messages))
        assert out[-1]["content"].startswith("and?\n\n[inline UI]")
        assert "- block 1:" in out[-1]["content"]
        assert messages[-1]["content"] == "and?", "the input is not mutated"
        assert _run(plugin.pre_llm_call(messages)) is None

    def test_a_noted_reply_stays_noted_after_resume(self, tmp_path):
        messages = [
            {"role": "assistant", "content": BAD},
            {"role": "user", "content": "next"},
        ]
        path = tmp_path / "s.kohakutr"

        def loaded_plugin(store):
            plugin = UiDeclarativePlugin()
            host = SimpleNamespace(session_store=store)
            ctx = PluginContext(
                agent_name="a", _host_agent=host, _plugin_name="ui.declarative"
            )
            _run(plugin.on_load(ctx))
            return plugin

        with closing(SessionStore(path)) as store:
            assert _run(loaded_plugin(store).pre_llm_call(messages)) is not None
        with closing(SessionStore(path)) as resumed:
            assert _run(loaded_plugin(resumed).pre_llm_call(messages)) is None

    def test_note_on_list_content_and_tool_results(self):
        plugin = UiDeclarativePlugin()
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": BAD}]},
            {"role": "tool", "content": [{"type": "text", "text": "done"}]},
        ]
        out = _run(plugin.pre_llm_call(messages))
        assert out[-1]["content"][-1]["text"] == failure_note(block_failures(BAD))
        empty = [{"role": "assistant", "content": BAD}, {"role": "user"}]
        assert _run(UiDeclarativePlugin().pre_llm_call(empty))[-1][
            "content"
        ].startswith("[inline UI]")

    def test_a_checked_reply_is_not_validated_again(self, monkeypatch):
        plugin = UiDeclarativePlugin()
        calls = []
        real = block_failures

        def counting(text):
            calls.append(text)
            return real(text)

        monkeypatch.setattr(
            "kohakuterrarium.builtins.plugins.ui_declarative.plugin.block_failures",
            counting,
        )
        messages = [{"role": "assistant", "content": GOOD}, {"role": "user"}]
        assert _run(plugin.pre_llm_call(messages)) is None
        assert _run(plugin.pre_llm_call(messages)) is None
        assert len(calls) == 1

    def test_no_note_when_nothing_failed_or_nothing_follows(self):
        plugin = UiDeclarativePlugin()
        cases = [
            [{"role": "user", "content": "hi"}],
            [{"role": "assistant", "content": BAD}],
            [
                {"role": "assistant", "content": "plain"},
                {"role": "user", "content": "x"},
            ],
            [{"role": "assistant", "content": GOOD}, {"role": "user", "content": "x"}],
            [{"role": "assistant", "content": None}, {"role": "user", "content": "x"}],
        ]
        for messages in cases:
            assert _run(plugin.pre_llm_call(messages)) is None, messages

    def test_remembered_replies_are_bounded(self, monkeypatch):
        monkeypatch.setattr(
            "kohakuterrarium.builtins.plugins.ui_declarative.plugin.MAX_NOTED", 2
        )
        plugin = UiDeclarativePlugin()
        for i in range(3):
            reply = f"{i}\n{BAD}"
            msgs = [{"role": "assistant", "content": reply}, {"role": "user"}]
            assert _run(plugin.pre_llm_call(msgs)) is not None
        assert len(plugin._noted) == 2
        first = [{"role": "assistant", "content": f"0\n{BAD}"}, {"role": "user"}]
        assert _run(plugin.pre_llm_call(first)) is not None, "evicted, noted again"
