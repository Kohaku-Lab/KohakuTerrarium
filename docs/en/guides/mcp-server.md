# MCP tool server

The standalone server exposes KT tools to an external MCP client. With no
delegation targets configured it creates no Creature and starts no local model.
Optional delegation targets run locally configured Creatures and standalone
task subagents. `kt mcp-serve` manages a separate
background process per workspace, an authenticated Streamable HTTP endpoint,
and optionally its preconfigured ngrok tunnel.

## Configure once, then start and stop

Install this checkout (or put its `src` directory on `PYTHONPATH` and use
`python -m kohakuterrarium` in place of `kt`). The tested SDK dependency is
`mcp>=1.28.1,<2`; the existing MCP client remains on the same major.
Configure an ngrok account and fixed HTTPS domain first, then from the desired
working directory run:

```powershell
kt mcp-serve setup
kt mcp-serve start
kt mcp-serve status
kt mcp-serve stop
kt mcp-serve start
```

`setup` opens a wizard in an interactive terminal. It selects the ingress mode,
public HTTPS origin, local port (default 8765), optional tool configuration and,
in managed mode, ngrok executable/configuration. It shows a change summary and
asks before saving. Cancellation or EOF leaves the previous record untouched.
Setup does not start anything, install ngrok, register an account, allocate a
domain, or test public connectivity. It validates the origin and local
dependencies before saving: tools configuration is parsed, ngrok must be found,
and an explicit ngrok configuration must be a readable file. Ngrok validates
its own file contents when it starts. A busy local port is checked only at startup.

In scripts, use explicit options:

```powershell
kt mcp-serve setup --non-interactive --mode ngrok --origin https://your-fixed-domain.example
kt mcp-serve setup --non-interactive --mode external --origin https://your-domain.example
```

`--ngrok-bin`, `--ngrok-config`, `--port` and `--config` belong to **setup**.
The start command takes lifecycle options such as `--wait`, not configuration
options. A start without saved configuration tells you to run setup first.
Non-TTY input, `--non-interactive`, or `--json` disables all prompts; missing
required input produces a nonzero exit code. Interactive arguments prefill the
wizard. JSON output never contains the MCP secret.

The first setup saves the canonical workspace, public origin, random secret,
local port, ingress mode and optional tool-config path under
`~/.kohakuterrarium/mcp-serve/<workspace-key>/connection.json`. Subsequent starts
reuse them. A ready human-readable start prints the complete URL for deliberate
copying; `kt mcp-serve url` displays it again. Keep that URL private. Setup
summaries, status, and JSON lifecycle output omit the secret.

Pass `--workspace PATH` on any command to manage another directory. Workspace
identity resolves symlinks and Windows case. Moving a directory creates a
different identity; the old URL is never silently rebound to it. Corrupt saved
connection records fail closed and must be restored explicitly. This version
does not provide workspace migration or secret-rotation commands.

The background supervisor holds an OS file lock per workspace. Concurrent or
repeated starts reuse the existing instance; a stale PID alone never establishes
ownership. Stop requests carry the current run identity in a private local file,
not in a remotely exposed management route. Old stop requests cannot stop a new
run. Stop cancels owned jobs, closes the local listener and reaps owned ngrok;
it preserves connection configuration and never deletes cloud resources.

`start` waits up to 30 seconds for an authenticated public initialization to
identify this exact instance (`--wait 1..120` overrides it). Exit code 0 means
public readiness was verified. Exit code 1 can mean the local service is running
but public connectivity is not ready: inspect `status`, which separates
`local_ready`, `public_ready`, `tunnel_state`, and the last public check time.
An outage never causes a random-domain fallback or a new tool instance. Managed
ngrok exits retry with bounded 1–30 second backoff; an online agent handles its
own network reconnects. The supervisor rechecks public identity periodically
without downloading job contents.

For an independently maintained stable entry point, use:

```powershell
kt mcp-serve setup --non-interactive --mode external --origin https://your-domain.example
kt mcp-serve start
```

Point that entry at the selected loopback port. KT neither starts nor stops an
external tunnel. A public host still needs an HTTPS reverse proxy: KT listens
on loopback and does not terminate TLS itself. In managed mode only, inherited
HTTP proxy environment variables are removed from the ngrok child, matching the tested reference setup;
ngrok's own configuration is retained. The system proxy is not changed.

