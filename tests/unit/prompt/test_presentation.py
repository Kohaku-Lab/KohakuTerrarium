"""Unit tests for :mod:`kohakuterrarium.prompt.presentation`.

``## Presenting your answer`` sits right after the base prompt and exists
only when a plugin contributes a part (prompt contract rule 5: asserted
present and absent).
"""

from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.modules.plugin.manager.manager import PluginManager
from kohakuterrarium.prompt.aggregator import aggregate_system_prompt
from kohakuterrarium.prompt.presentation import (
    CLOSING,
    HEADING,
    OPENING,
    build_presentation_section,
)
from tests.unit.test_tool_doc_shape import DISCOURAGING


class _Part(BasePlugin):
    def __init__(self, name, text, priority=50):
        super().__init__()
        self.name = name
        self.priority = priority
        self.text = text

    def presentation_contribution(self, context):
        return self.text


class _Broken(BasePlugin):
    name = "broken"

    def presentation_contribution(self, context):
        raise RuntimeError("boom")


def _manager(*plugins):
    manager = PluginManager()
    for plugin in plugins:
        manager.register(plugin)
    return manager


def test_no_part_means_no_section():
    assert build_presentation_section(None, PluginContext()) == ""
    assert build_presentation_section(_manager(), None) == ""
    assert build_presentation_section(_manager(_Part("a", None)), PluginContext()) == ""


def test_parts_in_priority_order_between_opening_and_closing():
    manager = _manager(
        _Part("late", "LATE", 90), _Part("early", "EARLY", 10), _Broken()
    )
    section = build_presentation_section(manager, PluginContext())
    assert section == "\n\n".join([HEADING, OPENING, "EARLY", "LATE", CLOSING])
    manager.disable("early")
    assert "EARLY" not in build_presentation_section(manager, PluginContext())


def test_framework_lines_are_positive():
    for line in (OPENING, CLOSING):
        for phrase in DISCOURAGING:
            assert phrase not in line.lower()


def test_aggregator_places_the_section_right_after_the_base_prompt():
    manager = _manager(_Part("a", "PART-A"))
    registry = Registry()
    registry.register_tool(get_builtin_tool("read"))
    prompt = aggregate_system_prompt(
        "BASE PROMPT",
        registry,
        tool_format="bracket",
        runtime_plugins=manager,
        plugin_context=PluginContext(),
    )
    assert prompt.startswith(f"BASE PROMPT\n\n{HEADING}\n\n{OPENING}\n\nPART-A")
    assert prompt.index(CLOSING) < prompt.index("## Available Functions")
    without = aggregate_system_prompt(
        "BASE PROMPT",
        Registry(),
        tool_format="bracket",
        runtime_plugins=_manager(),
        plugin_context=PluginContext(),
    )
    assert HEADING not in without and OPENING not in without
