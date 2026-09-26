# Setup and running configuration verification

Date: 2026-09-26. Change builds on local checkpoint `a824f686`.

## Accepted behavior

The owner confirmed workspace-scoped setup with `ngrok` and `external` modes,
configuration-only setup, editing including origin changes without rotating
the secret, interactive and noninteractive input, and removal of configuration
flags from start. Existing connection records remain readable.

Setup validates local dependencies, applies partial updates with explicit clear
operations, clears previous-mode settings, and atomically saves after the wizard
summary is confirmed. EOF/cancellation before saving leaves the prior record
intact. Another setup changing the record during review causes a conflict,
not an overwrite.

Saving while running is supported. Current tools, ingress retries and readiness
use the instance's private configuration snapshot; saved settings apply to the
next instance. Status compares both configurations. Start reuses an existing
instance and reports pending changes. URL defaults to the active address while
running and the saved address while stopped; `url --configured` explicitly
selects the saved address. Startup failure does not roll back settings.

Referenced files are not frozen. The pending-state comparison concerns setup
fields, not external file contents. See the [guide](../../docs/en/guides/mcp-server.md)
for the CLI examples and file reload semantics.

## Automated verification

- Affected CLI, MCP setup/runtime, serving, HTTP/auth, integration workflows,
  file-size and dependency-graph guards: **2329 passed, 3 skipped**, in
  `temp/mcp-setup-regression.log`.
- Complete local e2e: **69 passed, 11 failed**, in `temp/mcp-setup-e2e.log`.
  The failure identifiers match the previously documented baseline. These
  failures are not claimed fixed by this change.
- Black, Ruff and diff whitespace checks pass. No frontend files changed.
- Coverage percentages remain unmeasured because this environment has no
  `coverage` or `pytest-cov`; the 95% per-file target is not claimed satisfied.

The lifecycle integration uses real CLI subprocesses and real MCP clients. A
real external child records the requested tunnel origin/port on every retry.
The workflow saves another mode, origin and port while a job is running, then
checks that subsequent tunnel launches still receive the original arguments.
After stop/start, only the saved configuration is applied. It also retains
duplicate-start, workspace-isolation, busy-port, Windows reader-lock and ingress
failure coverage. Its timeout is 200 seconds for deliberate offline waits and
multiple interpreter launches.

Wizard tests exercise confirmation, decline, EOF and a competing save during
review. An audit found that interrupting just after an atomic commit incorrectly
printed "no configuration was saved". A filesystem-boundary regression failed
before correction and passed afterward: input cancellation is handled before
commit, and an interrupted commit no longer makes that false claim.

## Actual public SDK check

1. Checked through the existing public MCP route that the old instance had no
   active jobs, then stopped and started it once to create the new run snapshot.
   The saved URL and secret were unchanged.
2. Started a real background Python job in instance
   `49b213bc714842b2bc3fe7f604b4b9c8`.
3. Used the new CLI setup to save pending external mode, another origin and
   another local port. Confirmed `restart_required=true`, that default `url`
   still selected the active address, and that `url --configured` selected the
   pending address. Repeating start returned the same running instance.
4. Verified process ancestry/run identity and stopped only the owned ngrok
   child. Its guardian was recreated and restored the original public route,
   despite the saved configuration now selecting external mode.
5. Retrieved job `49b213bc714842b2bc3fe7f604b4b9c8_python_f4f0159a` over public
   HTTPS: `state=done`, output `SETUP-RECONNECT-OK`, with successful MCP delivery.
6. Restored the complete original saved configuration in a finally block.
   Confirmed equality with the original record and no pending changes.
   `probe.txt` and `kt-chatgpt-phase2.txt` were unchanged, verified through MCP
   and physical file reads.

The service was left running at the original URL. The report in
`temp/mcp-probe-run/setup-public-results.json` contains no capability URL.
This is public SDK evidence; no new ChatGPT UI call is claimed in this update.
Earlier owner-reported ChatGPT acceptance remains recorded in the phase reports.