Supervisor and tunnel diagnostics are in `server.log` and `tunnel.log` beside
the connection record. An ownership pipe lets a tunnel guardian reap its ngrok
child if the supervisor exits unexpectedly. The supervisor itself is not an
OS boot service: after its crash or a computer restart, run `start` again.
Transient Windows readers can delay atomic status publication. Such diagnostic
write failures do not stop tool execution; status becomes `unresponsive` if its
heartbeat stays stale, and a held ownership lock prevents a duplicate launch.

## Editing saved settings while running

Run setup again to edit the saved configuration. Omitted fields keep their
previous values; new configurations use defaults. Use `--clear-config` or
`--clear-ngrok-config` to restore the respective default. In the wizard, an
empty answer keeps the displayed value and `-` clears an optional file path.
Switching to external mode clears saved ngrok-specific settings. Ngrok flags
are rejected when external mode is selected. Changing origin keeps the secret
and warns that the ChatGPT connection URL must be updated.

Setup may save while the service is running. The running instance and all of
its tunnel retries use a private `active.json` snapshot tied to its run ID.
They never adopt pending settings in the middle of a job. The next instance
uses the saved settings. Status shows `active`, `configured`, `pending_changes`
and `restart_required`, without credentials. Public readiness always describes
the running instance. Repeating start reuses that instance and reports pending
settings; it does not silently restart it.

```powershell
kt mcp-serve setup --non-interactive --origin https://new-domain.example
kt mcp-serve status
kt mcp-serve url                 # running URL; saved URL when stopped
kt mcp-serve url --configured    # explicitly copy the next-start URL
kt mcp-serve stop
kt mcp-serve start
```

The wizard checks that the saved record has not changed since it opened. A
conflicting save fails and asks you to start setup again; it never overwrites
another terminal's changes. Validation precedes one atomic commit. Failed
startup preserves the new settings and reports the failure without rollback.

The snapshot freezes **setup-managed fields**, not external file contents.
Editing the referenced tool configuration takes effect at the next tool-process
start. Editing an ngrok configuration file may affect its next tunnel restart.
Status does not detect or promise to freeze those file contents.

Existing connection records remain readable without reconfiguration. A process
started by the older CLI has no active snapshot: stop and start it once before
using live setup editing or retrieving its running URL with the new CLI.
No new identity or secret is generated by that upgrade.

## Configuration

Without `--config`, the nine tools below use their defaults. To customize them,
pass `--config` to setup with a dedicated YAML or JSON file:

```yaml
name: KT tools
workspace: ./work
pwd_guard: warn
tools:
  - name: read
  - name: write
  - name: edit
  - name: multi_edit
  - name: glob
  - name: grep
  - name: tree
  - name: bash
    config:
      timeout: 60
      max_output: 262144
  - name: python
    config:
      timeout: 60
plugins: []
```

## Delegate to a local Creature or subagent

Register targets in the same configuration file used by `setup --config`:

```yaml
workspace: ./work
delegation:
  coder:
    kind: creature
    config: "@kt-biome/creatures/swe"
    description: "Implement and verify changes in this workspace"
  reviewer:
    kind: subagent
    config: ./reviewer.yaml
    description: "Review a concrete change and report findings"
```

The catalog is local configuration. Clients select an alias; they cannot submit
configuration paths, inline definitions, model overrides or tool overrides.
Relative references resolve against the MCP configuration file. Installed
`@package/...` references use normal KT package resolution. Definitions load when
an instance is created; a bad definition fails that job rather than silently
dropping configured capabilities. Catalog changes require a server restart;
editing a referenced definition affects new instances, not existing ones.

Creature definitions use the ordinary KT configuration format. A standalone
subagent needs no parent Creature. Its YAML/JSON file uses SubAgentConfig fields,
with `llm` selecting the local KT profile and `tools` accepting names or the
ordinary tool configuration entries:

```yaml
name: reviewer
llm: default
system_prompt: "Review the requested change. Report concrete findings."
tools:
  - name: read
  - name: glob
  - name: grep
  - name: bash
    config:
      timeout: 60
can_modify: false
max_turns: 30
timeout: 600
plugins: []
```

Custom/package tools and plugins resolve relative to the definition through KT's
existing factories. The subagent model may also be selected using `model` when
`llm` is omitted. There is no parent-model inheritance. Interactive subagents
are not supported by this first release. Limits and sandbox policy belong in
the target configuration and its plugins.

Configure local model credentials/profiles through the ordinary KT commands,
then save and activate this server configuration:

```bash
kt mcp-serve setup --config ./mcp.yaml
kt mcp-serve stop
kt mcp-serve start
```

Refresh the client's tool list after restarting. Registered delegation enables
six additional tools:

