# Phase two: shared KT tools and jobs

Date: 2026-09-26. Base: `b03a2853d394e25285036fe2420e3d9c6858a2e5`.

## Implemented and exercised

- Dedicated strict configuration and a process-lifetime ToolRuntime with no
  Agent/LLM construction. Nine builtin tools plus four job-access tools.
- Agent and MCP use the same extracted pre-dispatch, task-start and background
  handle functions. Executor accepts an explicit context binding; its original
  Agent binding and live plugin behavior remain available.
- Existing file-read/stale-read guards, warn/retry path behavior, plugin vetoes,
  runtime services, execution limits, result normalization and JobStore remain
  in the shared path. No new queue, task state machine or automatic promotion.
- Exact secret-path gate plus SDK Host/Origin checks for Streamable HTTP.
  Host access logging is disabled by the development runner.
- Waiting cancellation leaves execution alive. Explicit cancellation and normal
  runtime shutdown use Executor cleanup. Mid-flight promotion reuses its job.
- Each instance has independent read state, jobs, plugins and directory; IDs
  carry an instance namespace. Process restart does not restore job state.

## Validation

Environment: Windows, CPython 3.14.2, MCP SDK 1.28.1. The dependency lower bound
now matches the tested SDK while retaining `<2` for the existing MCP client.

The broad affected-tier run covered `tests/unit/{core,bootstrap,modules,mcp,
mcp_server}`, new API tests, file-size/dependency guards, and integration
`{core,bootstrap,modules,mcp,mcp_server}`: **4,582 passed, one new assertion
failed**. That assertion incorrectly expected the first outside-directory write
to pass. It was corrected to verify KT's existing warning, absence of a write,
then successful intentional retry. Production path-guard behavior was not changed.

The subsequent targeted core/new-runtime/API/HTTP run passed **115 tests**.
The HTTP workflow also covers actual shell/Python execution, waiting, cancellation,
same-job promotion, and a cancelled HTTP request whose job is retrieved through
an independent initialized client. Negative cases include unknown config/options,
unsupported model plugins, failed policy loading, wrong secrets and Host/Origin.

The complete existing e2e suite returned **69 passed, 11 failed**. Running the
same suite against an unmodified `git archive HEAD` snapshot returned **69 passed,
the same 11 failed**, at the same assertions. These failures concern session
history/resume, provider inventory, cross-node delivery, worker delegation and
prompt expectations. They are baseline failures, not a green e2e result. Local
details are in `temp/mcp-e2e-full.log` and `temp/mcp-e2e-baseline.log`.

Ruff, Black and dependency/file-size guards were run. Coverage percentage was
not measured: neither pytest-cov nor coverage is installed in the test environment.
No 95% coverage claim is made. No frontend files changed. No commit or PR was made.

## Public HTTPS SDK acceptance

The already-owned probe process was replaced with `scripts/mcp_probe/run_kt.py`;
the fixed tunnel and private connection record were preserved. A real MCP SDK
client traversed the public HTTPS domain and verified:

- Exactly 13 tools discovered; wrong/missing-secret endpoints rejected.
- Write, read, edit and physical disk agreement for `kt-phase2-sdk.txt`.
- Actual bash output `KT-MCP-SHELL` and Python output `KT-MCP-PYTHON`.
- Background Python completion retrieved via its job ID; another job cancelled
  and observed in `cancelled` state.
- Original `probe.txt` remained exactly `CHATGPT-PROBE-1: hello from the web`.

Instance observed: `00716680947f49c9be1b915faeaa95b0`.
Private runtime IDs/config and the secret-free SDK report remain under ignored
`temp/mcp-probe-run/`; credentials are outside the repository. The live server and
owned tunnel are retained for the owner's ChatGPT acceptance.

## Actual ChatGPT acceptance and query correction

The owner reported actual calls from ChatGPT on instance
`00716680947f49c9be1b915faeaa95b0`:

- Read `probe.txt` without changing its original text.
- Created and reread `kt-chatgpt-phase2.txt` with exact content `CHATGPT-KT-PHASE2`.
- bash returned `CHATGPT-SHELL-OK`, exit code 0; Python returned
  `CHATGPT-PYTHON-OK`, exit code 0.
- Background Python job ending `_429a7f68` was queried through `job_wait` with
  the same full ID and returned `done`, `CHATGPT-BG-OK`, exit code 0.
- Background Python job ending `_666c6c83` was successfully cancelled. Its next
  `job_status` payload contained `cancelled` and the cancellation reason, but
  the connector wrapped the response as `INVALID_ARGUMENT`.

Independent read-only public SDK inspection confirmed both jobs and exact disk
contents. It reproduced `isError: true` on the cancelled-job status lookup.
The response adapter had confused the stored job outcome with the success of
the query. The integration regression first failed on this exact flag.

The adapter now returns successful `job_status`/`job_wait` calls for retained
jobs, including failures and cancellations, preserving their state/error/exit
code. Unknown jobs and foreground execution failures still report MCP errors.
The affected unit/runtime/HTTP tests passed **26 tests**, including failed-job
queries, unknown IDs and foreground failure classification. Ruff and formatting
checks passed. No shared executor or cancellation semantics changed in this fix.

After confirming no jobs were active, the owned server was restarted with the
same URL, record and tunnel. Current instance:
`abbb792cb37044889237b33c6ddee613`. Public HTTPS SDK verification passed:

- Newly cancelled job `abbb792cb37044889237b33c6ddee613_python_2e99688e` returns
  `state: cancelled`, its original cancellation reason and `isError: false`
  from both `job_status` and `job_wait`.
- Queries of an intentionally failed Python job succeed while its nonzero exit
  code and traceback remain present; the foreground execution itself is an error.
- Previous-instance job IDs report `Unknown job`; both owner test files persist.

The owner subsequently repeated `job_status` in ChatGPT for
`abbb792cb37044889237b33c6ddee613_python_2e99688e`. The reported response returned
normally with `tool: python`, `state: cancelled`, empty output,
`error: User manually interrupted this job.`, `exit_code: null`, and empty
metadata. No `INVALID_ARGUMENT` wrapper was reported on this recheck.

**Phase-two ChatGPT acceptance passed, including the corrected cancelled-job
query.** This is owner-reported external evidence in addition to the independent
public SDK verification. The baseline e2e failures and coverage limitations
recorded above remain; acceptance is not a claim that every repository test passes.

## Remaining scope

The final CLI, duplicate-start behavior, managed tunnel supervision and status
diagnostics belong to phase three. The phase-two runner does not implement them.
Subsequent implementation and verification are recorded in
[PHASE3_RESULTS.md](PHASE3_RESULTS.md).
PDF text is supported, but generated page images are elided without an artifact
store; local image-file reads are delivered as native MCP images.

See [the guide](../../docs/en/guides/mcp-server.md) for configuration and semantics.
