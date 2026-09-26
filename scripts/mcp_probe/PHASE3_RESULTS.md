# Phase three: persistent CLI and owned ingress

Date: 2026-09-26. Checkout baseline:
`b03a2853d394e25285036fe2420e3d9c6858a2e5`.
These are observed Windows results, not claims about all clients or platforms.

## Delivered behavior

- `kt mcp-serve start`, `stop`, `status`, and `url`; background service by
  default, with a canonical workspace identity and private persistent record.
- Saved origin and random secret survive normal stops/restarts. An explicit
  legacy-record import preserves the already accepted ChatGPT connection.
- Workspace-scoped OS locks serialize lifecycle commands and prevent duplicate
  instances. A stale PID is never authority to stop a process. Corrupt identity
  records fail closed; a missing runtime snapshot does not bypass a live lock.
- Managed ngrok uses its preconfigured fixed domain. A pipe-owned guardian
  reaps only its child. External ingress is never started or stopped by KT.
- Local readiness and authenticated public readiness are separate. The public
  probe checks initialization metadata for this instance, without fetching job
  outputs. Offline startup returns a nonzero exit code while preserving a
  functioning local runtime. Tunnel failures retry without restarting tools.
- Port conflicts are explicit. No random-port, random-domain, workspace-rebind
  or secret-reset fallback exists. Status JSON omits credentials; `url` and a
  successful human-readable `start` deliberately show the connection URL.

No OS boot-service registration, workspace-migration UI or secret-rotation
command is included. The [guide](../../docs/en/guides/mcp-server.md) covers these
boundaries, configuration and installation from this checkout.

## Live public verification

The existing fixed origin is `https://renegade-yeast-unsealed.ngrok-free.dev`.
The complete capability URL is deliberately absent from this record.

1. Imported the phase-one connection record without changing its secret or
   canonical workspace. Started with the existing external ngrok process.
   Public SDK initialization and file reads passed. Stopping KT left that
   external tunnel alive, verified by its process identity.
2. Stopped the explicitly identified old experiment tunnel and switched to
   managed ngrok. Repeated `start` returned the same supervisor/run identity.
3. On instance `400d6b52ce9d48cb9e04837211b6726d`, created a real background
   Python job, confirmed it was running, and forcibly stopped only the owned
   ngrok child. Public readiness became false while local readiness stayed
   true. A new tunnel guardian restored the fixed public route; supervisor PID
   and tool instance remained unchanged.
4. Retrieved the same job
   `400d6b52ce9d48cb9e04837211b6726d_python_a71c187c` through public HTTPS:
   `state=done`, `output=PHASE3-SURVIVED-TUNNEL`, `exit_code=0`, `isError=false`.
5. Stopped KT through the CLI. The owned ngrok PID was gone afterward. Restarted
   using only the saved workspace configuration; public readiness passed.
   Compared the full URL and secret in memory with the legacy record: equal.
   The previous job ID returned `Unknown job` from the new instance.

Final live instance at verification:
`897505a87c90427b9b5eb45b6df7acd5`. The service and managed tunnel were left
running. Both files remained byte-for-byte unchanged:

| File | Content |
| --- | --- |
| `probe.txt` | `CHATGPT-PROBE-1: hello from the web` |
| `kt-chatgpt-phase2.txt` | `CHATGPT-KT-PHASE2` |

The public calls above used the actual MCP Python SDK over ngrok. Subsequently,
the owner reported actual KT MCP calls from ChatGPT using the existing
connection after the final restart, with no writes:

- `job_status` returned `instance_id: 897505a87c90427b9b5eb45b6df7acd5`, matching
  the final CLI-managed instance.
- Reading `probe.txt` returned `CHATGPT-PROBE-1: hello from the web`.

**Phase-three ChatGPT reconnection acceptance passed.** This is owner-reported
external evidence, separate from the independent public SDK checks. Together
with the phase-one/two acceptance records, it completes the agreed three-phase
functional acceptance for the owner's Windows environment and ChatGPT account.
The baseline e2e failures and unmeasured coverage below remain; this does not
claim every repository quality gate is satisfied.

## Bugs found during acceptance and regression evidence

A first tunnel-recovery attempt exposed a real Windows bug: a concurrent reader
of `runtime.json` denied its atomic replacement, and the resulting diagnostic
write exception terminated the tool runtime. That attempt was not accepted as
a successful recovery. A real open-file regression reproduced the failure.
Atomic replacement now retries briefly, and status publication runs off the
event loop with failures isolated from tool lifetime. The corrected live
recovery described above passed.

The same audit found tunnel-launch I/O errors could terminate tools before the
guardian existed. A real directory occupying `tunnel.log` reproduced it through
the CLI. Launch errors now report offline status and retry independently;
removing the obstruction does not create a new tool instance. Stale online
tunnel status after a stopped ownership lock was also reproduced and corrected.

## Automated checks

- Final MCP unit suites, two complete integration workflows, file-size and
  dependency-graph guards: **2025 passed** (`temp/mcp-phase3-final-checks.log`).
- Subsequent stopped-state diagnostic regression: **2 passed**.
- Broader affected CLI, API/auth, serving, file-guard and architecture
  regressions earlier in phase three: **2289 passed, 3 skipped**. Counts overlap;
  they are not an additive total.
- Full local e2e run: **69 passed, 11 failed**. All 11 failures match the
  unmodified baseline comparison from phase two (session/history/resume,
  provider enumeration, cross-node channels, worker dispatch and prompt
  expectations). No claim is made that the entire repository is green.
- Changed Python files pass Black; `ruff check src/ tests/` and `git diff
  --check` pass. No frontend files were changed.

The lifecycle workflow exercises concurrent starts, separate workspaces and
jobs, stop isolation, stale reads after restart, busy ports, offline ingress,
Windows status-reader locks, and tunnel launch/exit failures using real local
processes and MCP clients. It has a 150-second timeout because intentional
offline waits and interpreter launches exceed the normal 60-second budget.

Coverage percentages remain unmeasured: this environment lacks `coverage` and
`pytest-cov`. The repository's 95% per-file target is not claimed satisfied;
the limitation and baseline failures are also recorded in `temp/BUGS.md`.