| Tool | Use |
| --- | --- |
| `delegation_targets` | Discover registered aliases and descriptions without starting a model |
| `delegate` | Submit `target` and `prompt`; optionally continue a Creature `session_id` |
| `delegation_send` | Supplement an active `job_id` using KT's existing input semantics |
| `delegation_sessions` | List server-owned sessions, busy state and current delegation job |
| `delegation_history` | Page session activity or the current public conversation snapshot |
| `delegation_close` | Stop and close a server-owned session, retaining readable history |

A typical client flow is:

1. Call `delegation_targets` and choose an alias.
2. Call `delegate(target="coder", prompt="Investigate the failing test")`.
   Save both returned IDs: `job_id` identifies this execution, `session_id`
   identifies its conversation. Submission returns before model execution.
3. Use the existing `job_status` / `job_wait` tools to retrieve the result.
   Waiting has a maximum of 60 seconds per call; timeout or disconnect does not
   cancel execution. Delegation jobs are already asynchronous and do not need
   `job_promote`.
4. Inspect `delegation_history(session_id=..., view="events")` for activity,
   or `view="conversation"` for public messages and full retained tool results.
   Use `cursor` and `limit` (1–200). Activity retains the latest 2,000 events and
   reports eviction with `truncated` / `earliest_cursor`. Conversation pagination
   reads a live snapshot; compaction or ongoing turns may change its offsets.
5. Continue with `delegate(target="coder", session_id=..., prompt="Apply the fix")`,
   or omit `session_id` to start an independent conversation.
6. Use `job_cancel` to stop a current delegation, or `delegation_close` when the
   conversation is no longer needed.

Do not repeat a submission merely because its HTTP response was lost. List jobs
and sessions first: all authenticated clients share this server's ownership and
history. The job history has the same bounded retention as direct tool jobs.

### Runtime and cancellation semantics

Delegates inherit the MCP workspace as their working directory. Different
conversations share its files; no worktree or filesystem isolation is created.
MCP adds no extra path restriction. Tools, plugins and autonomous triggers follow
the target's configuration, independently of the direct MCP tool allowlist and
its policies. Creature execution uses KT headless I/O while preserving named
outputs and triggers. Registering a target does not immediately instantiate it.

Each Creature session accepts one active delegated turn. Busy sessions reject
new work, including while running an autonomous turn; that busy state may have
no MCP delegation job ID. Session listing reads the live Creature state, including
idle, paused and stopped, rather than inferring it from the target catalog.
Use history to inspect its activity. A running-input supplement
is not another queued delegation and is delivered at the normal KT boundary.
KT may promote foreground tools to the background while handling that input;
the original delegated turn can then finish. Cancelling that completed job is
a no-op; use `delegation_close` to stop any work remaining in its session.
Finishing a Creature turn does not imply its background work has finished.
Autonomous activity is session history, not the result of an unrelated job.

`job_cancel` uses KT stop for a Creature: it stops that instance, its triggers,
and all its KT-managed tools/subagents, including background work left by earlier
turns. It waits for cleanup and preserves conversation history. Explicitly
continuing that same server-owned session rebuilds the runtime through KT's
existing persistence/resume flow; cancellation never automatically restarts it.
Cancelling a standalone subagent stops only its own task scope and waits for its
normal cancellation chain. Neither operation rolls back file changes or promises
to reclaim arbitrary detached operating-system processes.

Creature sessions stay alive until explicitly closed or server shutdown. A closed
session cannot be continued. Subagents are one-shot: they accept supplements while
running, but completed subagents cannot continue the same conversation. Starting
another task creates a fresh subagent.

The server does not reconnect to other KT processes, import arbitrary saved
conversations, or automatically recover jobs/sessions after its own restart.
Creature persistence uses normal `.kohakutr` files under
`~/.kohakuterrarium/mcp-serve/sessions/<instance-id>/`; MCP handles remain scoped
to the server lifetime. Terrarium recipes are not delegation targets: team task
correlation, completion and cancellation require a separate collaboration
protocol. Internal use of Terrarium to host Creatures does not provide that
team-level contract.

## Direct tool configuration details

`workspace` must exist; a relative path resolves against the config file's
directory. Omitting `tools` enables the nine tools above. Tools must have
unique names and `type: builtin` (the default). `max_output` and each tool's
declared runtime options are accepted. `timeout` applies to bash/Python;
`env` applies to bash. Per-tool `working_dir` is rejected because the shared
execution context supplies the directory. At the top level, controller notification settings,
LLM profiles, prompts, triggers, compact and AgentConfig inheritance are
rejected rather than silently ignored.

