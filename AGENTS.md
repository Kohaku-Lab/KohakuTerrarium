# AGENTS.md: Building Agents With (and On) KohakuTerrarium

A one-file brief for coding agents. Two jobs are covered:

1. **Building an agent**: config files, custom modules, terrarium recipes.
2. **Using agents from Python**: the programmatic API, end to end.

A short final section covers contributing to the framework itself (the
authoritative rules live in `CLAUDE.md` and `CONTRIBUTING.md`).

---

## 0. Orientation in 30 seconds

KohakuTerrarium is a framework for building agents, not another agent.

- **Creature** = the agent unit: controller (LLM loop) + tools + triggers +
  sub-agents + plugins + memory + I/O. Defined by a config folder, or built
  in Python.
- **Terrarium** = the runtime engine hosting every running creature in the
  process. Graphs, channels, lifecycle, sessions. No LLM of its own.
- **Studio** = the management layer above the engine (catalog, sessions,
  persistence) that the web/desktop/HTTP surfaces delegate to.
- `@<package>/<path>` references resolve into installed packages
  (`~/.kohakuterrarium/packages/`); they work everywhere a config path does.

Setup + sanity check:

```bash
pip install kohakuterrarium
kt login codex                      # or: kt config key set <provider>
kt install @kt-biome                # official creature pack
kt doctor                           # verifies provider, profiles, configs
kt run @kt-biome/creatures/swe      # interactive run
kt resume --last                    # pick a previous session back up
```

---

## Part 1: Building an agent

### 1.1 Anatomy of a creature config

A creature is a folder:

```
my-agent/
├── config.yaml        # the agent definition (config.yml/.json/.toml also work)
├── system.md          # system prompt (or inline `system_prompt:` in YAML)
└── prompts/, custom/  # optional: extra prompt files, custom module code
```

A representative `config.yaml`:

```yaml
name: my_agent
llm: default                # LLM profile/preset name (alias: llm_profile)

system_prompt_file: system.md

input:
  type: cli                 # cli / tui / none (trigger-driven agents use none)
output:
  type: stdout

tools:
  - name: read
    type: builtin
  - name: bash
    type: builtin
    config: { timeout: 120 }
  - name: my_tool           # custom tool, loaded from your module
    type: custom
    module: custom/my_tool.py
    class: MyTool

triggers:
  - type: timer
    interval: 300
    prompt: "Periodic check: anything new in the watched folder?"

subagents:
  - name: researcher
    type: builtin           # builtin sub-agents: explore, plan, critic, research, ...

plugins:
  - name: budget
    options: { max_tool_calls: 200 }

mcp_servers:                # optional MCP servers (stdio / streamable HTTP)
  - name: fs
    transport: stdio
    command: mcp-server-filesystem
```

Rules that matter:

- **Never put the tool list, tool-call syntax, or full tool docs in
  `system.md`.** The framework aggregates the tool list and framework hints
  into the prompt automatically; full docs load on demand via `##info##`.
- `system.md` is for personality / role / agent-specific guidelines only.
- The controller is an **orchestrator**: short outputs, tool calls, dispatch.
  Long user-facing content should come from output sub-agents.
- Any config path field accepts `@pkg/...` references, including
  `base_config:` for inheritance:

```yaml
base_config: "@kt-biome/creatures/swe"   # inherit, then override below
name: my_swe_variant
```

Run it: `kt run ./my-agent` (or `kt run @my-pack/creatures/my-agent` once
packaged).

### 1.2 Custom tools

Two ways. For a quick function-shaped tool (programmatic injection only),
decorate a plain function. The schema comes from the type hints and the
description from the docstring:

```python
import kohakuterrarium as kt

@kt.tool
def check_stock(item: str, warehouse: str = "main") -> str:
    """Look up how many units of an item are in stock."""
    return lookup(item, warehouse)

agent = await kt.Agent.build("./my-agent", tools=[check_stock])
```

For a config-loadable tool (shareable, packageable), subclass the protocol:

