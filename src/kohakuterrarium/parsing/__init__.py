"""Parse streaming LLM output into text, action, and framework events."""

from kohakuterrarium.parsing.events import (
    AssistantImageEvent,
    BlockEndEvent,
    BlockStartEvent,
    CommandEvent,
    CommandResultEvent,
    OutputCallEvent,
    ParseEvent,
    SubAgentCallEvent,
    TextEvent,
    ToolCallEvent,
    is_action_event,
    is_text_event,
)
from kohakuterrarium.parsing.format import BRACKET_FORMAT, XML_FORMAT, ToolCallFormat
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
from kohakuterrarium.parsing.state_machine import ParserState, StreamParser, parse_full

# Preserve the pre-2.0 public name.
parse_complete = parse_full


def extract_tool_calls(events: list[ParseEvent]) -> list[ToolCallEvent]:
    """Return the tool-call events in their original order."""
    return [e for e in events if isinstance(e, ToolCallEvent)]


def extract_subagent_calls(events: list[ParseEvent]) -> list[SubAgentCallEvent]:
    """Return the sub-agent call events in their original order."""
    return [e for e in events if isinstance(e, SubAgentCallEvent)]


def extract_text(events: list[ParseEvent]) -> str:
    """Concatenate text events in their original order."""
    return "".join(e.text for e in events if isinstance(e, TextEvent))


__all__ = [
    "StreamParser",
    "ParserState",
    "parse_full",
    "parse_complete",
    "ParseEvent",
    "TextEvent",
    "ToolCallEvent",
    "SubAgentCallEvent",
    "CommandEvent",
    "CommandResultEvent",
    "OutputCallEvent",
    "BlockStartEvent",
    "BlockEndEvent",
    "AssistantImageEvent",
    "is_action_event",
    "is_text_event",
    "extract_tool_calls",
    "extract_subagent_calls",
    "extract_text",
    "ParserConfig",
    "ToolCallFormat",
    "BRACKET_FORMAT",
    "XML_FORMAT",
    "parse_opening_tag",
    "parse_closing_tag",
    "parse_attributes",
    "build_tool_args",
    "DEFAULT_COMMANDS",
    "DEFAULT_CONTENT_ARG_MAP",
    "DEFAULT_SUBAGENT_TAGS",
    "is_tool_tag",
    "is_subagent_tag",
    "is_command_tag",
    "is_output_tag",
]
