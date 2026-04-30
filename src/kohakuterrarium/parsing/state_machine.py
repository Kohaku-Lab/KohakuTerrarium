"""
Streaming state machine parser for LLM output.

Parses configurable format tool calls and commands from streaming text.

Bracket format (default):
    [/function]
    @@arg=value
    content here
    [function/]

XML format:
    <function arg="value">
    content here
    </function>

Handles partial chunks correctly (markers split across chunks).
"""

from enum import Enum, auto

from kohakuterrarium.parsing.events import (
    BlockEndEvent,
    BlockStartEvent,
    CommandEvent,
    OutputCallEvent,
    ParseEvent,
    SubAgentCallEvent,
    TextEvent,
    ToolCallEvent,
)
from kohakuterrarium.parsing.format import ToolCallFormat
from kohakuterrarium.parsing.patterns import (
    ParserConfig,
    is_command_tag,
    is_output_tag,
    is_subagent_tag,
    is_tool_tag,
    parse_attributes,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class ParserState(Enum):
    """Parser state machine states."""

    NORMAL = auto()  # Normal text streaming
    MAYBE_OPEN = auto()  # Saw start_char, might be opening marker
    OPEN_SLASH = auto()  # Saw start_char + /, expecting name (bracket: opening)
    IN_OPEN_NAME = auto()  # Reading function name for opening tag
    IN_OPEN_ATTRS = auto()  # Reading inline attributes (XML: <name attrs...>)
    IN_BLOCK = auto()  # Inside block, reading args/content
    MAYBE_CLOSE = auto()  # Saw start_char inside block, might be closing
    IN_CLOSE_NAME = auto()  # Reading function name in closing tag
    EXPECT_CLOSE_SLASH = auto()  # Expecting /end_char after close name (bracket)
    IN_SELF_CLOSING = auto()  # Saw / in attrs, expecting end_char for self-close


class StreamParser:
    """
    Streaming parser for LLM output.

    Detects and parses configurable format tool calls:

    Bracket format (default):
        [/bash]ls -la[bash/]
        [/write]
        @@path=file.py
        content here
        [write/]

    XML format:
        <bash>ls -la</bash>
        <read path="src/main.py"/>

    Usage:
        parser = StreamParser()
        for chunk in llm_stream:
            events = parser.feed(chunk)
            for event in events:
                handle_event(event)
        # Don't forget to flush at end
        final_events = parser.flush()
    """

    def __init__(self, config: ParserConfig | None = None):
        self.config = config or ParserConfig()
        self._fmt: ToolCallFormat = self.config.tool_format
        self._reset()

    def _reset(self) -> None:
        """Reset parser state."""
        self.state = ParserState.NORMAL
        self.text_buffer = ""  # Buffered text to emit
        self.name_buffer = ""  # Current function name being parsed
        self.block_buffer = ""  # Content inside current block
        self.current_name = ""  # Name of current open block
        self.attrs_buffer = ""  # Buffer for inline attributes (XML mode)
        self.inline_args: dict[str, str] = {}  # Parsed inline attrs
        self._last_progress_log = 0

    def feed(self, chunk: str) -> list[ParseEvent]:
        """
        Feed a chunk of text to the parser.

        Args:
            chunk: Text chunk from LLM stream

        Returns:
            List of ParseEvents detected in this chunk
        """
        events: list[ParseEvent] = []

        for char in chunk:
            new_events = self._process_char(char)
            events.extend(new_events)

        return events

    def flush(self) -> list[ParseEvent]:
        """
        Flush any remaining buffered content.

        Call this when the stream ends.
        """
        events: list[ParseEvent] = []
        sc = self._fmt.start_char

        # Emit any buffered text
        if self.text_buffer:
            events.append(TextEvent(self.text_buffer))
            self.text_buffer = ""

        # Handle incomplete states
        if self.state == ParserState.MAYBE_OPEN:
            events.append(TextEvent(sc))
        elif self.state == ParserState.OPEN_SLASH:
            events.append(TextEvent(sc + "/"))
        elif self.state == ParserState.IN_OPEN_NAME:
            if self._fmt.slash_means_open:
                events.append(TextEvent(sc + "/" + self.name_buffer))
            else:
                events.append(TextEvent(sc + self.name_buffer))
        elif self.state == ParserState.IN_OPEN_ATTRS:
            events.append(TextEvent(sc + self.name_buffer + " " + self.attrs_buffer))
        elif self.state == ParserState.IN_SELF_CLOSING:
            events.append(
                TextEvent(sc + self.name_buffer + " " + self.attrs_buffer + "/")
            )
        elif self.state == ParserState.IN_BLOCK:
            logger.warning(
                "Unclosed block at end of stream", block_name=self.current_name
            )
            raw = self._build_raw_open() + self.block_buffer
            events.append(TextEvent(raw))
        elif self.state == ParserState.MAYBE_CLOSE:
            self.block_buffer += sc
            raw = self._build_raw_open() + self.block_buffer
            events.append(TextEvent(raw))
        elif self.state == ParserState.IN_CLOSE_NAME:
            if self._fmt.slash_means_open:
                self.block_buffer += sc + self.name_buffer
            else:
                self.block_buffer += sc + "/" + self.name_buffer
            raw = self._build_raw_open() + self.block_buffer
            events.append(TextEvent(raw))
        elif self.state == ParserState.EXPECT_CLOSE_SLASH:
            self.block_buffer += sc + self.name_buffer
            raw = self._build_raw_open() + self.block_buffer
            events.append(TextEvent(raw))

        self._reset()
        return events

    def _process_char(self, char: str) -> list[ParseEvent]:
        """Process a single character."""
        events: list[ParseEvent] = []

        match self.state:
            case ParserState.NORMAL:
                events.extend(self._handle_normal(char))
            case ParserState.MAYBE_OPEN:
                events.extend(self._handle_maybe_open(char))
            case ParserState.OPEN_SLASH:
                events.extend(self._handle_open_slash(char))
            case ParserState.IN_OPEN_NAME:
                events.extend(self._handle_in_open_name(char))
            case ParserState.IN_OPEN_ATTRS:
                events.extend(self._handle_in_open_attrs(char))
            case ParserState.IN_SELF_CLOSING:
                events.extend(self._handle_in_self_closing(char))
            case ParserState.IN_BLOCK:
                events.extend(self._handle_in_block(char))
            case ParserState.MAYBE_CLOSE:
                events.extend(self._handle_maybe_close(char))
            case ParserState.IN_CLOSE_NAME:
                events.extend(self._handle_in_close_name(char))
            case ParserState.EXPECT_CLOSE_SLASH:
                events.extend(self._handle_expect_close_slash(char))

        return events

    def _handle_normal(self, char: str) -> list[ParseEvent]:
        """Handle character in NORMAL state."""
        events: list[ParseEvent] = []

        if char == self._fmt.start_char:
            # Potential opening marker
            if self.text_buffer:
                events.append(TextEvent(self.text_buffer))
                self.text_buffer = ""
            self.state = ParserState.MAYBE_OPEN
        else:
            self.text_buffer += char

        return events

    def _handle_maybe_open(self, char: str) -> list[ParseEvent]:
        """Handle character after seeing start_char."""
        events: list[ParseEvent] = []
        sc = self._fmt.start_char

        if char == "/":
            if self._fmt.slash_means_open:
                # Bracket mode: [/ = start of opening tag
                self.state = ParserState.OPEN_SLASH
            else:
                # XML mode: </ = start of closing tag
                # But we're in NORMAL, so this is just text (no open block)
                self.text_buffer += sc + char
                self.state = ParserState.NORMAL
        elif char.isalpha() or char == "_":
            if self._fmt.slash_means_open:
                # Bracket mode: [letter = not a valid opening (bracket needs [/)
                self.text_buffer += sc + char
                self.state = ParserState.NORMAL
            else:
                # XML mode: <letter = start of opening tag name
                self.name_buffer = char
                self.state = ParserState.IN_OPEN_NAME
        else:
            # Not a tag - emit start_char as text
            self.text_buffer += sc + char
            self.state = ParserState.NORMAL

        return events

    def _handle_open_slash(self, char: str) -> list[ParseEvent]:
        """Handle character after seeing start_char + / (bracket: [/, XML: not used from NORMAL)."""
        events: list[ParseEvent] = []

        if char.isalnum() or char == "_":
            # Start of function name
            self.name_buffer = char
            self.state = ParserState.IN_OPEN_NAME
        else:
            # Not a valid tag - emit as text
            self.text_buffer += self._fmt.start_char + "/" + char
            self.state = ParserState.NORMAL

        return events

    def _handle_in_open_name(self, char: str) -> list[ParseEvent]:
        """Handle character while reading opening function name."""
        events: list[ParseEvent] = []
        ec = self._fmt.end_char
        sc = self._fmt.start_char

        if char == ec:
            # End of opening marker
            self.current_name = self.name_buffer
            self.name_buffer = ""
            self.block_buffer = ""
            self.inline_args = {}
            self.state = ParserState.IN_BLOCK

            # Emit block start event
            if self.config.emit_block_events:
                events.append(BlockStartEvent(self.current_name))
            logger.debug("Block started", block_name=self.current_name)
        elif char == " " and self._fmt.arg_style == "inline":
            # XML mode: <name space... -> reading inline attributes
            self.state = ParserState.IN_OPEN_ATTRS
            self.attrs_buffer = ""
        elif char.isalnum() or char == "_":
            # Continue reading name
            self.name_buffer += char
        else:
            # Invalid character - not a valid marker, emit as text
            if self._fmt.slash_means_open:
                self.text_buffer += sc + "/" + self.name_buffer + char
            else:
                self.text_buffer += sc + self.name_buffer + char
            self.name_buffer = ""
            self.state = ParserState.NORMAL

        return events

    def _handle_in_open_attrs(self, char: str) -> list[ParseEvent]:
        """Handle character while reading inline attributes (XML mode)."""
        events: list[ParseEvent] = []
        ec = self._fmt.end_char

        if char == "/":
            # Might be self-closing: <name attrs/>
            self.state = ParserState.IN_SELF_CLOSING
        elif char == ec:
            # End of opening tag: <name attrs>
            self.inline_args = parse_attributes(self.attrs_buffer)
            self.current_name = self.name_buffer
            self.name_buffer = ""
            self.block_buffer = ""
            self.state = ParserState.IN_BLOCK

            if self.config.emit_block_events:
                events.append(BlockStartEvent(self.current_name))
            logger.debug(
                "Block started with attrs",
                block_name=self.current_name,
            )
        else:
            self.attrs_buffer += char

        return events

    def _handle_in_self_closing(self, char: str) -> list[ParseEvent]:
        """Handle character after seeing / in attrs (expecting end_char for self-close)."""
        events: list[ParseEvent] = []
        ec = self._fmt.end_char

        if char == ec:
            # Self-closing tag: <name attrs/>
            self.inline_args = parse_attributes(self.attrs_buffer)
            self.current_name = self.name_buffer
            self.name_buffer = ""
            self.block_buffer = ""
            self.attrs_buffer = ""

            if self.config.emit_block_events:
                events.append(BlockStartEvent(self.current_name))

            # Complete the block immediately with no content
            events.extend(self._complete_block())
        else:
            # The / was part of the attribute value or something else
            self.attrs_buffer += "/" + char
            self.state = ParserState.IN_OPEN_ATTRS

        return events

    def _handle_in_block(self, char: str) -> list[ParseEvent]:
        """Handle character inside block content."""
        events: list[ParseEvent] = []

        if char == self._fmt.start_char:
            # Potential closing marker
            self.state = ParserState.MAYBE_CLOSE
        else:
            self.block_buffer += char

        return events

    def _handle_maybe_close(self, char: str) -> list[ParseEvent]:
        """Handle character after seeing start_char inside block."""
        events: list[ParseEvent] = []
        sc = self._fmt.start_char

        if self._fmt.slash_means_open:
            # Bracket mode: closing is [name/]
            if char.isalnum() or char == "_":
                # Start of closing name: [name
                self.name_buffer = char
                self.state = ParserState.IN_CLOSE_NAME
            elif char == "/":
                # Saw [/ inside block - this could be a nested opening tag
                # but we don't support nesting, so treat as content
                self.block_buffer += sc + char
                self.state = ParserState.IN_BLOCK
            else:
                # Not a closing marker - add to content
                self.block_buffer += sc + char
                self.state = ParserState.IN_BLOCK
        else:
            # XML mode: closing is </name>
            if char == "/":
                # Start of closing tag: </
                self.name_buffer = ""
                self.state = ParserState.IN_CLOSE_NAME
            elif char.isalpha() or char == "_":
                # Saw <letter inside block, not a close tag
                # This is just content (e.g., HTML tags that aren't tools)
                self.block_buffer += sc + char
                self.state = ParserState.IN_BLOCK
            else:
                # Not a closing marker
                self.block_buffer += sc + char
                self.state = ParserState.IN_BLOCK

        return events

    def _handle_in_close_name(self, char: str) -> list[ParseEvent]:
        """Handle character while reading closing function name."""
        events: list[ParseEvent] = []
        sc = self._fmt.start_char
        ec = self._fmt.end_char

        if char.isalnum() or char == "_":
            # Continue reading close name
            self.name_buffer += char
        elif self._fmt.slash_means_open and char == "/":
            # Bracket mode: [name/ -> expecting ]
            self.state = ParserState.EXPECT_CLOSE_SLASH
        elif char == ec:
            if self._fmt.slash_means_open:
                # Bracket mode: [name] without slash -> not a close tag
                self.block_buffer += sc + self.name_buffer + char
                self.name_buffer = ""
                self.state = ParserState.IN_BLOCK
            else:
                # XML mode: </name> -> closing complete!
                if self.name_buffer == self.current_name:
                    events.extend(self._complete_block())
                else:
                    # Mismatched close - treat as content
                    logger.warning(
                        "Mismatched close marker",
                        expected=self.current_name,
                        got=self.name_buffer,
                    )
                    self.block_buffer += sc + "/" + self.name_buffer + ec
                    self.name_buffer = ""
                    self.state = ParserState.IN_BLOCK
        else:
            # Invalid close tag
            if self._fmt.slash_means_open:
                self.block_buffer += sc + self.name_buffer + char
            else:
                self.block_buffer += sc + "/" + self.name_buffer + char
            self.name_buffer = ""
            self.state = ParserState.IN_BLOCK

        return events

    def _handle_expect_close_slash(self, char: str) -> list[ParseEvent]:
        """Handle character after seeing [name/ - expecting ]."""
        events: list[ParseEvent] = []
        sc = self._fmt.start_char
        ec = self._fmt.end_char

        if char == ec:
            # End of closing marker - [name/]
            if self.name_buffer == self.current_name:
                # Valid close - process the block
                events.extend(self._complete_block())
            else:
                # Mismatched close - treat as content
                logger.warning(
                    "Mismatched close marker",
                    expected=self.current_name,
                    got=self.name_buffer,
                )
                self.block_buffer += sc + self.name_buffer + "/" + ec
                self.name_buffer = ""
                self.state = ParserState.IN_BLOCK
        else:
            # Invalid - not a proper close, add to content
            self.block_buffer += sc + self.name_buffer + "/" + char
            self.name_buffer = ""
            self.state = ParserState.IN_BLOCK

        return events

    def _complete_block(self) -> list[ParseEvent]:
        """Process a completed block and return appropriate events."""
        events: list[ParseEvent] = []
        name = self.current_name
        content = self.block_buffer

        # Parse args and content from block
        if self._fmt.arg_style == "inline" and self.inline_args:
            # XML mode: args come from tag attributes
            args = dict(self.inline_args)
            body = content.strip()
        else:
            # Bracket mode: args from @@key=value lines
            args, body = self._parse_block_content(content)

        # Build raw representation
        raw = self._build_raw(name, args, body)

        # Check for output tag first (format: output_<target>)
        is_output, output_target = is_output_tag(name, self.config.known_outputs)
        if is_output:
            # Output block - explicit output to named target
            events.append(OutputCallEvent(target=output_target, content=body, raw=raw))
            logger.debug("Parsed output block", target=output_target)

        elif is_tool_tag(name, self.config.known_tools):
            # Tool call
            tool_args = {**args}
            if body:
                # Map body to appropriate arg based on tool
                content_arg = self.config.content_arg_map.get(name, "content")
                # Don't override if already set via attribute
                if content_arg not in tool_args:
                    tool_args[content_arg] = body
            events.append(ToolCallEvent(name=name, args=tool_args, raw=raw))
            logger.debug("Parsed tool call", tool_name=name)

        elif is_subagent_tag(name, self.config.known_subagents):
            # Sub-agent call
            subagent_args = {"task": body.strip(), **args}
            events.append(SubAgentCallEvent(name=name, args=subagent_args, raw=raw))
            logger.debug("Parsed sub-agent call", subagent_type=name)

        elif is_command_tag(name, self.config.known_commands):
            # Framework command
            cmd_args = body.strip()
            events.append(CommandEvent(command=name, args=cmd_args, raw=raw))
            logger.debug("Parsed command", command=name)

        else:
            # Unknown block type - emit as text
            logger.warning("Unknown block type", block_name=name)
            events.append(TextEvent(raw))

        # Emit block end event
        if self.config.emit_block_events:
            events.append(BlockEndEvent(name))

        # Reset state
        self.current_name = ""
        self.name_buffer = ""
        self.block_buffer = ""
        self.attrs_buffer = ""
        self.inline_args = {}
        self.state = ParserState.NORMAL

        return events

    def _parse_block_content(self, content: str) -> tuple[dict[str, str], str]:
        """
        Parse block content into args and body.

        Args start with the configured arg_prefix on their own line.
        Everything else is body.

        Returns:
            (args_dict, body_string)
        """
        args: dict[str, str] = {}
        body_lines: list[str] = []
        in_args = True
        prefix = self._fmt.arg_prefix
        kv_sep = self._fmt.arg_kv_sep

        for line in content.split("\n"):
            # Skip empty lines while still in args section
            if in_args and line.strip() == "":
                continue
            if in_args and prefix and line.startswith(prefix):
                # Parse arg: @@key=value (or whatever the prefix/sep is)
                arg_content = line[len(prefix) :]
                if kv_sep in arg_content:
                    key, value = arg_content.split(kv_sep, 1)
                    args[key.strip()] = value.strip()
                else:
                    # Arg without value
                    args[arg_content.strip()] = ""
            else:
                # Once we hit a non-arg line, everything is body
                in_args = False
                body_lines.append(line)

        body = "\n".join(body_lines).strip()
        return args, body

    def _build_raw_open(self) -> str:
        """Build raw opening marker."""
        sc = self._fmt.start_char
        ec = self._fmt.end_char
        if self._fmt.slash_means_open:
            return f"{sc}/{self.current_name}{ec}\n"
        else:
            if self.inline_args:
                attr_parts = [f'{k}="{v}"' for k, v in self.inline_args.items()]
                attrs_str = " " + " ".join(attr_parts) if attr_parts else ""
                return f"{sc}{self.current_name}{attrs_str}{ec}\n"
            return f"{sc}{self.current_name}{ec}\n"

    def _build_raw(self, name: str, args: dict[str, str], body: str) -> str:
        """Build raw representation of block."""
        sc = self._fmt.start_char
        ec = self._fmt.end_char

        if self._fmt.slash_means_open:
            # Bracket format: [/name]@@key=val\nbody\n[name/]
            parts = [f"{sc}/{name}{ec}"]
            prefix = self._fmt.arg_prefix
            kv_sep = self._fmt.arg_kv_sep
            for key, value in args.items():
                parts.append(f"{prefix}{key}{kv_sep}{value}")
            if body:
                parts.append(body)
            parts.append(f"{sc}{name}/{ec}")
            return "\n".join(parts)
        else:
            # XML format: <name key="val">body</name> or <name key="val"/>
            attr_parts = [f'{k}="{v}"' for k, v in args.items()]
            attrs_str = " " + " ".join(attr_parts) if attr_parts else ""
            if body:
                return f"{sc}{name}{attrs_str}{ec}{body}{sc}/{name}{ec}"
            else:
                return f"{sc}{name}{attrs_str}/{ec}"


# Convenience function for non-streaming parsing
def parse_full(text: str, config: ParserConfig | None = None) -> list[ParseEvent]:
    """
    Parse a complete text (non-streaming).

    Args:
        text: Full text to parse
        config: Parser configuration

    Returns:
        List of all ParseEvents
    """
    parser = StreamParser(config)
    events = parser.feed(text)
    events.extend(parser.flush())
    return events