```python
# my-agent/custom/my_tool.py
from kohakuterrarium.modules.tool.base import BaseTool, ToolResult


class MyTool(BaseTool):
    @property
    def tool_name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "One-line description shown in the aggregated prompt."

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }

    async def execute(self, args: dict, context=None) -> ToolResult:
        try:
            return ToolResult(output=do_work(args["query"]))
        except Exception as e:
            return ToolResult(output="", error=str(e))
```

Declare it under `tools:` with `type: custom` + `module:` + `class:` (see
1.1). Execution is async and parallel by design: tools start the moment the
controller emits the call, never sequentially queued.

### 1.3 Custom input / output / trigger modules

Same pattern: subclass the protocol in `modules/<kind>/base.py`, point config
at your module. Sketches:

```python
# Input: push external events into the agent
from kohakuterrarium.modules.input.base import BaseInputModule

class WebhookInput(BaseInputModule):
    async def get_input(self):        # await the next external event;
        ...                           # return a TriggerEvent (or None)

# Output: deliver the agent's text somewhere
from kohakuterrarium.modules.output.base import BaseOutputModule

class DiscordOutput(BaseOutputModule):
    async def write(self, content: str): ...
    async def write_stream(self, chunk: str): ...

# Trigger: wake the agent automatically
from kohakuterrarium.modules.trigger.base import BaseTrigger

class FileWatchTrigger(BaseTrigger):
    async def wait_for_trigger(self):  # block until the condition fires;
        ...                            # return a TriggerEvent (or None)
```

Working examples: `examples/agent-apps/discord_bot/custom/` (full Discord
I/O + trigger set), `examples/agent-apps/monitor_agent/custom/alert_output.py`.
The guide: `docs/en/guides/custom-modules.md`.

### 1.4 Plugins (cross-cutting behavior)

Plugins wrap the framework with pre/post hooks; sandboxing, budgets, and
permission gating all ship this way. Don't add framework features when a
plugin can do it.

```python
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError


class ToolGuard(BasePlugin):
    name = "tool_guard"

    def __init__(self, blocked: list[str] | None = None):
        self.blocked = set(blocked or [])

    async def pre_tool_execute(self, tool_name, args, context=None):
        if tool_name in self.blocked:
            raise PluginBlockError(f"{tool_name} is blocked by policy")
        return None        # None = unchanged; or return transformed args

    async def post_llm_call(self, response, context=None):
        return None        # may transform the response
```

Hook inventory: `pre/post_tool_execute`, `pre/post_llm_call`,
`pre/post_subagent_run`, plus fire-and-forget callbacks (`on_load`,
`on_agent_start/stop`, `on_event`, `on_interrupt`, `on_compact_end`, ...).
The constructor contract is `cls(**options)`: your `options:` dict from
config arrives as keyword arguments. `PluginContext` exposes `agent_name`,
`working_dir`, `session_id`, `model`, `switch_model()`, `inject_event()`, and
session-persisted `get_state()` / `set_state()`.

Inject programmatically (`Agent.build(plugins=[ToolGuard(...)])`,
`agent.add_plugin(...)`, which works after start too) or declare in config with
`module:`/`class:`/`options:`. Eight worked examples: `examples/plugins/`.

### 1.5 Terrarium recipes (multi-agent)

A terrarium is a YAML recipe wiring creatures through broadcast channels:

```yaml
terrarium:
  name: review_team

  root:                                    # promotes ONE privileged node and
    base_config: "@kt-biome/creatures/root"   # wires it to observe everything

  creatures:
    - name: developer
      config: ./creatures/developer/
      channels:
        listen: [tasks, feedback]
        can_send: [review]
    - name: reviewer
      config: ./creatures/reviewer/
      channels:
        listen: [review]
        can_send: [feedback, results]

  channels:
    tasks:    { description: "Incoming work" }
    review:   { description: "Code for review" }
    feedback: { description: "Review feedback" }
    results:  { description: "Final results" }
```

Facts that prevent design mistakes:

- **All channels are broadcast**: every listener receives every send. There
  is no queue kind; don't write `type:` on a channel.
