---
title: Python API
summary: The public kohakuterrarium surface: errors, Agent, turn results, the Terrarium engine, Creature, sessions, packages, compose, validate, and testing.
tags:
  - reference
  - python
  - api
---

# Python API

The canonical reference for the public Python surface. Every signature
on this page is verified against source by
`tests/unit/test_docs_python_reference.py`; if a symbol here drifts
from the code, CI fails.

Everything you normally need is importable from the package root:

```python
import kohakuterrarium as kt

kt.Agent          # single-agent runtime
kt.Terrarium      # multi-agent engine
kt.Creature       # running-agent handle inside the engine
kt.Studio         # management facade (catalog / sessions / persistence)
kt.tool           # @kt.tool: plain function -> agent tool
kt.FunctionTool   # the class @kt.tool produces
kt.SessionReader  # read-only .kohakutr inspection
kt.SessionStore   # raw session persistence
kt.TurnResult, kt.TextChunk, kt.Activity, kt.TurnEnded   # turn surface
kt.EngineEvent, kt.EventKind, kt.EventFilter             # engine events
kt.ConnectionResult, kt.DisconnectionResult              # topology results
kt.errors         # typed exception hierarchy
kt.validate       # pre-flight validation helpers
kt.packages       # package install / resolve facade (subpackage)
```

`kt.compose` and `kt.testing` are imported as subpackages
(`from kohakuterrarium.compose import agent, factory, pure`,
`from kohakuterrarium.testing.llm import ScriptedLLM`).

Narrative walkthroughs: [programmatic usage](../guides/programmatic-usage.md),
[composition](../guides/composition.md), [sessions](../guides/sessions.md),
[packages](../guides/packages.md). `Studio` is covered in
[guides/studio](../guides/studio.md) and [concepts/studio](../concepts/studio.md).

---

## Errors & strictness

Module: `kohakuterrarium.errors`. Every error the framework raises on a
programmatic surface derives from `KTError`, so one `except` catches
them all. Many subclasses ALSO derive from the builtin exception the
same failure historically raised (`FileNotFoundError` / `ValueError` /
`TimeoutError`), so existing `except` sites keep working.

- `KTError`: base class for every KohakuTerrarium error.
- Configuration:
  - `ConfigError(KTError, ValueError)`: invalid agent / terrarium config content.
  - `ConfigNotFoundError(ConfigError, FileNotFoundError)`: config path or `@pkg` ref not found.
- Packages:
  - `PackageError(KTError)`: base for package-system errors.
  - `PackageRefError(PackageError, ValueError)`: malformed `@` reference.
  - `PackageNotInstalledError(PackageError, FileNotFoundError)`: `@<pkg>/...` names an uninstalled package.
  - `PackagePathNotFoundError(PackageError, FileNotFoundError)`: package exists, sub-path doesn't.
- LLM:
  - `LLMError(KTError)`: provider construction or call failure.
  - `LLMNotConfiguredError(LLMError, ValueError)`: no usable LLM resolves (missing key, unknown profile).
- Sessions:
  - `SessionError(KTError)`: persistence / resume failure.
  - `SessionNotResumableError(SessionError, ValueError)`: file exists but cannot be resumed.
  - `SessionNotFoundError(SessionError, NotFoundError, FileNotFoundError)`: named session doesn't exist.
- Turn execution:
  - `TurnError(KTError)`: a turn failed (provider error, unrecoverable tool crash).
  - `TurnTimeoutError(TurnError, TimeoutError)`: turn exceeded its `timeout=` budget and was cancelled.
  - `AgentNotRunningError(KTError, RuntimeError)`: operation needed a started agent.
- Request-shaped (used by the studio tier; the HTTP adapter maps them
  to status codes): `NotFoundError(KTError, KeyError)`,
  `InvalidRequestError(KTError, ValueError)`, `ConflictError(KTError)`.

**Strict by default.** Programmatic constructors (`Agent.build`,
`Agent.from_path`, `Terrarium.add_creature`, `Terrarium.apply_recipe`)
take `strict: bool = True`: an unresolvable LLM, unknown tool, or
broken plugin raises instead of degrading silently. Interactive
frontends pass `strict=False`. `Agent.run` / `Creature.run` raise
`TurnError` / `TurnTimeoutError` on failure unless you pass
`raise_on_error=False`.

