"""Unit tests for :mod:`kohakuterrarium.parsing` package-level helpers."""

from kohakuterrarium.parsing import (
    StreamParser,
    extract_subagent_calls,
    extract_text,
    extract_tool_calls,
    parse_complete,
    parse_full,
)
from kohakuterrarium.parsing.events import (
    SubAgentCallEvent,
    ToolCallEvent,
)
from kohakuterrarium.parsing.patterns import ParserConfig


def _cfg() -> ParserConfig:
    return ParserConfig(
        known_tools={"bash"},
        known_subagents={"agent"},
        known_commands=set(),
    )


def test_parse_complete_is_alias_for_parse_full():
    # Both must produce the same events for the same input.
    text = "before [/bash]ls[bash/] after"
    a = parse_full(text, _cfg())
    b = parse_complete(text, _cfg())
    assert [type(e).__name__ for e in a] == [type(e).__name__ for e in b]
    assert a[1].args == b[1].args  # tool args match


def test_extract_tool_calls_filters_only_tools():
    events = parse_full(
        "x [/bash]ls[bash/] y [/agent]task[agent/] z",
        _cfg(),
    )
    tools = extract_tool_calls(events)
    assert len(tools) == 1
    assert isinstance(tools[0], ToolCallEvent)
    assert tools[0].name == "bash"


def test_extract_subagent_calls_filters_only_subagents():
    events = parse_full(
        "x [/bash]ls[bash/] y [/agent]task[agent/] z",
        _cfg(),
    )
    sas = extract_subagent_calls(events)
    assert len(sas) == 1
    assert isinstance(sas[0], SubAgentCallEvent)
    assert sas[0].name == "agent"


def test_extract_text_joins_all_text_events_only():
    events = parse_full(
        "before [/bash]ls[bash/] after",
        _cfg(),
    )
    joined = extract_text(events)
    # Tool block is removed from the joined text — only TextEvents.
    assert "ls" not in joined
    assert "before" in joined
    assert "after" in joined


def test_stream_parser_exported():
    # Sanity — symbol is importable from the package root.
    assert StreamParser is not None
