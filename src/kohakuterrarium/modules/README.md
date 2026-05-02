# modules/ (Plugin Protocols)

The modules directory defines the plugin protocols that all framework components
implement. Each subdirectory provides a base protocol (interface), an abstract
base class with common logic, and any built-in variants.

## Subdirectories

### tool/ (`base.py`)

Defines the tool protocol that all tools implement.

| Class           | Purpose                                                                       |
| --------------- | ----------------------------------------------------------------------------- |
| `BaseTool`      | Abstract base class with `execute()`, `tool_name`, `description`              |
| `ToolContext`   | Injected context (session with channels/scratchpad, working dir, memory path) |
| `ToolResult`    | Return type with output, exit_code, error, metadata                           |
| `ToolConfig`    | Per-tool config (timeout, max_output, working_dir, env)                       |
| `ToolInfo`      | Name + one-line description pair for prompt aggregation                       |
| `ExecutionMode` | Enum: `DIRECT`, `BACKGROUND`, `STATEFUL`                                      |

Tools opt into context injection with `needs_context = True`.

### trigger/ (`base.py`, `timer.py`, `context.py`, `channel.py`)

Automatic event generators that fire without user input.

| Class                  | File         | Purpose                                                         |
| ---------------------- | ------------ | --------------------------------------------------------------- |
| `BaseTrigger`          | `base.py`    | Abstract trigger with `start()`, `stop()`, `wait_for_trigger()` |
| `TimerTrigger`         | `timer.py`   | Fire at fixed intervals (supports `immediate` flag)             |
| `ContextUpdateTrigger` | `context.py` | Fire when external context changes                              |
| `ChannelTrigger`       | `channel.py` | Fire when a named channel receives a message                    |

### subagent/ (`config.py`, `manager.py`, `base.py`, `interactive.py`)

Sub-agent lifecycle management.

| Class                              | File                         | Purpose                                                    |
| ---------------------------------- | ---------------------------- | ---------------------------------------------------------- |
| `SubAgentConfig`                   | `config.py`                  | Full config: tools, prompt, output routing, limits         |
| `SubAgentManager`                  | `manager.py`                 | Spawn, track, and collect results from sub-agents          |
| `SubAgent` / `InteractiveSubAgent` | `base.py` / `interactive.py` | Single-run and long-lived sub-agent execution              |
| `OutputTarget`                     | `config.py`                  | Enum: `CONTROLLER` or `EXTERNAL`                           |
| `ContextUpdateMode`                | `config.py`                  | Enum: `INTERRUPT_RESTART`, `QUEUE_APPEND`, `FLUSH_REPLACE` |

### input/ (`base.py`)

Input modules receive external input and produce `TriggerEvent`s.

- `InputModule`: Protocol with `start()`, `stop()`, `get_input()`
- `BaseInputModule`: ABC with running-state management

### output/ (`base.py`, `router.py`)

Output modules deliver agent output to destinations.

- `OutputModule`: Protocol with `write()`, `write_stream()`, `flush()`
- `BaseOutputModule`: ABC with common lifecycle hooks
- `OutputRouter` (`router.py`): State machine that routes parse events to the
  correct output module. Handles normal text, tool blocks (suppressed), and
  named output targets (`[/output_name]...[output_name/]`).
- `MultiOutputRouter` (`router.py`): Router variant that dispatches to multiple
  output modules simultaneously.
- `OutputState` (`router.py`): Enum tracking the router's current state
  (normal, inside tool block, inside output block).