```python
import kohakuterrarium as kt

try:
    agent = await kt.Agent.build("@kt-biome/creatures/general")
except kt.errors.KTError as e:
    print(f"setup failed: {e}")
```

---

## Agent

Module: `kohakuterrarium.core.agent` (re-exported as
`kohakuterrarium.Agent`). The single-agent runtime: LLM controller,
tools, triggers, sub-agents, I/O.

Construction:

- `await Agent.build(config, *, llm=None, pwd=None, io="config", strict=True, tools=None, plugins=None, subagents=None, outputs=None, user_commands=None, input_module=None, output_module=None, session=None, environment=None) -> Agent`:
  the canonical programmatic constructor.
  - `config`: config folder path, `@pkg/...` reference, or an
    `AgentConfig` instance.
  - `llm`: provider instance (e.g. `ScriptedLLM`), selector string
    (profile / preset name or `provider/model[@variations]`),
    `LLMProfile`, or `None` (resolve from config).
  - `io`: `"config"` (boot I/O as declared), `"none"` (input
    suppressed), `"headless"` (input suppressed AND default output
    silenced; the batch default). Explicit `input_module` /
    `output_module` win over `io`.
  - `tools` / `plugins` / `subagents`: instances registered before
    the system prompt aggregates (`kt.tool` adapters, `BasePlugin`
    objects, `SubAgentConfig`s).
  - `outputs`: extra named outputs `{name: OutputModule}`;
    `user_commands`: extra slash commands `{name: UserCommand}`.
  - Returns a configured agent that is **not started**.
- `Agent.from_path(config_path, *, input_module=None, output_module=None, session=None, environment=None, llm=None, pwd=None, strict=True, tools=None, plugins=None) -> Agent`:
  sync low-level constructor; prefer `build` in new code.

Lifecycle:

- `await agent.start()` / `await agent.stop()`: always pair them
  (there is no `async with` on `Agent`).
- `await agent.run_forever()`: the legacy autonomous main loop
  (input module + triggers drive the agent until the input exits).
  This is what `kt run` does; one-shot scripts use `run` instead.
- `agent.interrupt()`: cancel the active turn (non-blocking).

