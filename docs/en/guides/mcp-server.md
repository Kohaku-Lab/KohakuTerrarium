# MCP tool server

The standalone server exposes KT tools to an external MCP client without
creating a Creature or starting a local model. `kt mcp-serve` manages a separate
background process per workspace, an authenticated Streamable HTTP endpoint,
and optionally its preconfigured ngrok tunnel.

## Start, inspect and stop

Install this checkout (or put its `src` directory on `PYTHONPATH` and use
`python -m kohakuterrarium` in place of `kt`). The tested SDK dependency is
`mcp>=1.28.1,<2`; the existing MCP client remains on the same major.
Configure an ngrok account and fixed HTTPS domain first, then from the desired
working directory run:

```powershell
kt mcp-serve start --public-origin https://your-fixed-domain.example
kt mcp-serve status
kt mcp-serve stop
kt mcp-serve start
```

`start` defaults to managed ngrok and a loopback listener on port 8765. Use
`--ngrok-bin` and `--ngrok-config` for a specific installed agent/configuration,
or `--port` for another local port. A busy port fails explicitly. The command
does not install ngrok, register an account, or allocate a public domain.

The first start saves the canonical workspace, public origin, random secret,
local port, ingress mode and optional tool-config path under
`~/.kohakuterrarium/mcp-serve/<workspace-key>/connection.json`. Subsequent starts
reuse them. A ready human-readable start prints the complete URL for deliberate
copying; `kt mcp-serve url` displays it again. Keep that URL private. `status`
and `--json` lifecycle output omit the secret.

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
kt mcp-serve start --public-origin https://your-domain.example --tunnel external
```

Point that entry at the selected loopback port. KT neither starts nor stops an
external tunnel. To change local port, ingress mode, agent path or tool-config
path, stop first and supply the new option to `start`; changing a saved public
origin is rejected. In managed mode only, inherited HTTP proxy environment
variables are removed from the ngrok child, matching the tested reference setup;
ngrok's own configuration is retained. The system proxy is not changed.

Supervisor and tunnel diagnostics are in `server.log` and `tunnel.log` beside
the connection record. An ownership pipe lets a tunnel guardian reap its ngrok
child if the supervisor exits unexpectedly. The supervisor itself is not an
OS boot service: after its crash or a computer restart, run `start` again.
Transient Windows readers can delay atomic status publication. Such diagnostic
write failures do not stop tool execution; status becomes `unresponsive` if its
heartbeat stays stale, and a held ownership lock prevents a duplicate launch.

## Configuration

Without `--config`, the nine tools below use their defaults. To customize them,
pass `--config` with a dedicated YAML or JSON file:

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

`workspace` must exist; a relative path resolves against the config file's
directory. Omitting `tools` enables the nine tools above. Tools must have
unique names and `type: builtin` (the default). `max_output` and each tool's
declared runtime options are accepted. `timeout` applies to bash/Python;
`env` applies to bash. Per-tool `working_dir` is rejected because the shared
execution context supplies the directory. Controller notification settings,
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
record on the first CLI start for that workspace:

```powershell
kt mcp-serve start --workspace C:/work `
  --import-connection C:/private/ingress-test.json --tunnel external
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
These checks do not establish compatibility with
all accounts, clients, operating systems or hosting providers.
