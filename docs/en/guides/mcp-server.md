---
title: MCP server
summary: Start a workspace-scoped MCP server, connect external clients, manage tool jobs, and optionally enable delegation to local Creatures or subagents.
tags:
  - guides
  - mcp
  - deployment
---

# MCP server

The MCP server lets an external MCP client use KT capabilities in the current workspace. The client can call file and command tools directly, or you can optionally configure a local Creature or a one-shot subagent to take on tasks. When only direct tools are used, no Creature is created and no local model is started.

`kt mcp-serve` manages, per workspace, a separate background process, an authenticated Streamable HTTP endpoint, and an optional ngrok tunnel. This guide covers "letting external clients call KT". To let a Creature call other MCP servers, read [MCP client configuration](mcp.md).

> **Confirm the permission scope before exposing the server.** The default tools include file writes and command execution. The workspace is the default execution directory, **not a sandbox**. The full connection URL contains an access secret and must be kept like a credential. See [Access and isolation boundaries](#access-and-isolation-boundaries).

First-time users start at [First-time setup](#first-time-setup). If you are already connected, go straight to [Calling tools and managing jobs](#calling-tools-and-managing-jobs), [Custom direct tools and plugins](#custom-direct-tools-and-plugins), or [Delegating to a local Creature or subagent](#delegating-to-a-local-creature-or-subagent). For connection or runtime problems, see [Troubleshooting](#troubleshooting).

## First-time setup

### Prerequisites and ingress modes

Install a KT version that provides `kt mcp-serve`, and prepare a stable public HTTPS entry point. Choose one of two ingress modes:

| Mode | What you prepare | What KT manages |
| --- | --- | --- |
| `ngrok` | An installed and authenticated ngrok, an account, and a fixed HTTPS domain | Starting, supervising and reaping the ngrok tunnel process |
| `external` | A stable HTTPS entry point you maintain yourself, forwarding requests to a loopback port on this machine | Only the local MCP service; it never starts or stops the external tunnel |

The local port defaults to 8765. In `external` mode, forward the entry point to `http://127.0.0.1:8765`, adjusting the port if you choose another. An HTTPS reverse proxy is needed even if the host itself is publicly reachable: KT listens only on loopback and does not terminate TLS. For setting up the entry point, see [Reverse-proxy deployment](deployment-reverse-proxy.md).

The **public HTTPS origin** asked for by the wizard is the scheme, domain and optional port of the entry point, such as `https://your-domain.example`, without the MCP path. It is not the full connection URL you later paste into the client.

### Configure and start

Run the following in the workspace you want to expose:

```powershell
kt mcp-serve setup
kt mcp-serve start
kt mcp-serve url
```

`setup` opens an interactive wizard that asks, in order, for the ingress mode, public origin, local port, optional MCP tool configuration file, and, in managed mode, the ngrok executable and configuration file. If you only use the default tools at first, you can skip the tool configuration file. Before saving, the wizard shows a change summary and asks for confirmation; cancelling or ending input (EOF) leaves the existing configuration untouched.

**`setup` only saves configuration.** It does not start the service, install ngrok, register an account, allocate a domain or test public connectivity. It checks local dependencies before saving; see [Command options and non-interactive use](#command-options-and-non-interactive-use) for when each check runs.

`start` launches the service from the saved configuration and waits for the public readiness check. Exit code 0 means an authenticated initialization succeeded through the public entry point and reached this exact instance. A nonzero exit code does not necessarily mean the local process failed to start; see [Local readiness and public readiness](#local-readiness-and-public-readiness).

### Add to your client

Once the service is ready, the human-readable start output shows the full connection URL, and `kt mcp-serve url` shows it again. In an external client that supports Streamable HTTP, add an MCP server and paste this **full URL**, not just the public origin.

The full URL has the form `https://your-domain.example/mcp/<secret>`, where the secret is a random 43-character string generated the first time `setup` saves a configuration.

Because the full URL contains the access secret, keep it out of version control, ordinary logs and public screenshots. Tool-call confirmations required by the client are still handled by the client; KT does not bypass them.

### Verify the first tool call

After connecting, have the client call `job_status` without a `job_id` and confirm it returns an `instance_id`. Then call `tree` on the workspace root to verify one read-only tool operation. This step starts no local model and writes no files.

At this point, confirm three separate results: the configuration is saved, the server is publicly ready, and the client can actually call tools. A successful `setup` alone does not mean the last two are done.

## Daily start, stop and status

### Start, stop and retrieve the URL

After configuration, daily use does not require running `setup` again:

```powershell
kt mcp-serve start
kt mcp-serve status
kt mcp-serve url
kt mcp-serve stop
```

Repeated or concurrent `start` calls reuse the existing instance: they neither launch a duplicate nor apply [pending configuration](#active-and-pending-configuration) automatically. `stop` cancels owned jobs, closes the local listener and reaps the managed ngrok process; it keeps the connection configuration and never deletes cloud resources. Starting again reuses the connection settings but creates a new runtime instance; for job and session lifetimes, see [Calling tools and managing jobs](#calling-tools-and-managing-jobs).

The supervisor is not a boot service. After a crash or a computer restart, run `start` again.

### Manage other workspaces

Commands manage the current directory by default. Every subcommand accepts `--workspace PATH`, for example:

```powershell
kt mcp-serve status --workspace ./another-project
```

Workspace identity resolves symlinks and Windows path case. Moving a directory produces a different identity, and the old URL is never rebound to the new location. When the connection record is corrupt, every subcommand (including `setup`) refuses to continue. This version provides no workspace-migration command; to replace the secret or repair the record, see [Leaked secret or corrupted connection record](#leaked-secret-or-corrupted-connection-record).

### Local readiness and public readiness

`start` waits up to 30 seconds by default; use `--wait` to choose 1–120 seconds. For example:

```powershell
kt mcp-serve start --wait 60
kt mcp-serve status --json
```

Exit code 1 can mean the local service is already running but public connectivity is not ready yet. In that case, inspect `local_ready`, `public_ready`, `tunnel_state` and the last public check time in the status, rather than judging from the start command's exit code whether the process exists. Public readiness always describes the running instance, not configuration that has not taken effect yet.

Readiness-related fields in `status --json`:

| Field | Values and meaning |
| --- | --- |
| `state` | `starting`: starting up; `ready`: listening locally and the latest public check passed; `offline`: running locally, but the public check failed or the tunnel is not running; `failed`: startup or runtime error, see `error`; `stopped`: not running; `unresponsive`: the process still holds the instance lock, but its runtime status has not been updated for more than 30 seconds or cannot be read |
| `local_ready` / `public_ready` | Whether the local listener is ready / whether the latest public check passed |
| `tunnel_state` | In `ngrok` mode: `starting`, `connecting`, `online`, `reconnecting` or `stopped`; always `external` in `external` mode |
| `public_checked_at` | Unix timestamp (seconds) of the latest public check. After a passing check it is repeated roughly every 10 seconds; after a failing one, roughly every 2 seconds |
| `error` | The most recent error message, never containing the secret |
| `record_path` | Full path of the `connection.json` connection record |

If the supervisor has not taken over the instance when the wait expires, the start command reaps that child process and reports an error. On slow hosts, retry with a longer `--wait`.

## Calling tools and managing jobs

### Default tools and ordinary calls

Without a tool configuration file, nine direct tools are provided: `read`, `write`, `edit`, `multi_edit`, `glob`, `grep`, `tree`, `bash` and `python`. They share the workspace execution context.

A foreground call returns the KT Executor `job_id`, output, error, exit code and metadata. The `job_id` identifies one execution; later queries, waits and cancellations use that ID without rerunning the original operation.

Reading an image file returns MCP image content. Reading a PDF returns text only, without rendered page images.

### Background execution, queries and waiting

Passing `run_in_background: true` to `bash` / `python` immediately returns **the same KT job ID** while execution continues. These four job tools query and control those jobs:

| Tool | Behavior |
| --- | --- |
| `job_status` | Read one job; without `job_id`, list retained jobs and `instance_id` |
| `job_wait` | Wait 0–60 seconds, default 10; return the current state on timeout |
| `job_cancel` | Cancel an owned running job; Creature delegations have a wider cancellation scope, see [Supplementary input and cancellation](#supplementary-input-and-cancellation) |
| `job_promote` | Release a foreground call into the background, keeping its job ID without rerunning it |

Jobs are never promoted to the background merely because time has passed. A wait timing out, a waiting request disconnecting, or a waiting request being cancelled never cancels the owned job; use `job_cancel` explicitly to stop execution.

**Background completion does not automatically wake a ChatGPT conversation.** The client must query or wait for the result. Local delegation uses the same job tools, but delegation submissions are already asynchronous and never need `job_promote`.

### Cancellation, retries and retention

A job's outcome and the success of the MCP query are two different things. Reading or waiting for a retained record is a successful MCP call even when the job failed or was cancelled; `state`, `error` and `exit_code` describe the job outcome. Unknown jobs, invalid query arguments and foreground execution failures remain MCP errors.

If the HTTP reply to a mutating operation is lost, do not resubmit immediately. Query with `job_status` first; for delegation, also check the session state, so that a lost reply is not mistaken for work that never ran.

The server retains at most 100 completed jobs; direct tool jobs and delegation jobs follow the same bounded retention. A normal server stop cancels owned jobs. A restart creates a new instance, does not resume old jobs, and old IDs never match new jobs. The connection URL's identity is independent of this runtime state: an unchanged URL does not mean old jobs still exist.

## Custom direct tools and plugins

### Configuration files and path rules

Configuration comes in several distinct layers; do not treat them as the same kind of file:

| Configuration | Responsible for | How to specify |
| --- | --- | --- |
| Connection configuration | Workspace identity, secret, public origin, port, ingress mode and paths of external configuration files | Managed by `kt mcp-serve setup` |
| MCP tool configuration file | Direct tools, execution plugins and delegation target registration | `setup --config ./mcp.yaml` |
| Creature or subagent definition | A delegation target's own model, tools, plugins and runtime limits | `delegation.<alias>.config` in the MCP tool configuration |
| ngrok configuration file | ngrok's own settings | `setup --ngrok-config PATH` in managed mode |

The first `setup` saves the connection configuration to `~/.kohakuterrarium/mcp-serve/<workspace-key>/connection.json`, and later starts reuse that record. To customize direct tools, create a dedicated YAML or JSON file instead of passing an ordinary Creature configuration to `--config`.

The examples below place `mcp.yaml` at the workspace root and run the CLI from that directory, hence `workspace: .`. `workspace` must exist, and relative paths resolve against **the directory containing the configuration file**; the resolved path must match the workspace selected by the CLI, otherwise startup is refused.

### Choosing tools and runtime options

`mcp.yaml` at the workspace root:

```yaml
name: KT tools
workspace: .
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

Save the configuration path, then restart the service to enable it:

```powershell
kt mcp-serve setup --config ./mcp.yaml
kt mcp-serve stop
kt mcp-serve start
```

Omitting `tools` enables the nine tools above. Tool names must be unique, and `type` must be `builtin` (the default). `max_output` and each tool's declared runtime options are supported: `timeout` applies to bash/Python and `env` applies to bash. Per-tool `working_dir` is rejected because the shared execution context supplies the directory.

The top level of the MCP tool configuration does not accept controller notification settings, LLM profiles, prompts, triggers, compact or AgentConfig inheritance; these fields are rejected explicitly rather than silently ignored. To have a local model perform tasks, use the delegation target configuration in the next section.

The directory is the default execution location, **not a sandbox**. KT's existing read-before-write, stale-read checks, path guard and execution policies still apply. `pwd_guard` controls access to paths outside the workspace. The default `warn` blocks the first operation on a given outside path and returns a warning; repeating it on the same path proceeds. `block` always refuses; `off` performs no check. Allowed paths are remembered only within the current runtime instance and are shared by all of that instance's clients.

### Execution plugins and their limits

Execution plugins for direct tools use the usual `name`, `type`, `module`, `class` and `options` entries, and support only execution-side capabilities: load/unload, dispatch, pre/post-execution hooks, runtime services and promotion to the background. A plugin load failure aborts startup; plugins overriding LLM, Agent lifecycle, event, compact, prompt, visibility, command or termination hooks are rejected.

At runtime these plugins only have access to the working directory, name and instance ID; there is no host Agent, Controller, session persistence, model switching or subagent creation. Custom plugins must respect this contract; a plugin is trusted local code. These limits apply to the direct tool runtime and do not replace a delegation target's own plugin configuration.

## Delegating to a local Creature or subagent

This section is optional. If you only use direct tools, you need no model configuration or delegation targets.

> Delegation targets share the workspace files but use their own tool and plugin policies. The direct MCP tool allowlist does not restrict delegation; before enabling it, review the permissions and runtime limits in the target definitions. See [Access and isolation boundaries](#access-and-isolation-boundaries).

### Choosing a target type

| Type | Use it for | Conversation lifetime |
| --- | --- | --- |
| `creature` | Multi-turn continuation, or a Creature's tools, plugins and autonomous triggers | Continue with `session_id`; closed sessions cannot be continued |
| `subagent` | One-shot tasks without a parent Creature | Accepts supplementary input while running; cannot continue the same conversation after finishing; a new task creates a new subagent |

Targets are registered only from local configuration. Clients choose an alias, but cannot submit configuration paths or inline definitions, or override model and tool settings. Registering a target does not immediately create an instance or start a model.

### Registering targets and preparing models

First configure local model credentials and profiles with the ordinary KT commands, and prepare the target definitions. Creatures use the ordinary KT configuration format; the `coder` example below requires an installed `@kt-biome` package containing that definition.

Keep `workspace: .` in the `mcp.yaml` above and add a `delegation` field. Only the workspace and delegation parts are shown; existing fields such as `tools` and `plugins` can stay:

```yaml
workspace: .
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

Relative paths to target definitions resolve against the directory of the MCP tool configuration file; installed `@package/...` references use normal KT package resolution. Definitions load when a delegation instance is created; a bad definition fails that job rather than silently dropping configured capabilities.

A standalone subagent's YAML/JSON file uses the ordinary subagent definition fields (see [Subagents](sub-agents.md)). Save the following as `reviewer.yaml` next to `mcp.yaml`; `llm: default` refers to a configured local KT model profile:

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

A subagent's `tools` accepts tool names or ordinary tool configuration entries. Custom tools, package tools and plugins are written the same way as in ordinary KT configuration, and relative paths resolve against the definition file's directory. When `llm` is omitted, `model` can select the subagent's model; there is no parent model to inherit from. Interactive subagents are not currently supported. Runtime limits and sandbox policy belong in the target definition and its plugins.

If you have not saved the `mcp.yaml` path yet, run `kt mcp-serve setup --config ./mcp.yaml`. Then run `kt mcp-serve stop` and `kt mcp-serve start`, and refresh the client's tool list. Changing the target list requires a server restart; editing a referenced definition affects only newly created delegation instances, not existing ones.

### Submitting jobs and continuing sessions

Registering delegation targets adds six tools:

| Tool | Use |
| --- | --- |
| `delegation_targets` | List target aliases and descriptions without starting a model |
| `delegate` | Submit `target` and `prompt`; optionally continue a Creature session with `session_id` |
| `delegation_send` | Add information to a running delegation job (by `job_id`) |
| `delegation_sessions` | List sessions owned by this server, their busy state and current delegation job |
| `delegation_history` | Page through session activity or the current public conversation snapshot |
| `delegation_close` | Stop and close a session owned by this server, keeping readable history |

A typical Creature delegation flow follows. These are MCP tool calls, not terminal commands:

1. Call `delegation_targets` and choose a target alias.
2. Call `delegate(target="coder", prompt="Investigate the failing test")` and save the returned `job_id` and `session_id`. The former identifies this execution, the latter the conversation; submission returns before the model runs.
3. Use `job_status` / `job_wait` to get the result, or `delegation_history` to inspect activity. Waiting and retries follow the [job management rules](#calling-tools-and-managing-jobs) above; `job_promote` is not needed.
4. After the turn ends, continue with `delegate(target="coder", session_id=..., prompt="Apply the fix")`. Omitting `session_id` starts an independent conversation; call `delegation_close` when you no longer need the session.

Each Creature session accepts one active delegated turn at a time. A busy session rejects new work instead of queuing it. Autonomous triggered turns can also make a session busy, in which case there may be no MCP delegation job ID.

`delegation_sessions` reads the Creature's live state, including `idle`, `paused` and `stopped`, rather than inferring it from the target configuration. When a session is busy, use its history to see what it is doing.

### Supplementary input and cancellation

While a job runs, use `delegation_send` to add information to the active `job_id`. Supplementary input is not another queued delegation. For a Creature, it is buffered first and merged into the current turn after the results of the current batch of tool calls are written to the conversation, and before the next model call. Supplementary input is accepted only while the delegation job is still running; `accepted: false` in the result means it was not delivered, for example because the job has finished or is being cancelled. While handling supplementary input, KT may promote foreground tools to the background, after which the original delegated turn ends.

**The end of a Creature turn does not mean all of its background work has ended.** Autonomous activity belongs to the session history and is never reported as the result of an unrelated job. To stop execution, choose an operation according to the session state you want to keep:

| Operation | What stops | Can it be continued later? |
| --- | --- | --- |
| `job_cancel` on a running Creature delegation | Reuses KT stop: stops that Creature, its triggers and all KT-managed tools and subagents, including background work left by earlier turns; waits for cleanup and keeps history | Within the same server lifetime, continue explicitly with the original `session_id`; cancellation itself never restarts it |
| `job_cancel` on a running standalone subagent delegation | Stops only that subagent's own task scope and waits for its normal cancellation chain | One-shot subagents cannot be continued; later tasks create a new instance |
| `delegation_close` | Stops and closes the given session, keeping readable history | Closed sessions cannot be continued |
| Stopping the MCP server | Cancels the server's owned jobs and ends its sessions' lifetime | After a restart, jobs and sessions are not restored automatically, and old MCP handles are no longer valid |

> Calling `job_cancel` on a **completed job** has no effect. To stop background work remaining in a Creature session, use `delegation_close`. Cancellation does not roll back file changes, and does not promise to reclaim arbitrary operating-system processes that escaped KT management.

After a Creature is cancelled, explicitly continuing the same server-owned session rebuilds its runtime from the persisted session files (see [History and session lifecycle](#history-and-session-lifecycle)). This is different from closing the session or restarting the server; do not confuse them.

### History and session lifecycle

`delegation_history(session_id=..., view="events")` shows activity; `view="conversation"` shows public messages and fully retained tool results. Both views paginate with `cursor` and `limit` (1–200), but retain and paginate differently:

| View | Limits to note |
| --- | --- |
| `events` | Keeps only the latest 2,000 events; reports eviction with `truncated` / `earliest_cursor` |
| `conversation` | Pages through a mutable snapshot; compaction or an in-progress turn may shift offsets |

Creature sessions are kept until explicitly closed or until the server stops; cancelling a run does not delete history needed to continue. The server does not connect to other KT processes, import arbitrary saved conversations, or automatically restore jobs or sessions after its own restart.

Creature persistence uses ordinary `.kohakutr` files under `~/.kohakuterrarium/mcp-serve/sessions/<instance-id>/`. Retained files do not mean the original MCP handles are still usable: those handles are valid only for the current server lifetime.

## Changing configuration and applying it

### Saving and restarting

Run `setup` again to change the connection configuration. When editing an existing connection, unspecified fields keep their previous values; when creating a connection for the first time, unspecified optional fields use defaults. In the wizard, an empty answer keeps the displayed value, and `-` clears an optional file path. `--clear-config` and `--clear-ngrok-config` clear the respective custom file paths and restore defaults.

**Saving configuration does not change the running instance.** New settings take effect only after stopping and starting the service again; repeating `start` just reuses the current instance and reports pending changes, without silently restarting it.

For example, to switch an existing connection to a new public origin you have already prepared:

```powershell
kt mcp-serve setup --non-interactive --origin https://new-domain.example
kt mcp-serve status
kt mcp-serve url                 # running URL while the service runs; saved URL when stopped
kt mcp-serve url --configured    # explicitly get the URL the next start will use
kt mcp-serve stop
kt mcp-serve start
```

Changing the origin does not rotate the secret (use `rotate` for that; see [Leaked secret or corrupted connection record](#leaked-secret-or-corrupted-connection-record)), but after restarting you must update the connection URL in the client. Switching to `external` mode clears saved ngrok-specific settings; ngrok options are rejected in that mode.

### Active and pending configuration

`status` shows `active`, `configured`, `pending_changes` and `restart_required`, without credentials. They show, respectively, the running configuration, the saved configuration, the difference between the two, and whether a restart is needed. Public readiness checks still describe the running instance.

The current instance and all of its tunnel retries use a private `active.json` snapshot bound to its run identity, and never adopt pending configuration in the middle of a job.

**The snapshot freezes only `setup`-managed fields, not the contents of referenced files.** Changes to the MCP tool configuration file take effect at the next tool-process start; changes to the ngrok configuration file may affect the next tunnel restart. Status comparison neither detects nor promises to freeze those file contents, so an empty `pending_changes` does not mean external files are unchanged. For when delegation target definitions load, see [Registering targets and preparing models](#registering-targets-and-preparing-models).

### Concurrent edits and upgrades

The wizard checks whether the configuration record changed after it was opened. If another terminal has saved new configuration, this save reports a conflict and asks you to reopen the wizard, without overwriting the other change. All validation precedes a single atomic save. A failed start keeps the new configuration and reports the error, without rolling back.

Existing connection records remain readable without reconfiguration. A process started by an older CLI has no active snapshot: stop and start it once before using live configuration editing or retrieving its running URL with the new CLI. Upgrading never generates a new identity or secret.

## Troubleshooting

Supervisor and tunnel diagnostics are stored next to the connection record, as `server.log` and `tunnel.log` under `~/.kohakuterrarium/mcp-serve/<workspace-key>/`. For connection problems, check `kt mcp-serve status --json` first, then the logs.

| Symptom | Checks and fixes |
| --- | --- |
| `start` returns nonzero, but the local service seems to be running | Inspect `local_ready`, `public_ready`, `tunnel_state` and the last public check time; distinguish "public connectivity not ready yet" from "local startup failed". If the supervisor timed out before taking over, retry with a longer `--wait` |
| `setup` succeeded, but the client cannot connect | `setup` does not verify public connectivity. Confirm the entry point forwards to the selected loopback port, the service is publicly ready, and the client uses the full connection URL rather than the origin |
| Local port already in use | Find what holds it, or save another port with `setup --port`; adjust the forwarding target of an `external` entry point accordingly, then start the service |
| Error `Tool configuration belongs to a different workspace` | Compare the CLI's current directory or `--workspace` with `workspace` in `mcp.yaml`, resolved relative to that file's directory; both must point to the same workspace |
| Configuration changes do not take effect | Check `pending_changes` and `restart_required`, then stop and start; repeating `start` does not restart. If you edited the contents of a referenced file, the status diff does not detect it |
| Added a delegation target, but the client cannot find the tools | Changing the target list requires a server restart and a refresh of the client's tool list |
| No reply after a background job finishes | Background completion does not wake the client conversation; call `job_status` or `job_wait` explicitly |
| Delegation returns busy, but there is no active MCP delegation job ID | The Creature may be running an autonomous triggered turn; check the live state in `delegation_sessions` and `delegation_history` |
| Status shows `unresponsive` | The runtime status has not been updated for more than 30 seconds, which does not necessarily mean tools have stopped; check the logs, and see [Process and tunnel recovery](#process-and-tunnel-recovery) for causes |
| Error `Missing or invalid saved connection` | The connection record is corrupt; see [Leaked secret or corrupted connection record](#leaked-secret-or-corrupted-connection-record) |
| `rotate` fails with `MCP instance lock is busy` | The service is still running; run `stop` first. If it is already stopped, retry shortly |
| Old `job_id` / `session_id` unusable after a restart | A restart creates a new instance and does not restore old jobs or sessions; persisted files and a stable connection URL do not extend the validity of old MCP handles |

## Access and isolation boundaries

The full secret URL is a **bearer credential**, not OAuth or a ChatGPT account identity; anyone holding the URL can access this instance's tools. `setup` summaries, status and the JSON output of lifecycle commands hide the secret, but the human-readable ready output of `start` and the `url` command display the full URL on purpose.

All authenticated clients of one server share direct tool read history, tools, plugins and jobs, as well as access to delegation sessions and history; different server instances have independent state. Do not treat different clients or conversations as permission boundaries.

Delegation instances inherit the MCP workspace as their working directory. Different conversations share the files in that directory; no worktree or filesystem isolation is created. MCP adds no extra path restriction; delegation targets' tools, plugins and autonomous triggers follow the target configuration and are **not constrained by the direct MCP tool allowlist or its policies**. Runtime limits and sandbox policies belong in the target definitions and their plugins.

An HTTPS tunnel terminates TLS at its provider; do not assume the provider cannot see plaintext.

### Leaked secret or corrupted connection record

If the secret has leaked, or you want to replace it routinely, stop the service and generate a new secret with `rotate`:

```powershell
kt mcp-serve stop
kt mcp-serve rotate
kt mcp-serve start
kt mcp-serve url
```

`rotate` replaces only the current workspace's secret; the origin, ingress mode, port, configuration file paths and other settings stay unchanged, and other workspaces are unaffected. It runs only while the service is stopped and never stops the service for you: if the instance is still running, it fails with `MCP instance lock is busy` and changes nothing. There is no confirmation prompt, and the service remains stopped afterwards. Rotation requires only a valid saved connection; dependencies such as ngrok or the tool configuration do not need to be available.

Neither the normal nor the `--json` output of `rotate` contains the secret or the connection URL; use `url` to get the new URL and update every client. After the next start, the old URL no longer authenticates, and the `active.json` snapshot left by the previous run is replaced. Run the commands above one at a time and confirm that `stop` and `rotate` succeed before continuing. When managing another workspace, pass the same `--workspace` to each command.

`setup` still never replaces the secret. If a `setup` wizard was already open before the rotation, saving it reports a configuration conflict; run `setup` again.

When the connection record is corrupt, `rotate`, `setup` and the other subcommands fail with `Missing or invalid saved connection`. In that case, regenerate the connection as follows:

1. Run `kt mcp-serve stop`. You must stop the service before moving the record. This command still stops a running instance but may report an error at the end, which you can ignore.
2. Move `connection.json` out of its directory as a backup. Status cannot be queried while the record is corrupt; the file is under `~/.kohakuterrarium/mcp-serve/<workspace-key>/`.
3. Run `kt mcp-serve setup` again. This counts as a first-time setup: provide the origin, mode, port, `--config` and other settings again. Saving generates a new secret.
4. Run `kt mcp-serve start`, and replace the old URL with the new one in the client.

If you have an intact backup, you can instead overwrite `connection.json` with it after stopping the service, which keeps the original secret and URL. If the secret in the backup may have leaked, run `rotate` immediately after restoring.

## Advanced reference

### Command options and non-interactive use

Configuration options belong to **`setup`**, not `start`:

| Command | Main options |
| --- | --- |
| `setup` | `--mode`, `--origin`, `--port`, `--config`, `--ngrok-bin`, `--ngrok-config`, `--clear-config`, `--clear-ngrok-config`, `--non-interactive` |
| `start` | `--wait`, which only controls the startup wait; configuration options are not accepted |
| `url` | `--configured`, to get the URL the next start will use |
| `rotate` | No dedicated options; replaces the secret while the service is stopped, keeping other settings |
| All subcommands | `--workspace PATH` |
| `setup` / `start` / `stop` / `status` / `rotate` | `--json`, JSON output without the MCP secret |

Without saved configuration, `start` tells you to run `setup` first. Scripts can choose an ingress mode explicitly; the following two lines are alternatives, not steps to run in sequence:

```powershell
kt mcp-serve setup --non-interactive --mode ngrok --origin https://your-fixed-domain.example
kt mcp-serve setup --non-interactive --mode external --origin https://your-domain.example
```

Non-TTY input, `--non-interactive` or `--json` disables all `setup` prompts; missing required options produce a nonzero exit code. In interactive mode, command-line options prefill the wizard.

Exit codes:

| Exit code | When |
| --- | --- |
| 0 | The command succeeded; for `start`, the public readiness check passed. An interactive `setup` answered "no" at the confirmation prompt also returns 0 |
| 1 | `start` ended before public readiness (the local service may already be running); a configuration, dependency, lock or process error, such as no saved configuration, another lifecycle command still running, or `stop` failing to stop the instance within 20 seconds, or `rotate` run on a running workspace; an interactive `setup` interrupted by Ctrl+C or EOF |
| 2 | Invalid command-line arguments |

With `--json`, a failing command prints `{"error": "..."}`.

Before saving, the origin and local dependencies are validated: the tool configuration is parsed, managed mode confirms ngrok can be found, and an explicitly specified ngrok configuration file must be readable. ngrok validates its configuration file contents when it starts, and whether the local port is in use is also checked at startup.

### Process and tunnel recovery

The background supervisor holds an operating-system file lock per workspace; a stale PID alone never establishes ownership. Stop requests carry the current run identity in a private local file, not through a remote management endpoint; an old stop request cannot stop a newer instance.

A public outage never triggers a random-domain fallback or creates a new tool instance. When managed ngrok exits, it is retried with bounded 1–30 second backoff; a still-running ngrok handles its own network reconnects. The supervisor periodically rechecks the public instance identity without downloading job contents.

Only managed mode removes inherited HTTP proxy environment variables from the ngrok child process; ngrok's own configuration is kept and the system proxy is not changed. An ownership pipe lets the tunnel guardian reap its ngrok child if the supervisor exits unexpectedly.

On Windows, readers briefly holding a file can delay atomic updates of the status file. Such diagnostic write failures do not stop tool execution; when the runtime status has not been updated for more than 30 seconds it is shown as `unresponsive`, and a still-held ownership lock prevents a duplicate launch.

### Delegation runtime and team collaboration scope

Creatures use KT headless I/O while keeping named outputs and triggers. Terrarium recipes are not delegation targets: correlating, completing and cancelling team tasks requires a separate collaboration protocol; hosting Creatures in a Terrarium internally does not provide those team-level semantics.

### Embedding the ASGI app

`api.mcp_tools.create_app(config, secret=..., port=..., public_origin=...)` returns an ASGI app. Run its lifespan and **disable host access logs**. All requests, including discovery, require the exact secret path. The SDK sees a redacted path and enforces allowed Host/Origin values. Do not mount an unguarded copy, and do not publish the Studio management API alongside it.

## See also

- [MCP client configuration](mcp.md): connect external MCP tools to a Creature.
- [Creature configuration](creatures.md): define targets for local delegation.
- [Subagents](sub-agents.md): subagent capabilities and configuration.
- [Reverse-proxy deployment](deployment-reverse-proxy.md): maintain an external HTTPS entry point.
