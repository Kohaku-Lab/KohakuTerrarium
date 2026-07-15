"""Parse structured tags and map their content into action arguments."""

import re
from dataclasses import dataclass, field

from kohakuterrarium.parsing.format import BRACKET_FORMAT, ToolCallFormat

# Tools with non-generic body parameters need an explicit content target.
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
    "info": "tool_name",
    "read_job": "job_id",
}

# Commands bypass the tool registry and execute at the framework level.
DEFAULT_COMMANDS: set[str] = {"info", "read_job", "jobs", "wait"}

DEFAULT_SUBAGENT_TAGS: set[str] = {"agent"}


@dataclass
class ParserConfig:
    """Configure recognized actions, text buffering, and tool-call syntax."""

    emit_block_events: bool = False
    buffer_text: bool = True
    text_buffer_size: int = 1

    # Registries populate these sets before parsing begins.
    known_tools: set[str] = field(default_factory=set)
    known_subagents: set[str] = field(
        default_factory=lambda: DEFAULT_SUBAGENT_TAGS.copy()
    )
    known_commands: set[str] = field(default_factory=lambda: DEFAULT_COMMANDS.copy())
    known_outputs: set[str] = field(default_factory=set)

    content_arg_map: dict[str, str] = field(
        default_factory=lambda: DEFAULT_CONTENT_ARG_MAP.copy()
    )

    tool_format: ToolCallFormat = field(default_factory=lambda: BRACKET_FORMAT)


OPENING_TAG_PATTERN = re.compile(
    r"<([a-zA-Z_][a-zA-Z0-9_]*)"
    r"((?:\s+[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*\"[^\"]*\")*)"
    r"\s*(/?)>"
)

ATTR_PATTERN = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"([^"]*)"')

CLOSING_TAG_PATTERN = re.compile(r"</([a-zA-Z_][a-zA-Z0-9_]*)>")


def parse_attributes(attr_string: str) -> dict[str, str]:
    """Return double-quoted attributes from an opening tag."""
    attrs = {}
    for match in ATTR_PATTERN.finditer(attr_string):
        name, value = match.groups()
        attrs[name] = value
    return attrs


def parse_opening_tag(tag_text: str) -> tuple[str, dict[str, str], bool] | None:
    """Parse an XML opening tag into name, attributes, and closure state."""
    match = OPENING_TAG_PATTERN.match(tag_text)
    if not match:
        return None

    tag_name = match.group(1)
    attr_string = match.group(2)
    is_self_closing = match.group(3) == "/"

    attrs = parse_attributes(attr_string) if attr_string else {}

    return tag_name, attrs, is_self_closing


def parse_closing_tag(tag_text: str) -> str | None:
    """Return the name from a valid XML closing tag."""
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
    """Merge tag attributes with body content mapped to the tool's body argument."""
    args = dict(attributes)

    # Preserve explicit attributes when the body maps to the same argument.
    content = content.strip()
    if content:
        arg_map = content_arg_map or DEFAULT_CONTENT_ARG_MAP
        content_arg = arg_map.get(tag_name, "content")

        if content_arg not in args:
            args[content_arg] = content

    return args


def is_tool_tag(tag_name: str, known_tools: set[str] | None = None) -> bool:
    """Return whether the active tool registry contains the tag name."""
    if known_tools is None:
        return False
    return tag_name in known_tools


def is_subagent_tag(tag_name: str, known_subagents: set[str] | None = None) -> bool:
    """Return whether the tag invokes a configured sub-agent."""
    tags = known_subagents if known_subagents is not None else DEFAULT_SUBAGENT_TAGS
    return tag_name in tags


def is_command_tag(tag_name: str, known_commands: set[str] | None = None) -> bool:
    """Return whether the tag names a framework command."""
    cmds = known_commands if known_commands is not None else DEFAULT_COMMANDS
    return tag_name in cmds


def is_output_tag(
    tag_name: str, known_outputs: set[str] | None = None
) -> tuple[bool, str]:
    """Validate an ``output_<target>`` tag and return its target name."""
    if not tag_name.startswith("output_"):
        return False, ""

    target = tag_name[7:]
    if not target:
        return False, ""

    if known_outputs is not None and target not in known_outputs:
        return False, ""

    return True, target