- **Graph = connected component.** Connecting creatures across graphs merges
  them (environments union, session stores merge); removing a bridge
  creature or channel auto-splits. This bookkeeping is load-bearing.
- **Privileged node** (the `root:` creature, or `privileged: true`) gets the
  `group_*` tools: spawn/remove creatures, draw/delete channels, start/stop
  members. These are the runtime graph editor. Workers it spawns are NOT privileged.
- Sub-agents are *vertical* (private, inside one creature); channels are
  *horizontal* (peer creatures). Never mix the two levels.

Run: `kt terrarium run ./my-team/` or programmatically
`await Terrarium.from_recipe("./my-team/")`.

### 1.6 Packaging and sharing

A package is a repo/folder with a `kohaku.yaml` manifest and conventional
dirs (`creatures/`, `terrariums/`), optionally declaring `tools:`,
`plugins:`, `io:`, `triggers:`, `skills:`, `commands:`, `user_commands:`,
`prompts:`, `llm_presets:`, and `python_dependencies:`.

```bash
kt install ./my-pack -e        # editable while developing
kt install @name               # marketplace (TerrariumMarket)
kt install <git-url>           # any repo
kt install <src> --no-deps     # skip its python_dependencies
```

Everything becomes addressable as `@my-pack/...`. See
`docs/en/guides/packages.md`.

---

## Part 2: Using agents from Python

Everything below imports from the package root unless noted:

```python
from kohakuterrarium import (
    Agent, Terrarium, Creature, Studio,
    TurnResult, TextChunk, Activity, TurnEnded,
    SessionReader, SessionStore,
    tool, errors, validate, packages,
)
```

Errors are typed and strict by default: a wrong LLM name, a missing config,
an unknown tool. These **raise** (`errors.ConfigError`,
`errors.LLMNotConfiguredError`, `errors.PackageNotInstalledError`,
`errors.TurnError`, ...) instead of degrading silently. Interactive
frontends pass `strict=False`; your scripts shouldn't.

### 2.1 One agent, typed turns

```python
agent = await Agent.build(
    "@kt-biome/creatures/general",   # path, @ref, or AgentConfig
    llm="default",                   # profile/preset name, LLMProfile, or a
                                     #   provider INSTANCE (e.g. ScriptedLLM)
    pwd="/work/dir",                 # working dir; no global os.chdir
    io="headless",                   # "config" | "none" | "headless"
    tools=[my_tool],                 # @kt.tool adapters / Tool instances
    plugins=[MyPlugin()],            # plugin instances, enabled
)
await agent.start()

result = await agent.run("Do the thing.", timeout=600)
# TurnResult: .status ("ok"|"error"|"timeout"|"interrupted"), .ok, .text,
#             .error, .tool_calls, .activities, .usage, .duration_s
# A failed turn RAISES TurnError / TurnTimeoutError by default;
# pass raise_on_error=False to branch on result.status yourself.
# timeout= actually interrupts the turn, so no orphan task burns tokens.

async for event in agent.run_stream("Stream this one."):
    match event:
        case TextChunk(text=t):       print(t, end="")
        case Activity(kind=k):        ...   # tool_start/tool_done/...
        case TurnEnded(result=r):     print(r.status)

await agent.stop()
# Runtime additions (prompt refreshes live):
agent.add_tool(my_tool); await agent.add_plugin(p); agent.add_subagent(cfg)
```

(`agent.run_forever()` is the autonomous main loop used by `kt run`; you
almost never call it when embedding.)

### 2.2 The engine: many creatures, sessions, channels

```python
async with Terrarium(session_dir="runs/") as engine:   # autosession: every
    worker = await engine.add_creature(                # creature persists
        "@kt-biome/creatures/swe",
        llm="fast",                       # raises at add time if unknown
        pwd=folder,                       # per-creature cwd
        session=folder / "run.kohakutr",  # or True / False / a SessionStore;
                                          #   overrides the engine default
        io="headless",                    # batch runs: no console interleave
        tools=[check_stock],              # same injection as Agent.build
        start=True,
    )
    result = await worker.run(PROMPT, timeout=1800, raise_on_error=False)
    await engine.remove_creature(worker)
```

