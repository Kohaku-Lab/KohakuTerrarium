"""
Pattern definitions for stream parsing.

Supports custom format tool calls: [/tool_name]@@arg=value content[tool_name/]
"""

import re
from dataclasses import dataclass, field

from kohakuterrarium.parsing.format import BRACKET_FORMAT, ToolCallFormat

# Default content argument mapping for built-in tools
DEFAULT_CONTENT_ARG_MAP: dict[str, str] = {
    "bash": "command",
    "python": "code",
    "edit": "diff",
    "write": "content",
    "read": "path",
    "glob": "pattern",
    "grep": "pattern",
    "tree": "path",
    "send_message": "message",
    # Commands
    "info": "tool_name",
    "read_job": "job_id",
}

# Default commands (framework-level, not tool-level)
DEFAULT_COMMANDS: set[str] = {"info", "read_job", "jobs", "wait"}

# Default sub-agent tag (generic agent tag)
DEFAULT_SUBAGENT_TAGS: set[str] = {"agent"}


@dataclass
class ParserConfig:
    """
    Configuration for the stream parser.

    Attributes:
        emit_block_events: Whether to emit BlockStart/BlockEnd events
        buffer_text: Whether to buffer text between blocks
        text_buffer_size: Minimum chars to buffer before emitting
        known_tools: Set of known tool names (from registry)
        known_subagents: Set of known sub-agent tag names
        known_commands: Set of known command names
        known_outputs: Set of known output target names (e.g., "discord", "tts")
        content_arg_map: Mapping of tool name to content argument name
    """

    # Whether to emit BlockStartEvent and BlockEndEvent
    emit_block_events: bool = False

    # Buffer text chunks before emitting (reduces event count)
    buffer_text: bool = True

    # Minimum chars to buffer before emitting text
    text_buffer_size: int = 1

    # Dynamic tool/subagent/command sets (populated from registry)
    known_tools: set[str] = field(default_factory=set)
    known_subagents: set[str] = field(
        default_factory=lambda: DEFAULT_SUBAGENT_TAGS.copy()
    )
    known_commands: set[str] = field(default_factory=lambda: DEFAULT_COMMANDS.copy())
    known_outputs: set[str] = field(default_factory=set)  # e.g., {"discord", "tts"}

    # Content argument mapping (can be extended for custom tools)
    content_arg_map: dict[str, str] = field(
        default_factory=lambda: DEFAULT_CONTENT_ARG_MAP.copy()
    )

    # Tool call format (bracket, XML, or custom)
    tool_format: ToolCallFormat = field(default_factory=lambda: BRACKET_FORMAT)


# Regex for parsing XML-style opening tags with attributes
# Matches: <tag_name attr1="value1" attr2="value2">
# Or self-closing: <tag_name attr="value"/>
OPENING_TAG_PATTERN = re.compile(
    r"<([a-zA-Z_][a-zA-Z0-9_]*)"  # Tag name
    r"((?:\s+[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*\"[^\"]*\")*)"  # Attributes
    r"\s*(/?)>"  # Optional self-closing /
)

# Regex for extracting individual attributes
ATTR_PATTERN = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"([^"]*)"')

# Regex for closing tag
CLOSING_TAG_PATTERN = re.compile(r"</([a-zA-Z_][a-zA-Z0-9_]*)>")


def parse_attributes(attr_string: str) -> dict[str, str]:
    """
    Parse attributes from an opening tag.

    Args:
        attr_string: String like ' path="src/main.py" limit="50"'

    Returns:
        Dict of attribute name -> value
    """
    attrs = {}
    for match in ATTR_PATTERN.finditer(attr_string):
        name, value = match.groups()
        attrs[name] = value
    return attrs


def parse_opening_tag(tag_text: str) -> tuple[str, dict[str, str], bool] | None:
    """
    Parse an opening XML tag.

    Args:
        tag_text: Full tag like '<bash>' or '<edit path="file.py">' or '<read/>'

    Returns:
        (tag_name, attributes, is_self_closing) or None if invalid
    """
    match = OPENING_TAG_PATTERN.match(tag_text)
    if not match:
        return None

    tag_name = match.group(1)
    attr_string = match.group(2)
    is_self_closing = match.group(3) == "/"

    attrs = parse_attributes(attr_string) if attr_string else {}

    return tag_name, attrs, is_self_closing


def parse_closing_tag(tag_text: str) -> str | None:
    """
    Parse a closing XML tag.

    Args:
        tag_text: Tag like '</bash>'

    Returns:
        Tag name or None if invalid
    """
    match = CLOSING_TAG_PATTERN.match(tag_text)
    if match:
        return match.group(1)
    return None


def build_tool_args(
    tag_name: str,
    attributes: dict[str, str],
    content: str,
    content_arg_map: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Build tool arguments from tag attributes and content.

    For tools like bash/python, content is the main argument.
    For tools like edit, content is the diff and path is an attribute.

    Args:
        tag_name: The tool name
        attributes: Parsed attributes from the tag
        content: Content between opening and closing tags
        content_arg_map: Optional mapping of tool name to content arg name

    Returns:
        Complete args dict for the tool
    """
    args = dict(attributes)  # Copy attributes

    # Map content to the appropriate argument based on tool type
    content = content.strip()
    if content:
        # Use provided map or fall back to default
        arg_map = content_arg_map or DEFAULT_CONTENT_ARG_MAP
        content_arg = arg_map.get(tag_name, "content")

        # Don't override if already set via attribute
        if content_arg not in args:
            args[content_arg] = content

    return args


def is_tool_tag(tag_name: str, known_tools: set[str] | None = None) -> bool:
    """
    Check if tag name is a known tool.

    Args:
        tag_name: Tag name to check
        known_tools: Set of known tool names (from registry)
    """
    if known_tools is None:
        return False
    return tag_name in known_tools


def is_subagent_tag(tag_name: str, known_subagents: set[str] | None = None) -> bool:
    """
    Check if tag name is a sub-agent call.

    Args:
        tag_name: Tag name to check
        known_subagents: Set of known sub-agent tag names
    """
    tags = known_subagents if known_subagents is not None else DEFAULT_SUBAGENT_TAGS
    return tag_name in tags


def is_command_tag(tag_name: str, known_commands: set[str] | None = None) -> bool:
    """
    Check if tag name is a known command.

    Args:
        tag_name: Tag name to check
        known_commands: Set of known command names
    """
    cmds = known_commands if known_commands is not None else DEFAULT_COMMANDS
    return tag_name in cmds


def is_output_tag(
    tag_name: str, known_outputs: set[str] | None = None
) -> tuple[bool, str]:
    """
    Check if tag name is an output tag (format: output_<target>).

    Args:
        tag_name: Tag name to check (e.g., "output_discord")
        known_outputs: Set of known output target names

    Returns:
        (is_output, target_name) - e.g., (True, "discord") or (False, "")
    """
    if not tag_name.startswith("output_"):
        return False, ""

    target = tag_name[7:]  # Remove "output_" prefix
    if not target:
        return False, ""

    # If known_outputs is provided, validate against it
    if known_outputs is not None and target not in known_outputs:
        return False, ""

    return True, target
