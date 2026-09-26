# MCP ingress experiment: observed results

Date: 2026-09-26. Source baseline: `b03a2853d394e25285036fe2420e3d9c6858a2e5`.

## Environment

- Windows host; CPython 3.14.2; MCP Python SDK 1.28.1.
- Existing development Python environment reused without installing or upgrading
  the framework. Production source and dependency constraints are unchanged.
- Official portable ngrok agent 3.39.11, verified Authenticode signature from
  `ngrok, Inc.`. The owner supplied a fixed Dev Domain and configured the account.
- Probe listens on `127.0.0.1:8765`; ngrok forwards the complete request path.
- Actual connection record is under the user's `.kohakuterrarium/mcp-probe/`;
  test data is in a dedicated `LOCALAPPDATA/KT-MCP-Probe/workspace` directory.
  No live credential is checked into this report or the repository.

## Local acceptance: passed

Command: `python -m unittest scripts.mcp_probe.test_server -v`.
Latest local workflow result: **1 workflow, 2 response-mode subcases, OK**
(21.521 seconds). Each subcase starts real server subprocesses twice and uses
the MCP SDK over real loopback HTTP. This is a probe test, not KT's framework
integration or end-to-end suite.

Observed checks:

- Initialization and exact three-tool discovery in JSON and SSE response modes.
- Actual UTF-8 file write/read, including non-ASCII content.
- A fresh process at the same URL and secret reads the previous file content;
  its boot ID changes. The connection record remains byte-semantically unchanged.
- Missing/wrong secret, unprotected `/mcp`, `/sse`, `/docs`, `/api`, discovery
  paths, and a trailing-slash variant all fail with 404 across HTTP methods.
- Invalid Host returns 421; invalid Origin returns 403. The configured public
  Host and Origin succeed. Response content types match JSON/SSE selection.
- Oversized writes fail without changing the file.
- Explicit `--show-url` displays the configured HTTPS origin and saved secret;
  normal server logs and ordinary tool metadata/results do not contain it.
- Another workspace and corrupted connection JSON fail without resetting the
  record or generating a replacement secret.

Black (`--check --workers 1 --target-version py310`), Ruff, and `git diff --check`
passed for the probe changes. No frontend or production runtime was modified.

## Public HTTPS SDK acceptance: passed

A real client connected through the owner's fixed ngrok HTTPS domain, not
directly to localhost:

- Bare `/mcp` and a wrong secret returned the probe's exact 404 response.
- MCP initialization and discovery returned `probe_status`, `probe_read`,
  `probe_write`.
- `probe_write` wrote `PUBLIC-SDK-PROBE: hello via fixed HTTPS`; `probe_read`
  returned that exact content, also verified by reading the local file.
- Restarting only the probe kept the endpoint and credential unchanged. A new
  client connection initialized and called tools successfully after restart.
- Observed boot IDs before/after restart:
  `0c4fa1d8d70bc917bb1ab449` -> `7837fa6b4d259f0c140691a8`.

This confirms one tested SDK/client/tunnel combination; it does not substitute
for ChatGPT account acceptance or establish long-running job semantics.

## ChatGPT initial read/write: passed

The owner reported actual tool execution in ChatGPT, with read-back:

- Previous content: `PUBLIC-SDK-PROBE: hello via fixed HTTPS`.
- Written and read-back content: `CHATGPT-PROBE-1: hello from the web`.
- `probe_write` reported 35 characters.
- Boot ID before/after the write: `7837fa6b4d259f0c140691a8`.

The local file was independently inspected and contains the exact ChatGPT
marker. This establishes successful tool discovery and real write/read through
the configured secret-path endpoint for this account.

## Restart after ChatGPT write: SDK and ChatGPT passed

The probe was restarted after the owner's successful write. Its connection
record hash remained unchanged. A read-only public SDK check returned the same
ChatGPT marker and a new boot ID: `f4b91619aa33d5358ac80f72`.

The owned ngrok process was then separately stopped and restarted with the same
fixed domain. Another read-only public SDK check returned the same content and
the same new boot ID. Tunnel restart therefore did not restart the tool process.
Neither verification call rewrote the ChatGPT test file.

The owner subsequently reported actual read-only calls from the existing
ChatGPT connection, following the instruction to keep its configuration
unchanged. The returned boot ID was `f4b91619aa33d5358ac80f72` and the exact
content was `CHATGPT-PROBE-1: hello from the web`; no write was performed.
A further independent public SDK read returned the same values and matched
the physical file.

## Issues found during the experiment

1. CPython 3.14 temporary directories could not be reopened by the restricted
   Windows sandbox identity. The same tests pass from the normal user context;
   filesystem permissions were not weakened.
2. Bare `dict` return annotations did not produce structured tool results in the
   tested SDK. Explicit generic dictionary annotations resolved the failing
   client assertion.
3. The launching environment supplied an HTTP proxy. ngrok rejected agent
   startup with `ERR_NGROK_9009`. Removing proxy variables only from the ngrok
   subprocess allowed the configured account to connect. The system proxy and
   ngrok account configuration were not modified. No paid upgrade was used.
4. Browser automation opened ChatGPT but repeatedly timed out reading its page.
   The owner received the exact connection URL and an explicit test prompt for
   the already signed-in browser.

## Acceptance and limits

- **Phase 1 core acceptance passed:** the fixed HTTPS endpoint and persistent
  secret path worked for actual ChatGPT tool discovery, write/read, and
  reconnection after the probe and tunnel were restarted.
- This validates the tested Windows host, owner's account and ingress setup.
  It does not establish compatibility for other accounts, clients or providers.
- ChatGPT's exact write-confirmation UI behavior was not reported. No claim
  is made that client confirmations are absent or bypassed. Invalid-secret
  denial was verified locally and through public HTTPS; a separate wrong-secret
  ChatGPT connection remains an optional diagnostic.
- The probe and owned tunnel remain running for follow-up experiments. Runtime
  process IDs are recorded locally under `temp/mcp-probe-run/runtime.json`.

Phase two now replaces the live probe with the shared KT tool service, keeping
its connection record unchanged. See [PHASE2_RESULTS.md](PHASE2_RESULTS.md).
The development runner is not the final production CLI.