- `Creature.run / run_stream` mirror the Agent API; `.chat(msg)` remains as
  plain-text streaming sugar; `creature.attach()` opens a non-destructive
  typed event stream (`async with ... as stream`) that also sees
  trigger-initiated turns.
- Topology: `await engine.connect(a, b, channel="x")` (merges graphs),
  `await engine.disconnect(a, b, channel="x")` (may split),
  `await engine.add_channel(graph, "tasks")`,
  `engine.channel(graph, "tasks")` → live channel handle
  (`await ch.send(ChannelMessage(sender="user", content="..."))`),
  `engine.environment(graph)`, `engine.list_graphs()`, `engine.subscribe()`
  for typed `EngineEvent`s.
- Recipes: `engine = await Terrarium.from_recipe("@pack/terrariums/team")`.
- The batch pattern (N folders × one engine, bounded concurrency):
  `examples/code/batch_grading.py`. It is about 50 lines; start there.

### 2.3 Sessions: resume and read back

```python
# Resume a saved run (topology rebuilt from the recipe/config in meta):
engine = await Terrarium.resume("runs/run.kohakutr", llm="default")
# or into a running engine:  await engine.adopt_session(path)

# Read a finished run offline (read-only, no engine needed):
with SessionReader("runs/run.kohakutr") as r:
    r.meta, r.agents
    for turn in r.turns():          # live branch only
        turn.user_text, turn.assistant_text, turn.tool_calls
    r.events(); r.conversation(); r.channel_messages("tasks")
    r.search("auth bug")            # FTS over the recorded events
```

CLI equivalents: `kt resume`, `kt search`, `kt embedding`.

### 2.4 Packages from Python

```python
from kohakuterrarium import packages

packages.ensure("@kt-biome")                 # idempotent install
path = packages.resolve_package_path("@kt-biome/creatures/swe")
packages.list_packages()
packages.install_package_spec("@pack@v1.2.0", deps="never")
```

### 2.5 Composition algebra

```python
from kohakuterrarium.compose import agent, factory, pure

swe = await agent("@kt-biome/creatures/swe", engine=shared, llm="default")
pipeline = swe >> pure(extract_code) >> reviewer
result = await (pipeline | fallback).retry(3, backoff=2.0)(task)
```

`>>` sequence · `&` parallel (first failure cancels siblings) · `|` fallback
(double failure chains the original as `__cause__`) · `* N` retry ·
`.retry(n, backoff=)` · `.iterate(x)`. `factory(...)` builds a fresh
creature per call; `agent(...)` is persistent. Pass `engine=` to share one
engine; closing a runnable then removes only its creature.

### 2.6 Validation and testing

```python
from kohakuterrarium import validate
report = validate.creature("@pack/creatures/x")   # config + llm + tools
validate.llm("default"); validate.ping("default") # resolve / live-call check
```

For deterministic tests, inject the scripted provider directly, no
monkeypatching needed:

```python
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry
agent = await Agent.build(cfg, llm=ScriptedLLM(["reply 1", "reply 2"]))
```

### 2.7 Drives (durable commitments)

A **Drive** is an optional Terrarium runtime resource (beside session /
channel): a durable, assignable commitment the engine delivers as
ordinary events. Opt-in — a Terrarium with no `drive_config` has no
Drive machinery. Delivery is **at least once**, logically deduped; there
is **no exactly-once**, and recovery events warn that a prior attempt
*may* have run side effects. The engine never reasons about a Drive.

Explicit Python (no Studio): enable the runtime, then create/administer
through the service.

