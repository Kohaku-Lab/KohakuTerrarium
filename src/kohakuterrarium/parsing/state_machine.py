"""Parse structured LLM output incrementally across arbitrary chunk boundaries."""

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
    """Track partial tags and block content across stream chunks."""

    NORMAL = auto()
    MAYBE_OPEN = auto()
    OPEN_SLASH = auto()
    IN_OPEN_NAME = auto()
    IN_OPEN_ATTRS = auto()
    IN_BLOCK = auto()
    MAYBE_CLOSE = auto()
    IN_CLOSE_NAME = auto()
    EXPECT_CLOSE_SLASH = auto()
    IN_SELF_CLOSING = auto()


class StreamParser:
    """Convert streamed bracket, XML, or custom blocks into parse events.

    Call :meth:`flush` after the final chunk so incomplete markers become text.
    """

    def __init__(self, config: ParserConfig | None = None):
        self.config = config or ParserConfig()
        self._fmt: ToolCallFormat = self.config.tool_format
        self._reset()

    def _reset(self) -> None:
        """Restore the parser to its initial stream state."""
        self.state = ParserState.NORMAL
        self.text_buffer = ""
        self.name_buffer = ""
        self.block_buffer = ""
        self.current_name = ""
        self.attrs_buffer = ""
        self.inline_args: dict[str, str] = {}
        self._last_progress_log = 0

    def feed(self, chunk: str) -> list[ParseEvent]:
        """Consume one stream chunk and return events completed by it."""
        events: list[ParseEvent] = []

        for char in chunk:
            new_events = self._process_char(char)
            events.extend(new_events)

        return events

    def flush(self) -> list[ParseEvent]:
        """Emit buffered or incomplete content as text and reset the parser."""
        events: list[ParseEvent] = []
        sc = self._fmt.start_char

        if self.text_buffer:
            events.append(TextEvent(self.text_buffer))
            self.text_buffer = ""

        # Incomplete structured syntax remains user-visible text.
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
        """Advance the state machine by one character."""
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
        """Buffer plain text or begin a possible opening tag."""
        events: list[ParseEvent] = []

        if char == self._fmt.start_char:
            if self.text_buffer:
                events.append(TextEvent(self.text_buffer))
                self.text_buffer = ""
            self.state = ParserState.MAYBE_OPEN
        else:
            self.text_buffer += char

        return events

    def _handle_maybe_open(self, char: str) -> list[ParseEvent]:
        """Classify text following a possible opening delimiter."""
        events: list[ParseEvent] = []
        sc = self._fmt.start_char

        if char == "/":
            if self._fmt.slash_means_open:
                self.state = ParserState.OPEN_SLASH
            else:
                # A closing XML tag cannot begin outside an open block.
                self.text_buffer += sc + char
                self.state = ParserState.NORMAL
        elif char.isalpha() or char == "_":
            if self._fmt.slash_means_open:
                self.text_buffer += sc + char
                self.state = ParserState.NORMAL
            else:
                self.name_buffer = char
                self.state = ParserState.IN_OPEN_NAME
        else:
            self.text_buffer += sc + char
            self.state = ParserState.NORMAL

        return events

    def _handle_open_slash(self, char: str) -> list[ParseEvent]:
        """Read the first bracket-format opening-tag name character."""
        events: list[ParseEvent] = []

        if char.isalnum() or char == "_":
            self.name_buffer = char
            self.state = ParserState.IN_OPEN_NAME
        else:
            self.text_buffer += self._fmt.start_char + "/" + char
            self.state = ParserState.NORMAL

        return events

    def _handle_in_open_name(self, char: str) -> list[ParseEvent]:
        """Read an opening tag name and transition into its block."""
        events: list[ParseEvent] = []
        ec = self._fmt.end_char
        sc = self._fmt.start_char

        if char == ec:
            self.current_name = self.name_buffer
            self.name_buffer = ""
            self.block_buffer = ""
            self.inline_args = {}
            self.state = ParserState.IN_BLOCK

            if self.config.emit_block_events:
                events.append(BlockStartEvent(self.current_name))
            logger.debug("Block started", block_name=self.current_name)
        elif char == " " and self._fmt.arg_style == "inline":
            self.state = ParserState.IN_OPEN_ATTRS
            self.attrs_buffer = ""
        elif char.isalnum() or char == "_":
            self.name_buffer += char
        else:
            if self._fmt.slash_means_open:
                self.text_buffer += sc + "/" + self.name_buffer + char
            else:
                self.text_buffer += sc + self.name_buffer + char
            self.name_buffer = ""
            self.state = ParserState.NORMAL

        return events

    def _handle_in_open_attrs(self, char: str) -> list[ParseEvent]:
        """Read inline attributes until the XML opening tag closes."""
        events: list[ParseEvent] = []
        ec = self._fmt.end_char

        if char == "/":
            self.state = ParserState.IN_SELF_CLOSING
        elif char == ec:
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
        """Complete an XML self-closing tag or return its slash to attributes."""
        events: list[ParseEvent] = []
        ec = self._fmt.end_char

        if char == ec:
            self.inline_args = parse_attributes(self.attrs_buffer)
            self.current_name = self.name_buffer
            self.name_buffer = ""
            self.block_buffer = ""
            self.attrs_buffer = ""

            if self.config.emit_block_events:
                events.append(BlockStartEvent(self.current_name))

            events.extend(self._complete_block())
        else:
            # A slash only closes the tag when immediately followed by the delimiter.
            self.attrs_buffer += "/" + char
            self.state = ParserState.IN_OPEN_ATTRS

        return events

    def _handle_in_block(self, char: str) -> list[ParseEvent]:
        """Buffer block content or begin a possible closing tag."""
        events: list[ParseEvent] = []

        if char == self._fmt.start_char:
            self.state = ParserState.MAYBE_CLOSE
        else:
            self.block_buffer += char

        return events

    def _handle_maybe_close(self, char: str) -> list[ParseEvent]:
        """Classify a possible closing delimiter inside block content."""
        events: list[ParseEvent] = []
        sc = self._fmt.start_char

        if self._fmt.slash_means_open:
            if char.isalnum() or char == "_":
                self.name_buffer = char
                self.state = ParserState.IN_CLOSE_NAME
            elif char == "/":
                # Nested blocks are unsupported, so an opening marker remains content.
                self.block_buffer += sc + char
                self.state = ParserState.IN_BLOCK
            else:
                self.block_buffer += sc + char
                self.state = ParserState.IN_BLOCK
        else:
            if char == "/":
                self.name_buffer = ""
                self.state = ParserState.IN_CLOSE_NAME
            elif char.isalpha() or char == "_":
                # Non-closing XML-like tags inside a block remain body content.
                self.block_buffer += sc + char
                self.state = ParserState.IN_BLOCK
            else:
                self.block_buffer += sc + char
                self.state = ParserState.IN_BLOCK

        return events

    def _handle_in_close_name(self, char: str) -> list[ParseEvent]:
        """Read and validate a closing tag name."""
        events: list[ParseEvent] = []
        sc = self._fmt.start_char
        ec = self._fmt.end_char

        if char.isalnum() or char == "_":
            self.name_buffer += char
        elif self._fmt.slash_means_open and char == "/":
            self.state = ParserState.EXPECT_CLOSE_SLASH
        elif char == ec:
            if self._fmt.slash_means_open:
                self.block_buffer += sc + self.name_buffer + char
                self.name_buffer = ""
                self.state = ParserState.IN_BLOCK
            else:
                if self.name_buffer == self.current_name:
                    events.extend(self._complete_block())
                else:
                    # Mismatched closing tags remain block content.
                    logger.warning(
                        "Mismatched close marker",
                        expected=self.current_name,
                        got=self.name_buffer,
                    )
                    self.block_buffer += sc + "/" + self.name_buffer + ec
                    self.name_buffer = ""
                    self.state = ParserState.IN_BLOCK
        else:
            if self._fmt.slash_means_open:
                self.block_buffer += sc + self.name_buffer + char
            else:
                self.block_buffer += sc + "/" + self.name_buffer + char
            self.name_buffer = ""
            self.state = ParserState.IN_BLOCK

        return events

    def _handle_expect_close_slash(self, char: str) -> list[ParseEvent]:
        """Finish or reject a bracket-format closing tag."""
        events: list[ParseEvent] = []
        sc = self._fmt.start_char
        ec = self._fmt.end_char

        if char == ec:
            if self.name_buffer == self.current_name:
                events.extend(self._complete_block())
            else:
                # Mismatched closing tags remain block content.
                logger.warning(
                    "Mismatched close marker",
                    expected=self.current_name,
                    got=self.name_buffer,
                )
                self.block_buffer += sc + self.name_buffer + "/" + ec
                self.name_buffer = ""
                self.state = ParserState.IN_BLOCK
        else:
            self.block_buffer += sc + self.name_buffer + "/" + char
            self.name_buffer = ""
            self.state = ParserState.IN_BLOCK

        return events

    def _complete_block(self) -> list[ParseEvent]:
        """Classify a completed block, emit events, and reset block state."""
        events: list[ParseEvent] = []
        name = self.current_name
        content = self.block_buffer

        if self._fmt.arg_style == "inline" and self.inline_args:
            args = dict(self.inline_args)
            body = content.strip()
        else:
            args, body = self._parse_block_content(content)

        raw = self._build_raw(name, args, body)

        # Output tags take precedence over registries with overlapping names.
        is_output, output_target = is_output_tag(name, self.config.known_outputs)
        if is_output:
            events.append(OutputCallEvent(target=output_target, content=body, raw=raw))
            logger.debug("Parsed output block", target=output_target)

        elif is_tool_tag(name, self.config.known_tools):
            tool_args = {**args}
            if body:
                content_arg = self.config.content_arg_map.get(name, "content")
                # Explicit arguments take precedence over block body content.
                if content_arg not in tool_args:
                    tool_args[content_arg] = body
            events.append(ToolCallEvent(name=name, args=tool_args, raw=raw))
            logger.debug("Parsed tool call", tool_name=name)

        elif is_subagent_tag(name, self.config.known_subagents):
            subagent_args = {"task": body.strip(), **args}
            events.append(SubAgentCallEvent(name=name, args=subagent_args, raw=raw))
            logger.debug("Parsed sub-agent call", subagent_type=name)

        elif is_command_tag(name, self.config.known_commands):
            cmd_args = body.strip()
            events.append(CommandEvent(command=name, args=cmd_args, raw=raw))
            logger.debug("Parsed command", command=name)

        else:
            # Unknown blocks are preserved verbatim instead of being discarded.
            logger.warning("Unknown block type", block_name=name)
            events.append(TextEvent(raw))

        if self.config.emit_block_events:
            events.append(BlockEndEvent(name))

        self.current_name = ""
        self.name_buffer = ""
        self.block_buffer = ""
        self.attrs_buffer = ""
        self.inline_args = {}
        self.state = ParserState.NORMAL

        return events

    def _parse_block_content(self, content: str) -> tuple[dict[str, str], str]:
        """Split leading argument lines from the remaining block body."""
        args: dict[str, str] = {}
        body_lines: list[str] = []
        in_args = True
        prefix = self._fmt.arg_prefix
        kv_sep = self._fmt.arg_kv_sep

        for line in content.split("\n"):
            if in_args and line.strip() == "":
                continue
            if in_args and prefix and line.startswith(prefix):
                arg_content = line[len(prefix) :]
                if kv_sep in arg_content:
                    key, value = arg_content.split(kv_sep, 1)
                    args[key.strip()] = value.strip()
                else:
                    args[arg_content.strip()] = ""
            else:
                # Argument parsing stops permanently at the first body line.
                in_args = False
                body_lines.append(line)

        body = "\n".join(body_lines).strip()
        return args, body

    def _build_raw_open(self) -> str:
        """Reconstruct the current opening tag for incomplete-block fallback."""
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
        """Reconstruct a completed block in its configured syntax."""
        sc = self._fmt.start_char
        ec = self._fmt.end_char

        if self._fmt.slash_means_open:
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
            attr_parts = [f'{k}="{v}"' for k, v in args.items()]
            attrs_str = " " + " ".join(attr_parts) if attr_parts else ""
            if body:
                return f"{sc}{name}{attrs_str}{ec}{body}{sc}/{name}{ec}"
            else:
                return f"{sc}{name}{attrs_str}/{ec}"


def parse_full(text: str, config: ParserConfig | None = None) -> list[ParseEvent]:
    """Parse complete text by feeding and flushing a temporary stream parser."""
    parser = StreamParser(config)
    events = parser.feed(text)
    events.extend(parser.flush())
    return events
