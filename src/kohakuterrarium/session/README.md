# session/

Session persistence backed by KohakuVault. Stores everything needed to
resume an agent or terrarium in a single `.kohakutr` file (SQLite): conversation
snapshots, append-only event logs, channel message history, sub-agent
conversations, scratchpad state, token usage, and full-text search indexes.
`SessionOutput` (in `output/`) is an output module that captures all agent
events without modifying the processing loop. `resume/` rebuilds agents from
saved state; engine-level terrarium resume lives in `terrarium/resume.py`.

## Layout

| Path                | Description                                                                                                                  |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py`       | Lazy facade: `SessionStore`                                                                                                  |
| `store/`            | `SessionStore` (8 table groups via KohakuVault), assembled from parts, plus its internals: key index, branch index, table policy, format version, artifacts, rollups, snapshots, identity, token views; see `store/README.md` |
| `vault/`            | `handles` (release native table handles), `io` (batched reads/copies), `read` (read-only table opens)                        |
| `occupancy/`        | Process-local admission and occupancy: `gate` (resource gate), `registry` (open stores), `family` (sidecar identities), `mutation` (cancellation-safe mutation workers) |
| `readonly/`         | `metadata` (strict metadata reads), `view` (selective queries), `reader` (`SessionReader`), `candidate` (admission-controlled reads) |
| `history/`          | `replay` (normalize, branch-select, replay events), `paging`, `records`, `selection`, `raw`, `assistant`, `branch_paths`      |
| `resume/`           | `resume` (`resume_agent` / `detect_session_type`), `async_flow`, `branch`, `build`, `open`, `target` (retired-file resolution) |
| `memory/`           | `index` (`SessionMemory`: FTS5 + vector search), `embedding`, `async_search`, `blocks`, `resources`, `legacy`                  |
| `output/`           | `output` (`SessionOutput`), `activity`, `assistant`, `widgets`, `text_buffer`                                                 |
| `attach/`           | `agent` (methods bound onto `Agent`), `service` (host-scoped attach/detach), `compat` (legacy re-exports)                      |
| `session.py`        | Async wrapper around a running agent + `SessionStore`                                                                        |
| `sync.py`           | Session event mirroring across the Laboratory layer                                                                          |
| `run_state.py`      | Crash-tolerant run records per process boot                                                                                  |
| `job_reaper.py`     | Finds jobs a server restart killed                                                                                           |
| `errors.py`         | Session-level exceptions                                                                                                     |
| `migrations/`       | Format migrations                                                                                                            |

## Dependencies

- `kohakuterrarium.builtins.inputs` (create_builtin_input, for resume IO)
- `kohakuterrarium.builtins.outputs` (create_builtin_output, for resume IO)
- `kohakuterrarium.core.agent` (Agent)
- `kohakuterrarium.core.config.serde` (unpack_agent_config)
- `kohakuterrarium.core.conversation.conversation` (Conversation)
- `kohakuterrarium.laboratory.protocols` (LabNotifier / LabRegistrar, for sync)
- `kohakuterrarium.modules.input.base` / `modules.output.base`
- `kohakuterrarium.packages.resolve` (resolve_any_path)
- `kohakuterrarium.utils.logging`
- Third-party: `kohakuvault` (KVault, TextVault)