The directory is the default execution location, **not a sandbox**. Existing
KT read-before-write, stale-read checks, path guard and execution policies
apply. With `pwd_guard: warn`, an initial outside-directory file operation
returns a warning; intentionally retrying follows KT's existing rule.

Execution plugins use the usual `name`, `type`, `module`, `class` and `options`
entries. Only execution-side plugins are supported: load/unload, dispatch,
pre/post execution, runtime services and promotion. Configured load failures
abort startup. Plugins overriding LLM, Agent lifecycle, event, compact, prompt,
visibility, command or termination hooks are rejected. Their PluginContext
has a working directory, name and instance ID, but no host Agent, Controller,
session persistence, model switching or child-agent spawning. Custom plugin
authors must handle that contract; a plugin is trusted local code.

## Migrating the ingress experiment

Stop the old experimental tool process, then explicitly import its connection
record on the first setup for that workspace:

```powershell
kt mcp-serve setup --non-interactive --workspace C:/work `
  --import-connection C:/private/ingress-test.json --mode external
kt mcp-serve start --workspace C:/work
```

Import preserves the origin and secret and rejects a different workspace.
Use external mode if retaining the separately launched experiment tunnel.
To let KT manage ngrok, stop that separately owned tunnel explicitly before
starting managed mode. The full URL stays the same; the old probe record is not
deleted or modified. Refresh client tool discovery when switching from probe
tools to KT tools. `scripts/mcp_probe/run_kt.py` remains only an experiment helper.

For embedding, `api.mcp_tools.create_app(config, secret=..., port=...,
public_origin=...)` returns an ASGI app. Run its lifespan and **disable host
access logs**. All requests, including discovery, require the exact secret
path. The SDK sees a redacted path and enforces allowed Host/Origin values.
Do not mount an unguarded copy or publish Studio management APIs alongside it.

## Calls, jobs and state

Foreground calls return their Executor job ID, output, error, exit code and
metadata. Native image files are converted using KT's existing media resolver
and returned as MCP image content. PDF text is available; generated page
images are currently elided by the shared normalizer because this runtime has
no persistent artifact store.

For bash/Python, `run_in_background: true` returns the **same KT job ID** while
execution continues. Four tools expose the existing JobStore:

| Tool | Behavior |
| --- | --- |
| `job_status` | Read one job, or list retained jobs plus `instance_id`. |
| `job_wait` | Wait 0–60 seconds, default 10; return current state on timeout. |
| `job_cancel` | Cancel an owned running job through Executor. |
| `job_promote` | Release a foreground call into background without rerunning it. |

Reading or waiting for a retained job is a successful MCP call even when that
job failed or was cancelled. Its `state`, `error` and `exit_code` describe the
job outcome. Unknown jobs, invalid query arguments and foreground execution
failures remain MCP errors. This keeps cancellation details visible instead
of letting clients misclassify a successful status lookup as an invalid call.

There is no automatic elapsed-time promotion. Disconnection or cancellation
of a waiting request does not cancel the owned task. Use `job_cancel` for that.
After a lost reply, query jobs before retrying a mutation. Background completion
does **not** automatically wake a ChatGPT conversation: explicitly query/wait.

All authenticated clients of one server share its read history, tools, plugins
and jobs. Separate instances have separate state. Normal shutdown cancels owned
jobs using existing KT cleanup. Restart creates a new instance and cannot resume
old jobs; old IDs do not match new jobs. The existing JobStore retains at most
100 completed jobs. The URL identity is separate from these in-memory states.

## Access and verification boundary

The full secret URL is a bearer capability, not OAuth or a ChatGPT account
identity. Anyone holding it has this instance's tool access. Keep it out of
source control and ordinary logs. An HTTPS tunnel terminates traffic at its
provider; do not assume the provider cannot see plaintext. Client confirmations
remain client behavior; KT does not bypass them.

Phase-one ChatGPT read/write and fixed-URL reconnection evidence is recorded
in `scripts/mcp_probe/RESULTS.md`. Actual phase-two ChatGPT file, shell, Python
and background-job results, plus a subsequent job-query response correction,
are recorded in `scripts/mcp_probe/PHASE2_RESULTS.md`. SDK checks are distinguished
from owner-reported ChatGPT behavior. CLI lifecycle, managed ingress recovery
and local regression results are in `scripts/mcp_probe/PHASE3_RESULTS.md`.
The subsequent setup workflow and running-configuration isolation checks are
recorded in `scripts/mcp_probe/SETUP_RESULTS.md`.
These checks do not establish compatibility with
all accounts, clients, operating systems or hosting providers.
