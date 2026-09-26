# MCP ingress experiment (phase 1)

This disposable probe answers whether a fixed HTTPS endpoint with a persistent
secret path can connect to ChatGPT, discover tools, read/write a local test file,
and reconnect after a server restart. It is **not** `kt mcp-serve start` and is
not a KT tool exporter. No KT imports, LLM, Agent, Executor, plugin chain, shell,
or JobStore are involved. The bounded test file is an experiment fixture, not a
proposal to restrict the eventual KT workspace permissions.

## Scope and acceptance

The only writable target is `probe.txt` in an explicitly supplied disposable
directory. Three tools expose its content and a process-specific boot ID. The
MCP credential is a random 256-bit token, unrelated to the directory hash used
for naming the local record. Initialization, tool discovery, and calls all pass
through the same secret-path gate. Ordinary HTTP paths return 404.

Acceptance has two separate layers:

1. Local: real SDK client -> loopback HTTP server -> actual file write/read;
   rejected credentials/hosts/origins; restart with the same record and URL;
   changed boot ID and preserved disk content. Test JSON and SSE response modes.
2. External: the owner's ChatGPT account -> fixed HTTPS tunnel -> same probe;
   actual write confirmation and read-back; restart without editing the saved
   ChatGPT connection; repeat with a changed boot ID. Local success cannot
   establish this layer.

Only scripts and test fixtures change. The production KT code, SDK version
constraint, CLI, and user configuration remain untouched. Stop the probe with
Ctrl+C and stop only the tunnel you started. No cloud resources are created by
these scripts. A failed ingress must not be reported as public readiness.

## Requirements

Use a Python environment with `mcp==1.28.1` and `uvicorn>=0.34` installed.
The test also uses `httpx` (an MCP dependency) and stdlib unittest. These scripts
use the current SDK 1.x APIs; they do not establish support for every version
allowed by KT's wider `mcp>=1.0.0,<2` constraint.

Commands below run from the repository root. Replace `$probePython` with the
Python executable in that environment. Do not upgrade the framework to MCP 2.x.

```powershell
$probePython = 'C:\path\to\venv\Scripts\python.exe'
& $probePython -m unittest scripts.mcp_probe.test_server -v
```

Windows sandbox note: CPython 3.14 temporary-directory ACLs may prevent a
restricted token from reopening its newly created directory. Run the test from
a normal user terminal if this occurs; do not weaken filesystem ACLs.

## Prepare the fixed ingress

1. Sign in to [ngrok](https://dashboard.ngrok.com/signup), install the official
   [Windows agent](https://ngrok.com/download/windows), and configure its token
   locally using `ngrok config add-authtoken <YOUR_AUTHTOKEN>`.
2. Find the account's assigned Dev Domain. Do not purchase a domain for this
   experiment. Current entitlements are account-specific; a paid subscription
   is not an assumption of these scripts.
3. Choose a fresh test directory and a record **outside the repository**, then
   substitute your actual domain below. Set the public origin before the first
   launch: changing it later intentionally fails instead of rebinding a record.

```powershell
$probeWorkspace = Join-Path $env:LOCALAPPDATA 'KT-MCP-Probe\workspace'
$probeState = Join-Path $env:USERPROFILE '.kohakuterrarium\mcp-probe\ingress-test.json'
$probeOrigin = 'https://YOUR-ASSIGNED-DOMAIN.ngrok-free.app'
New-Item -ItemType Directory -Force -Path $probeWorkspace | Out-Null
$probeArgs = @(
    'scripts/mcp_probe/server.py',
    '--workspace', $probeWorkspace,
    '--state-file', $probeState,
    '--public-origin', $probeOrigin,
    '--port', '8765'
)
& $probePython @probeArgs
```

Keep that terminal running. In another terminal, run the preconfigured tunnel:

```powershell
ngrok http http://127.0.0.1:8765 --url https://YOUR-ASSIGNED-DOMAIN.ngrok-free.app --inspect=false --log=false
```

The upstream server listens on loopback only. Forward the complete path without
rewriting it. Host validation permits the configured public domain and the
explicit local host/port; it is not globally disabled to accommodate a tunnel.
User-maintained HTTPS ingress can replace ngrok without changing the tools.

In a third terminal with the same variables, explicitly obtain the full URL:

```powershell
& $probePython @probeArgs --show-url
```

This is the only intentional credential display. Treat the complete URL and the
connection JSON as credentials. The server disables access logs and strips the
secret from the request scope before entering the SDK. `--inspect=false` only
controls local ngrok inspection: it does not guarantee that the provider cannot
see or retain decrypted requests. This secret path is not OAuth or account
identity, and anybody with the complete URL can call the three probe tools.

## ChatGPT acceptance procedure

Use the account's developer-mode MCP connection flow. Select **No Authentication**
and paste the **complete** URL, including `/mcp/<secret>`. Available UI and account
policy must be checked on the real account; documentation alone is not a pass.

1. Verify the connection discovers `probe_status`, `probe_read`, `probe_write`.
2. In a fresh conversation with the connection enabled, ask:

   > Use only KT MCP ingress probe. Call probe_status and record boot_id. Read
   > probe.txt with probe_read. Replace it using probe_write with the exact text
   > "CHATGPT-PROBE-1: hello from the web". Call probe_read again and report the
   > exact returned content and boot_id. Do not simulate the tool calls.

3. Inspect the physical `probe.txt` locally. Record any client confirmation.
4. Stop and restart the **probe process**, preserving its arguments and record.
   Leave the tunnel running. In the same ChatGPT connection, call probe_status
   and probe_read. Expect the same content and a different boot ID. A fresh MCP
   initialization is allowed; recreating or editing the URL is not.
5. Also restart the tunnel with its same fixed domain and verify reconnection.
6. Try a separate connection with one character of the secret changed. It must
   not discover or invoke tools. Never put real secrets in test reports.
7. If needed, repeat with `--response-mode sse`. This is SSE responses within
   Streamable HTTP, not the legacy separate `/sse` transport.

Record timestamp, actual tools/results, file contents, boot IDs, confirmations,
and reconnect failures. Do not mark external validation complete based on a
local client, browser GET, or a successful ngrok startup message.

## Intentional limits

- No full KT tool semantics, job behavior, long-running calls or cancellation.
- No tunnel supervisor, duplicate-start discovery, background service, or
  management commands. A competing bind fails; no alternate port is selected.
- No workspace migration or secret rotation UI. A saved record fails closed
  for another directory/origin or damaged JSON. Reuse it unchanged on restart.
- Stateless MCP transport avoids persisting transport sessions. Process state
  is shared across callers, but boot IDs are recreated on each launch.
- Only a small UTF-8 file is exposed; this is not a general filesystem sandbox.

## Sources checked on 2026-09-26

- [ChatGPT developer mode](https://developers.openai.com/api/docs/guides/developer-mode):
  Streamable HTTP, No Authentication, and arbitrary tool support are documented;
  secret-path retention remains an empirical question.
- [Connect and test](https://developers.openai.com/plugins/deploy/connect-chatgpt):
  evaluate actual discovery, tool calls and confirmation in ChatGPT.
- [ngrok domains](https://ngrok.com/docs/gateway/domains): assigned Dev Domains
  are stable; available domain options depend on the plan.
- [ngrok Windows installation](https://ngrok.com/download/windows): official
  agent and local token setup.

See [RESULTS.md](RESULTS.md) for observed results and remaining external gates.
