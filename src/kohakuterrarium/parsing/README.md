# parsing/

Streaming state machine parser for LLM output. Detects tool calls, sub-agent
dispatches, framework commands, and output blocks from partial text chunks
as they arrive during LLM streaming. Supports two configurable format
families: bracket format (`[/tool]...@@arg=val...[tool/]`, default) and
XML format (`<tool arg="val">...</tool>`). The parser emits typed
`ParseEvent` objects consumed by the output router and the executor.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Re-exports parser, all event types, format config, pattern functions, and extraction helpers |
| `state_machine.py` | `StreamParser` (incremental state machine), `ParserState` enum, `parse_full` (non-streaming convenience) |
| `events.py` | Parse event types: `TextEvent`, `ToolCallEvent`, `SubAgentCallEvent`, `CommandEvent`, `CommandResultEvent`, `OutputCallEvent`, `BlockStartEvent`, `BlockEndEvent` |
| `format.py` | `ToolCallFormat` dataclass, `BRACKET_FORMAT` and `XML_FORMAT` presets, `format_tool_call_example` |
| `patterns.py` | `ParserConfig`, tag detection functions (`is_tool_tag`, `is_subagent_tag`, `is_command_tag`, `is_output_tag`), attribute parsing, content-arg mapping |

## Dependencies

- `kohakuterrarium.parsing.format` (internal, used by patterns and state_machine)
- No external kohakuterrarium dependencies (self-contained module)