Turn drivers (see [Turn results & events](#turn-results--events)):

- `await agent.run(content, *, timeout=None, source="programmatic", raise_on_error=True) -> TurnResult`
- `agent.run_stream(content, *, timeout=None, source="programmatic") -> AsyncIterator[TurnEvent]`

Runtime extension (each refreshes the live system prompt):

- `agent.add_tool(tool)`: registry + executor + prompt in one call; idempotent on `tool_name`.
- `await agent.add_plugin(plugin, *, enabled=True)`: fires the plugin's `on_load` even when added after `start()`.
- `agent.add_subagent(config)`: register a `SubAgentConfig`.
- `agent.refresh_system_prompt()`: recompute the aggregated prompt by hand.

Other runtime controls:

- `await agent.inject_input(content, source="programmatic") -> bool`: push input without consuming output.
- `agent.switch_model(profile_name) -> str` / `agent.llm_identifier() -> str`
- `agent.attach_session_store(store)`: wire a `SessionStore` sink.
- `agent.set_output_handler(handler, replace_default=False)`
- Properties: `is_running`, `tools`, `subagents`, `conversation_history`.

```python
import kohakuterrarium as kt

agent = await kt.Agent.build("@kt-biome/creatures/general", io="headless")
await agent.start()
try:
    result = await agent.run("Summarize ./README.md", timeout=300)
    print(result.text)
finally:
    await agent.stop()
```

### `@kt.tool` / `FunctionTool`

Module: `kohakuterrarium.modules.tool.function` (re-exported as
`kohakuterrarium.tool` / `kohakuterrarium.FunctionTool`). Turns a plain
sync or async function into an agent tool: name from the function name,
description from the first docstring line, JSON-schema parameters from
the type hints. Sync functions run via `asyncio.to_thread`. A `context`
parameter receives the `ToolContext`.

- `tool(fn=None, *, name=None, description=None, execution_mode=ExecutionMode.DIRECT) -> FunctionTool | decorator`:
  works as `@tool`, `@tool(name=..., description=...)`, or a direct
  call `tool(existing_fn)`.
- `FunctionTool(fn, *, name=None, description=None, execution_mode=...)`: the class behind it.

```python
import kohakuterrarium as kt

@kt.tool
def check_stock(item: str) -> str:
    """Look up how many units of an item are in stock."""
    return f"{item}: 3 in stock"

agent = await kt.Agent.build(cfg, tools=[check_stock])
```

---

## Turn results & events

Module: `kohakuterrarium.core.turn` (all four types re-exported from
the package root). The typed observation surface returned / yielded by
`run`, `run_stream`, and `attach`.

- `TurnResult`: outcome of one full turn:
  - `status: str`: `"ok"` | `"error"` | `"timeout"` | `"interrupted"`.
  - `ok: bool`: property, `status == "ok"`.
  - `text: str`: concatenated assistant text.
  - `error: str | None`: failure detail when status != ok.
  - `tool_calls: list[Activity]`: the `tool_start` / `tool_done` / `tool_error` activities.
  - `activities: list[Activity]`: every non-text event of the turn.
  - `usage: dict | None`: token usage when the provider reported it.
  - `duration_s: float`
- `TextChunk`: `text: str`; one streamed piece of assistant text.
- `Activity`: `kind: str`, `detail: str`, `metadata: dict`; a
  non-text event (`tool_start`, `tool_done`, `tool_error`,
  `subagent_start`, `subagent_done`, `processing_start`,
  `processing_end`, `processing_error`, `session_info`, `ask_user`, ...).
- `TurnEnded`: `result: TurnResult`; terminal event of a `run_stream`.
- `TurnEvent = TextChunk | Activity | TurnEnded`: the union the
  streams yield.
- `AgentEventStream`: the open-ended observer behind
  `Creature.attach()`: async context manager + async iterator of
  `TurnEvent`s; non-destructive and multi-consumer.

Semantics worth knowing:

- `run` raises `TurnError` / `TurnTimeoutError` by default; pass
  `raise_on_error=False` to always get the `TurnResult` and branch on
  `result.status` yourself.
- `timeout=` actually **interrupts** the turn (the controller loop is
  cancelled and unwound); it does not abandon a still-burning turn.
- `run_stream` never raises mid-iteration: errors surface as
  `Activity(kind="processing_error")` and in the final
  `TurnEnded(result)`.

```python
async for ev in agent.run_stream("Refactor utils.py"):
    match ev:
        case kt.TextChunk(text=t):
            print(t, end="", flush=True)
        case kt.Activity(kind="tool_start", detail=d):
            print(f"\n[tool] {d}")
        case kt.TurnEnded(result=r):
            print(f"\n[done: {r.status}]")
```

---

## Terrarium engine

Module: `kohakuterrarium.terrarium.engine` (re-exported as
`kohakuterrarium.Terrarium`). The multi-agent runtime engine. It hosts
every running creature in the process; a solo agent is a 1-creature
graph.

Construction:

- `Terrarium(*, pwd=None, session_dir=None)`: bare engine.
  `session_dir` turns on **autosession**: every new graph automatically
  gets a `<session_dir>/<graph_id>.kohakutr` store (merge / split
  children land there too).
- `await Terrarium.from_recipe(recipe, *, pwd=None) -> Terrarium`:
  engine with a recipe applied (`TerrariumConfig` or YAML path / `@pkg` ref).
- `await Terrarium.resume(store, *, pwd=None, llm=None) -> Terrarium`:
  fresh engine + adopt a saved session (`SessionStore` or path;
  `llm` is a selector string override).
- `await Terrarium.with_creature(config, *, pwd=None) -> tuple[Terrarium, Creature]`:
  engine + one creature in one call.
- `async with Terrarium() as engine: ...`: `__aexit__` calls `shutdown()`.

Creature CRUD:

- `await engine.add_creature(config, *, graph=None, creature_id=None, llm=None, pwd=None, start=True, is_privileged=False, parent_creature_id=None, io="config", strict=True, session=None, name=None, tools=None, plugins=None) -> Creature`
  - `config`: path / `@pkg/...` ref, `AgentConfig`, `CreatureConfig`,
    or a pre-built `Creature` (build-time kwargs raise on a pre-built one).
  - `session`: persistence control: a path mints a store at exactly
    that file; `True` mints in the default session dir; `False`
    disables persistence even under autosession; a `SessionStore`
    attaches as-is; `None` (default) follows the engine (autosession /
    graph's existing store / nothing).
  - `is_privileged`: grants the `group_*` graph-mutation tool surface
    (elevate-only; never demotes a pre-built creature).
  - `llm` / `io` / `strict` / `tools` / `plugins`: same contract as
    `Agent.build`.
  - `name`: spawn-time display-name override.
- `await engine.remove_creature(creature)`: stop + remove; may auto-split the graph.
- `engine.get_creature(creature_id) -> Creature` / `engine.list_creatures() -> list[Creature]`
- Pythonic accessors: `engine[id]`, `id in engine`, `for c in engine`, `len(engine)`.

Channels and topology (all graph channels are broadcast: every
listener receives every send):

- `await engine.add_channel(graph, name, description="") -> ChannelInfo`
- `await engine.remove_channel(graph, name) -> TopologyDelta`: may auto-split.
- `await engine.connect(sender, receiver, *, channel=None) -> ConnectionResult`:
  cross-graph connect auto-merges (environments union, session stores merge).
- `await engine.disconnect(sender, receiver, *, channel=None) -> DisconnectionResult`:
  may auto-split (each side gets its own store copy).
- `engine.environment(graph) -> Environment`: public handle for a
  graph's live environment (raises `KeyError` on unknown graph).
- `engine.channel(graph, name)`: live broadcast-channel handle (or
  `None`): `await ch.send(ChannelMessage(...))` to seed a graph,
  `ch.history` to observe traffic.
- `engine.get_graph(graph_id) -> GraphTopology` / `engine.list_graphs() -> list[GraphTopology]`
- `await engine.assign_root(creature, *, report_channel="report_to_root") -> RootAssignment`:
  elevate a creature to the privileged node of its graph and wire
  report channels.

Recipes and resume-into:

- `await engine.apply_recipe(recipe, *, graph=None, pwd=None, llm=None, strict=True, session=None, creature_builder=None) -> GraphTopology`:
  `session` follows the `add_creature` contract but mints one
  terrarium-typed store for the whole graph.
- `await engine.adopt_session(store, *, pwd=None, llm=None) -> str`:
  resume a saved session into this running engine; returns the new
  `graph_id`.
- `await engine.attach_session(graph, store)`: attach a
  `SessionStore` (or mint one at a path) to a graph.

Lifecycle and output wiring:

- `await engine.start(creature)` / `await engine.stop(creature)` / `await engine.stop_graph(graph)`
- `await engine.shutdown()`: stop everything, close every store the
  engine minted, terminate subscribers; idempotent.
- `await engine.wire_output(creature, target) -> str` / `await engine.unwire_output(creature, edge_id) -> bool`
- `engine.list_output_wiring(creature) -> list[dict]`
- `await engine.wire_output_sink(creature, sink) -> str` / `await engine.unwire_output_sink(creature, sink_id) -> bool`

Observability:

- `engine.subscribe(filter=None) -> AsyncIterator[EngineEvent]`: the
  subscriber registers immediately at the call (events between
  `subscribe()` and the first `await` are buffered); breaking out
  de-registers; `shutdown()` terminates it.
- `engine.status()` (roll-up) / `engine.status(creature)` (per-creature dict).

Anywhere a `CreatureRef` / `GraphRef` is accepted you may pass the
object or its string id.

### Engine events

Module: `kohakuterrarium.terrarium.events` (re-exported from the root).
The engine bus carries **structure** events only; per-creature content
(text, tool activity) flows through the typed turn surface instead.

- `EventKind` (`str` enum): `CHANNEL_MESSAGE`, `TOPOLOGY_CHANGED`,
  `SESSION_KIND_CHANGED`, `CREATURE_ADDED`, `CREATURE_STARTED`,
  `CREATURE_STOPPED`, `OUTPUT_WIRE_ADDED`, `OUTPUT_WIRE_REMOVED`,
  `PARENT_LINK_CHANGED`.
- `EngineEvent`: `kind`, `creature_id`, `graph_id`, `channel`,
  `payload: dict`, `ts: float`.
- `EventFilter(kinds=None, creature_ids=None, graph_ids=None, channels=None)`:
  fields AND-combine; `None` means "any"; `matches(ev) -> bool`.
- `ConnectionResult`: `channel`, `trigger_id`, `delta_kind`
  (`"nothing"` | `"merge"`), `graph_id`.
- `DisconnectionResult`: `channels: list[str]`, `delta_kind`
  (`"nothing"` | `"split"`).

```python
import kohakuterrarium as kt

async with kt.Terrarium(session_dir="runs/") as engine:
    alice = await engine.add_creature("@kt-biome/creatures/general")
    bob = await engine.add_creature("@kt-biome/creatures/general")

    # Subscribe BEFORE mutating: events emitted between subscribe()
    # and the first await are buffered, so none are lost.
    events = engine.subscribe(kt.EventFilter(kinds={kt.EventKind.TOPOLOGY_CHANGED}))
    result = await engine.connect(alice, bob, channel="alice_to_bob")
    assert result.delta_kind == "merge"   # two graphs became one
    ev = await anext(events)
    print(ev.kind, ev.payload)
```

---

## Creature

Module: `kohakuterrarium.terrarium.creature_host` (re-exported as
`kohakuterrarium.Creature`). The engine's handle for one running agent.
Returned by `add_creature` / `with_creature`; not constructed directly.

Attributes: `creature_id`, `name`, `agent: Agent`, `graph_id`,
`listen_channels`, `send_channels`, `is_privileged`,
`parent_creature_id`, `is_running`, `status` (one of `"not_started"`,
`"error"`, `"busy"`, `"idle"`, `"stopped"`).

Turn drivers (delegate to the underlying `Agent`):

- `await creature.run(content, **kwargs) -> TurnResult`: same
  `timeout=` / `raise_on_error=` semantics as `Agent.run`.
- `creature.run_stream(content, **kwargs) -> AsyncIterator[TurnEvent]`
- `creature.attach() -> AgentEventStream`: non-destructive,
  multi-consumer observer; captures out-of-band turns (triggers,
  channel messages) too:

  ```python
  async with creature.attach() as stream:
      async for ev in stream:
          ...
  ```

- `await creature.chat(message) -> AsyncIterator[str]`: text-only
  sugar (inject + drain); prefer `run` / `run_stream` in new code.
- `await creature.inject_input(message, *, source="chat")`: push
  input without consuming output.

Lifecycle / introspection:

- `await creature.start()` / `await creature.stop()`: idempotent.
- `creature.get_status() -> dict`: model, provider, session_id,
  tools, subagents, pwd, channels, privilege.
- `creature.get_log_entries(last_n=20)` / `creature.get_log_text(last_n=10)`.

---

## Sessions

### `SessionReader`: read-only inspection

Module: `kohakuterrarium.session.reader` (re-exported as
`kohakuterrarium.SessionReader`). The one-stop read-only surface over a
`.kohakutr` file. Opens via `SessionStore.open_readonly`, so reading never
bumps `last_active` or flips `status`. Context-manager friendly.

- `SessionReader(path)`: raises `FileNotFoundError` if missing;
  `~` expands.
- Properties: `path: Path`, `meta: dict` (session_id, config_type /
  config_path, status, ...), `agents: list[str]`.
- `reader.events(agent=None) -> list[dict]`: the append-only event
  log; `None` concatenates every agent's events.
- `reader.conversation(agent=None) -> list[dict]`: the final
  conversation snapshot (OpenAI message dicts).
- `reader.channel_messages(channel) -> list[dict]`: one terrarium
  channel's history.
- `reader.turns(agent=None) -> list[TurnView]`: live-branch turns
  reassembled from the event log. `TurnView`: `index`, `user_text`,
  `assistant_text`, `tool_calls: list[dict]`, `source`, `ts`.
- `reader.search(query, *, mode="fts", k=10, agent=None) -> list[SearchResult]`:
  full-text (or vector, if indexed) search; un-indexed sessions
  return no hits.
- `reader.index() -> int`: build the FTS index for `search` ad hoc.
- `reader.close()`: or use `with SessionReader(...) as r:`.

```python
import kohakuterrarium as kt

with kt.SessionReader("runs/student-42.kohakutr") as r:
    print(r.meta["status"], r.agents)
    for turn in r.turns():
        print(turn.user_text, "->", turn.assistant_text[:80])
```

### Engine-owned persistence and resume

Persistence is an engine feature, with no manual `SessionStore` +
`init_meta` + `attach_session` ceremony:

- `Terrarium(session_dir="runs/")`: autosession for every graph.
- `engine.add_creature(..., session="runs/x.kohakutr")`: per-creature
  store (path | `True` | `False` | `SessionStore` | `None`).
- `engine.apply_recipe(..., session=...)`: one store for the recipe graph.
- `await Terrarium.resume(store_or_path, *, pwd=None, llm=None)`:
  fresh engine from a saved session.
- `await engine.adopt_session(store_or_path, *, pwd=None, llm=None) -> str`:
  resume into a running engine.
- `await engine.shutdown()` closes every store the engine minted
  (files no longer get stuck at `status: "running"`).

`SessionStore` (module `kohakuterrarium.session.store`, re-exported at
the root) remains the raw layer:

- `SessionStore(path)`: open read-write.
- `SessionStore.open_readonly(path)`: `close()` never mutates meta;
  use for every listing / preview consumer.
- `store.close(update_status=True)`: idempotent; `update_status=True`
  marks the session paused + bumps `last_active` (ignored on
  read-only stores).
- Event / conversation / state / channel / job accessors: see the
  [sessions guide](../guides/sessions.md).

---

## Packages

Module: `kohakuterrarium.packages`, a lazy facade (PEP 562): names
resolve on first attribute access so importing it stays cheap.

Install lifecycle:

- `ensure(spec, *, deps="auto") -> str`: idempotent install; returns
  the package name immediately if already installed (no version check,
  even for pinned specs). The right call at the top of a batch script.
- `install_package_spec(spec, editable=False, name_override=None, *, deps="auto") -> str`:
  `@name` / `@name@version` / `@source/name` resolve through the
  marketplace; git URLs and local paths fall through.
- `install_package(source, editable=False, name_override=None, ref=None, *, deps="auto") -> str`:
  git URL or local directory.
- `update_package(name, *, deps="auto") -> str`: `git pull --ff-only`
  in place; refuses pinned installs.
- `uninstall_package(name) -> bool`

`deps` is the Python-dependency policy: `"auto"` installs the
manifest's `python_dependencies` + `requirements.txt` via
`sys.executable -m pip`; `"never"` skips them. An unknown policy or a
failed install raises `PackageError`.

Reference resolution and layout:

- `is_package_ref(path) -> bool`: is this an `@pkg/...` path ref?
- `resolve_package_path(ref) -> Path` / `resolve_any_path(path) -> Path`
- `packages_dir() -> Path`: the active packages directory; honours
  `KT_CONFIG_DIR` (default `~/.kohakuterrarium/packages`).
- `get_package_root(name) -> Path | None` / `find_package_root_for_path(path) -> Path | None`
- `list_packages()` / `get_package_modules(...)`

Manifest-slot resolvers: `resolve_package_tool`, `resolve_package_io`,
`resolve_package_trigger`, `resolve_package_command`,
`resolve_package_user_command`, `resolve_package_prompt`,
`resolve_package_skills`, `get_package_framework_hints`.

Typed errors re-exported: `PackageError`, `PackageRefError`,
`PackageNotInstalledError`, `PackagePathNotFoundError`.

```python
from kohakuterrarium import packages

packages.ensure("@kt-biome")                  # idempotent install
path = packages.resolve_package_path("@kt-biome/creatures/swe")
for pkg in packages.list_packages():
    print(pkg["name"], pkg["version"])
```

---

## Compose

Module: `kohakuterrarium.compose`. Pipeline algebra over agents and
plain callables. Exports: `agent`, `factory`, `AgentRunnable`,
`AgentFactory`, `BaseRunnable`, `Runnable`, `Pure`, `pure`, `Sequence`,
`Product`, `Fallback`, `FailsWhen`, `Retry`, `Router`,
`PipelineIterator`.

Agent wrappers:

- `await agent(config, *, engine=None, pwd=None, llm=None) -> AgentRunnable`:
  persistent agent (conversation accumulates across calls); async
  context manager. `config` is an `AgentConfig`, a path, or an
  `@pkg/...` ref; `llm` follows the standard selector grammar. With
  `engine=None` a private `Terrarium` is created and torn down with the
  runnable; pass a shared engine to amortize startup (closing then only
  removes the creature).
- `factory(config, *, engine=None, pwd=None, llm=None) -> AgentFactory`:
  ephemeral. A fresh agent per call, destroyed after.

Operators (all return a `BaseRunnable`):

- `a >> b`: sequence; pipes output to input. Plain callables
  auto-wrap with `Pure`; a dict on the right becomes a `Router`.
- `a & b`: parallel product; returns a tuple. On the first failure
  the surviving siblings are **cancelled and awaited** before the
  exception propagates.
- `a | b`: fallback; on exception run `b` with the original input.
  When the fallback also fails, the primary's exception is chained as
  `__cause__`.
- `a * N`: retry up to `N` attempts (immediate).
- `.retry(max_attempts, *, backoff=0.0, max_backoff=30.0)`: retry
  with exponential backoff (sleep doubles per attempt, capped).
- `.iterate(initial_input)`: async iterator feeding output back as
  the next input (`it.feed(value)` overrides the next input).
- `.map(fn)` / `.contramap(fn)`: post- / pre-transform.
- `.fails_when(predicate)`: raise `ValueError` when the predicate
  matches the output (turns a bad success into a fallback trigger).

```python
from kohakuterrarium.compose import agent, factory, pure

async with await agent("@kt-biome/creatures/swe", llm="fast") as swe:
    pipeline = swe >> pure(str.strip) >> (lambda t: f"Review:\n{t}")
    result = await (pipeline.retry(3, backoff=1.0))("Implement the feature")
```

---

## Validate

Module: `kohakuterrarium.validate` (re-exported as
`kohakuterrarium.validate`). Pre-flight checks that raise typed errors
on the first problem; `kt doctor` is the CLI wrapper.

- `validate.config(path) -> AgentConfig`: agent config folder /
  `@pkg` ref parses with full strictness.
- `validate.terrarium_config(path) -> TerrariumConfig`: terrarium
  recipe parses.
- `validate.llm(selector=None) -> str`: selector resolves AND the
  provider constructs (credential check, no network); returns the
  canonical `provider/name[@variations]` identifier. Raises
  `LLMNotConfiguredError` / `ValueError`.
- `validate.creature(path, *, llm_binding=None) -> ValidationReport`:
  full dry-run build (`strict=True`, headless IO, never started).
  `ValidationReport`: `name`, `config_path`, `model_identifier`,
  `tools`, `plugins`, `subagents`.
- `await validate.ping(selector_or_provider=None, *, timeout=30.0) -> str`:
  the only validator that touches the network. It makes one minimal
  LLM round-trip and returns the reply text.

```python
import kohakuterrarium as kt

kt.validate.config("./scoring-agent")
kt.validate.llm("openai/gpt-5@reasoning=high")
report = kt.validate.creature("./scoring-agent")
await kt.validate.ping("openai/gpt-5")
```

---

## Testing

Module: `kohakuterrarium.testing`.

- `ScriptedLLM(script: list[ScriptEntry] | list[str] | None = None)`
  (module `kohakuterrarium.testing.llm`): a deterministic provider.
  **Prefer direct injection**: every construction entry point accepts
  the instance via `llm=`: `Agent.build(cfg, llm=ScriptedLLM([...]))`,
  `engine.add_creature(path, llm=...)`, `compose.agent(cfg, llm=...)`.
  Assertion surface: `call_count`, `call_log`.
  `ScriptEntry(response, match=None, delay_per_chunk=0, chunk_size=10)`.
- `OutputRecorder` (`testing.output`): captures `chunks`, `writes`,
  `activities`, `all_text`.
- `EventRecorder` (`testing.events`): `record`, `get_all`,
  `get_by_type`, `clear`.
- `TestAgentBuilder` (`testing.agent`): fluent harness for unit-style
  agent tests (`with_llm_script`, `with_builtin_tools`, `build()`).

The monkeypatch seam at `bootstrap.llm.create_llm_provider` +
`bootstrap.agent_init.create_llm_provider` remains only for paths where
the framework constructs agents internally (config files, resume,
recipes).

```python
import kohakuterrarium as kt
from kohakuterrarium.testing.llm import ScriptedLLM

agent = await kt.Agent.build(cfg, llm=ScriptedLLM(["Hello!"]), io="headless")
await agent.start()
result = await agent.run("hi")
assert result.text == "Hello!"
await agent.stop()
```

---

## See also

- Guides: [programmatic usage](../guides/programmatic-usage.md),
  [composition](../guides/composition.md),
  [sessions](../guides/sessions.md), [packages](../guides/packages.md),
  [studio](../guides/studio.md), [custom modules](../guides/custom-modules.md),
  [plugins](../guides/plugins.md).
- Tutorial: [first Python embedding](../tutorials/first-python-embedding.md).
- Reference: [cli](cli.md), [http](http.md),
  [configuration](configuration.md), [builtins](builtins.md),
  [plugin-hooks](plugin-hooks.md).
- Runnable scripts: [`examples/code/`](../../../examples/code/);
  `batch_grading.py` is the canonical batch pattern.
