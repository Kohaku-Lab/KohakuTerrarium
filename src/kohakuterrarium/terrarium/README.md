# terrarium/

Multi-agent orchestration runtime. The terrarium is a **pure wiring layer**
with no intelligence of its own: it loads standalone creature configs,
creates channels between them, injects channel triggers, and manages
lifecycle. Intelligence lives in creatures (and in the optional root agent
that sits OUTSIDE the terrarium).

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Re-exports `TerrariumRuntime`, `TerrariumAPI`, configs, hot-plug, observer |
| `runtime.py` | `TerrariumRuntime` — lifecycle orchestration (start, stop, channel wiring) with `HotPlugMixin` |
| `api.py` | `TerrariumAPI` — programmatic facade wrapping a `TerrariumRuntime` |
| `config.py` | `TerrariumConfig`, `CreatureConfig`, `ChannelConfig`, `load_terrarium_config`, `build_channel_topology_prompt` |
| `factory.py` | `build_creature`, `build_root_agent` — construct Agent instances; wire triggers and topology prompt |
| `creature.py` | `CreatureHandle` — wrapper around an `Agent` with terrarium metadata (channels, config) |
| `hotplug.py` | `HotPlugMixin` — add / remove creatures and channels at runtime without restart |
| `observer.py` | `ChannelObserver`, `ObservedMessage` — non-destructive channel message recording |
| `output_log.py` | `OutputLogCapture`, `LogEntry` — tee wrapper that captures creature output for observability |
| `persistence.py` | `attach_session_store`, `build_conversation_from_messages` — session store wiring and resume helpers |
| `tool_manager.py` | `TerrariumToolManager` — shared state for terrarium management tools (stored in environment) |
| `tool_registration.py` | `ensure_terrarium_tools_registered` — lazy import of terrarium tools to avoid circular imports |
| `cli.py` | CLI subcommands (`kt terrarium run` / `resume` / `list`), TUI + headless drivers |
| `cli_output.py` | `CLIOutput` — minimal prefixed-stdout output for headless terrarium mode |

## Dependency direction

Imported by: `cli/` (via `cli/__init__.py` and `cli_rich/`), `api/` (indirectly
via `serving/`), `serving/manager.py`, `builtins/tools/terrarium_*.py`
(lazy).

Imports: `core/` (Agent, channel, conversation, environment, session),
`builtins/inputs.NoneInput`, `builtins/tool_catalog` (for the root agent),
`modules/output`, `modules/trigger/channel`, `session/store`, `utils/logging`.

One-way dependency: `terrarium/` → `core/`, never `core/` → `terrarium/`.

## Key entry points

- `TerrariumRuntime(config).start()` — construct + start all creatures
- **`TerrariumAPI(runtime)`** — the stable programmatic facade. Most external
  callers (HTTP API, root-agent tools, tests) talk to the terrarium through
  this, not through `TerrariumRuntime` directly. Exposes:
  - channel ops: `channel_list`, `channel_read`, `channel_send`, `observe`
  - creature ops: `creature_list`, `creature_start`, `creature_stop`, `status`
  - lifecycle: `stop`, `status`
- `build_creature(name, config, ...)` / `build_root_agent(config, runtime)`
  — the two Agent-construction factories
- `HotPlugMixin.add_creature` / `remove_creature` / `add_channel`
- `ChannelObserver.observe(channel_name)` — non-destructive stream
- `load_terrarium_config(path)` — parse terrarium YAML

## Notes

- A terrarium has NO LLM of its own. The optional root agent sits OUTSIDE
  the terrarium (a normal creature with terrarium tools bound) — it is the
  user's interface; the terrarium obeys its orders through `TerrariumAPI`.
- `tool_registration.py` exists purely to break the
  `core → builtins/tools → terrarium → core` circular import cycle. Tools
  are registered only on first terrarium use.
- `ChannelObserver` never consumes messages from a channel — it attaches a
  callback so observers and normal listeners co-exist.
- Session persistence works exactly like single-agent sessions, but each
  creature gets its own `.kohakutr` file; the terrarium writes a sidecar
  index linking them.

## See also

- `../core/README.md` — `Agent` + channel primitives
- `../serving/manager.py` — `KohakuManager.terrarium_*` methods (HTTP layer)
- `../builtins/tools/terrarium_*.py` — the tools the root agent uses to drive the terrarium
- `docs/concepts/multi-agent/` — creature vs terrarium vs root agent model
