# session/

Session persistence backed by KohakuVault. Stores everything needed to
resume an agent or terrarium in a single `.kohakutr` file (SQLite): conversation
snapshots, append-only event logs, channel message history, sub-agent
conversations, scratchpad state, token usage, and full-text search indexes.
`SessionOutput` is an output module that captures all agent events without
modifying the processing loop. `resume.py` rebuilds agents from saved
state; engine-level terrarium resume lives in `terrarium/resume.py`.

## Files

| File                    | Description                                                                                                                                |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `__init__.py`           | Re-exports `SessionStore`                                                                                                                  |
| `store.py`              | `SessionStore`: persistent storage with 8 table groups (meta, state, events, channels, subagents, jobs, conversation, fts) via KohakuVault |
| `store_fork.py`         | Fork / branch primitive for `SessionStore`                                                                                                 |
| `store_counters.py`, `store_protocol.py`, `token_views.py`, `version.py` | Store helpers: counter restore, helper protocols, read-side token-usage views, format versioning |
| `reader.py`             | `SessionReader`: read a finished `.kohakutr` without spelunking                                                                            |
| `output.py`             | `SessionOutput`: output module that records text chunks, tool activity, and processing state to the store                                  |
| `resume.py`             | `resume_agent` / `detect_session_type`: rebuild from a `.kohakutr` file, inject saved conversation and scratchpad (engine-level terrarium resume lives in `terrarium/resume.py`) |
| `attach.py`, `agent_attach.py`, `attachment_service.py` | Attach/detach a store to a live agent (compat re-exports + service)                                       |
| `session.py`            | Async wrapper around a running agent + `SessionStore`                                                                                      |
| `artifacts.py`          | Session-local artifact helpers                                                                                                             |
| `rollup.py`             | Per-turn rollup helpers                                                                                                                    |
| `sync.py`               | Session event mirroring across the Laboratory layer                                                                                       |
| `memory.py`, `embedding.py` | `SessionMemory` FTS5 + vector search; embedding providers                                                                              |
| `history.py`            | Event-history normalization                                                                                                                |
| `errors.py`, `migrations/` | Session-level exceptions; format migrations                                                                                             |

## Dependencies

- `kohakuterrarium.builtins.inputs` (create_builtin_input, for resume IO)
- `kohakuterrarium.builtins.outputs` (create_builtin_output, for resume IO)
- `kohakuterrarium.core.agent` (Agent)
- `kohakuterrarium.core.config_serde` (unpack_agent_config)
- `kohakuterrarium.core.conversation` (Conversation)
- `kohakuterrarium.laboratory.protocols` (LabNotifier / LabRegistrar, for sync)
- `kohakuterrarium.modules.input.base` / `modules.output.base`
- `kohakuterrarium.packages.resolve` (resolve_any_path)
- `kohakuterrarium.utils.logging`
- Third-party: `kohakuvault` (KVault, TextVault)
