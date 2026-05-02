# session/

Session persistence backed by KohakuVault. Stores everything needed to
resume an agent or terrarium in a single `.kohakutr` file (SQLite): conversation
snapshots, append-only event logs, channel message history, sub-agent
conversations, scratchpad state, token usage, and full-text search indexes.
`SessionOutput` is an output module that captures all agent events without
modifying the processing loop. `resume.py` rebuilds agents and terrariums
from saved state.

## Files

| File          | Description                                                                                                                                |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `__init__.py` | Re-exports `SessionStore`                                                                                                                  |
| `store.py`    | `SessionStore`: persistent storage with 8 table groups (meta, state, events, channels, subagents, jobs, conversation, fts) via KohakuVault |
| `output.py`   | `SessionOutput`: output module that records text chunks, tool activity, and processing state to the store                                  |
| `resume.py`   | `resume_agent`, `resume_terrarium`: rebuild from `.kohakutr` file, inject saved conversation and scratchpad                                |

## Dependencies

- `kohakuterrarium.builtins.inputs` (create_builtin_input, for resume IO)
- `kohakuterrarium.builtins.outputs` (create_builtin_output, for resume IO)
- `kohakuterrarium.core.agent` (Agent)
- `kohakuterrarium.core.conversation` (Conversation)
- `kohakuterrarium.modules.output.base` (OutputModule)
- `kohakuterrarium.terrarium.config` (load_terrarium_config)
- `kohakuterrarium.terrarium.runtime` (TerrariumRuntime)
- `kohakuterrarium.utils.logging`
- Third-party: `kohakuvault` (KVault, TextVault)
