"""
Parsing module - Stream parsing for LLM output.

Provides state machine parser for detecting configurable format tool calls
and framework commands from streaming LLM output.

Bracket format (default):
    [/function_name]
    @@arg=value
    content
    [function_name/]

XML format:
    <function_name arg="value">content</function_name>

Exports:
- StreamParser: Main streaming parser
- ParseEvent types: TextEvent, ToolCallEvent, SubAgentCallEvent, CommandEvent
- ParserConfig: Parser configuration
- ToolCallFormat, BRACKET_FORMAT, XML_FORMAT: Format configuration
"""

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

# Alias for backward compatibility
parse_complete = parse_full


def extract_tool_calls(events: list[ParseEvent]) -> list[ToolCallEvent]:
    """Extract all ToolCallEvent instances from a list of events."""
    return [e for e in events if isinstance(e, ToolCallEvent)]


def extract_subagent_calls(events: list[ParseEvent]) -> list[SubAgentCallEvent]:
    """Extract all SubAgentCallEvent instances from a list of events."""
    return [e for e in events if isinstance(e, SubAgentCallEvent)]


def extract_text(events: list[ParseEvent]) -> str:
    """Extract and concatenate all text from TextEvent instances."""
    return "".join(e.text for e in events if isinstance(e, TextEvent))


__all__ = [
    # Parser
    "StreamParser",
    "ParserState",
    "parse_full",
    "parse_complete",
    # Events
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
    # Extraction helpers
    "extract_tool_calls",
    "extract_subagent_calls",
    "extract_text",
    # Config
    "ParserConfig",
    # Format
    "ToolCallFormat",
    "BRACKET_FORMAT",
    "XML_FORMAT",
    # Pattern functions
    "parse_opening_tag",
    "parse_closing_tag",
    "parse_attributes",
    "build_tool_args",
    # Pattern defaults (for extending)
    "DEFAULT_COMMANDS",
    "DEFAULT_CONTENT_ARG_MAP",
    "DEFAULT_SUBAGENT_TAGS",
    "is_tool_tag",
    "is_subagent_tag",
    "is_command_tag",
    "is_output_tag",
]