```python
from kohakuterrarium import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig, default_registrations
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest, DrivePatch

async with Terrarium(
    session_dir="runs/",                          # Drives persist to a sidecar:
    drive_config=DriveRuntimeConfig(enabled=True),#   runs/<name>.kohakutr.drives
    drive_registrations=default_registrations(),  # enabled=True + no regs -> error
) as engine:
    w = await engine.add_creature("@kt-biome/creatures/swe", start=True)
    svc = LocalTerrariumService(engine)
    who = ActorRef("service", "deploy-bot")       # "<kind>:<identity>"
    view = await svc.create_drive(
        CreateDriveRequest(
            kind="generic", title="Watch the deploy",
            scope_type="graph", scope_id=w.graph_id,
            owner=who, owner_scope="service", created_by=who,
            spec={"instruction": "Monitor until stable"},
            assignee_creature_id=w.creature_id,
        ),
        graph_id=w.graph_id, actor=who, operator=True,  # graph-scoped create = graph authority
    )
    await svc.update_drive(view.record.drive_id, DrivePatch(priority=5),
                           expected_revision=view.record.revision, actor=who)  # CAS
    # completion is a *proposal*, never a direct terminal write:
    await svc.propose_drive_transition(view.record.drive_id, DriveStatus.COMPLETED,
                                       evidence={"ok": True},
                                       expected_revision=view.record.revision, actor=who)
```

- Every creature in a Drive-enabled Terrarium gets 5 self-service tools
  (`drive_create` / `drive_status` / `drive_update` / `drive_report` /
  `drive_transition`); a privileged node also gets `group_drive`.
- Managed surfaces resolve `drive_config` from Studio's
  `drive-settings.yaml` (`runtime.enabled` + per-registration `enabled`;
  save and apply are separate: `applied_live` / `restart_required` /
  `rejected`). Recipes carry **no** Drive fields.
- **`/goal` is not a framework feature.** It is an optional **built-in**
  composition (disabled by default): a `goal` Drive registration
  (`kohakuterrarium.terrarium.drive.goal`, enabled in Drive settings)
  *and* a `GoalPlugin` (`kohakuterrarium.builtins.plugins.goal`, enabled
  in the plugin panel / a creature `plugins:` entry) — two independent
  toggles. Budgets pause a goal, never complete it; `user_confirm`
  completion is user-authoritative.
- Full model: `docs/en/concepts/multi-agent/drive.md`; APIs:
  `docs/en/guides/programmatic-drive.md`; goal: `docs/en/guides/goal.md`.

---

## Part 3: Working on the framework itself

`CLAUDE.md` (architecture + conventions) and `CONTRIBUTING.md` (policy +
pre-flight) are authoritative. The compressed version:

- **Style**: Python 3.10+; modern type hints (`list`, `X | None`, never
  `Optional`/`Union`); full asyncio; no `print()` in library code (structured
  logging, `[HH:MM:SS] [module] [LEVEL]`); no imports inside functions except
  allowlisted lazy ones (`scripts/dep_graph_allowlist.json` matches
  file+function+target).
- **Size**: max 600 lines/file (hard 1000; `tests/unit/test_file_sizes.py`).
- **Frontend**: Vue 3 + Vite, JavaScript only. `npm run format:check` +
  `npm run build` before committing.
- **Tests, three tiers** (`tests/README.md` is the spec): unit = one source
  file → one test file; integration = one core-lib folder → one test-class
  whose methods are *complete single-function workflows*; e2e = a handful of
  fat user-journey tests (NOT run in CI; run locally for multi-node /
  Studio / serving changes). Behavior asserts, real collaborators; the only
  seam is the LLM (`ScriptedLLM`, preferably via direct `llm=` injection).
  To raise integration/e2e coverage, fatten existing workflow functions
  instead of adding new test functions.
- **The audit loop is required** for any multi-file change: implement →
  write negative-case tests → run the affected tiers + lint (`black src/
  tests/`, `ruff check src/ tests/`) → audit the diff (clear bugs, integrity
  bugs, behavior bugs) → if a bug slipped past the tests, FIX THE TEST FIRST
  (prove it catches the bug), then the bug → loop until clean.
- **Invariants that must not break**: auto-merge/auto-split of graphs;
  broadcast-only channels; auth lives entirely in `api/auth/` (nothing below
  `api/` may import it); `launcher/` imports nothing from the rest of the
  package; the controller never blocks on tool execution; "privileged node"
  is the runtime concept (`root:` is just recipe syntax).
- **Never edit** `src/kohakuterrarium/web_dist/` by hand; it's Vite build
  output.
