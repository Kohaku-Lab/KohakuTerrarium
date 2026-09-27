# Local MCP delegation verification

Date: 2026-09-27. Change builds on checkpoint `10911175`.

## Delivered contract

Locally registered aliases select ordinary Creature definitions or independent
one-shot subagent definitions. The server owns all instances it creates. Target
configuration is separate from runtime session state. Existing direct MCP tools
remain available without starting a model.

Clients can submit asynchronous work, supplement active work, discover targets,
list sessions, read paginated activity/conversation history, and close sessions.
The existing job status/wait/cancel tools serve delegated work. Creature sessions
can be continued explicitly; cancelling uses KT stop and stops prior managed
background work too. Explicit continuation after cancellation restores that
server-owned conversation through KT persistence. No session is automatically
resumed after server restart. Configured triggers remain enabled.

Standalone subagents use KT tools, plugins, model resolution and SubAgentManager
without constructing a parent Creature. Their working directory is the MCP
workspace; delegation adds no filesystem boundary. Their internal JobStore is
owned separately from the public MCP job, just as Creature execution is, so a
public job cannot report completion before its owned resources finish closing.

Terrarium recipes, foreign runtime attachment, arbitrary historical-session
import and completed-subagent continuation remain outside this first release.
See the [setup and client guide](../../docs/en/guides/mcp-server.md).

## Automated evidence

- Broad affected core/bootstrap/subagent/MCP unit and integration workflows:
  **2122 passed**, in `temp/mcp-delegation-broad.log`.
- Final MCP/subagent/job unit checks, file-size/dependency guards, real MCP HTTP
  workflow and full programmatic Creature journey: **2278 passed**, in
  `temp/mcp-delegation-final.log`. This overlaps the preceding run; the counts
  must not be added together.
- Complete local e2e suite: **69 passed, 11 failed**, in
  `temp/mcp-delegation-e2e.log`. All eleven failure identifiers match the recorded
  setup baseline in `temp/mcp-setup-e2e.log`; they are not claimed fixed. The
  affected Creature journey and HTTP workflow were rerun after the final fixes.
- Full `black --check --workers 1 --target-version py310 src/ tests/`: all
  **1702 files** unchanged. Full `ruff check src/ tests/` and `git diff --check`
  pass. No frontend files changed.

The tests use real KT owners, persistence, filesystem tools, triggers, plugins
and MCP clients. Only model execution is replaced by ScriptedLLM. Coverage
percentages remain unmeasured because this environment has neither `coverage`
nor `pytest-cov`; the per-file 95% target is not claimed satisfied.

## Audit regressions

Failing behavior tests were reproduced before fixing malformed catalog parsing,
shutdown before task startup, invalid-definition state, failed plugin-load
cleanup, provider-cleanup errors, close/submit races, and global named-subagent
model settings overriding the target's selected provider. Final audit tests also
covered premature completion during provider cleanup, one shutdown failure
skipping other owners/direct jobs, and stale session-state reporting.

Additional checks cover parallel isolated conversations, wait/disconnect without
cancellation, autonomous busy rejection without a delegation job ID, cancellation
of background tools from earlier turns, context-preserving continuation,
full retained tool results, and server-restart rejection of old session/job IDs.

## Public SDK and real-provider acceptance

Following the owner's request to arrange integration testing, the existing public
endpoint was checked for active jobs, configured with two explicit test targets,
and restarted. Its origin and private authentication URL are unchanged. Instance
`e354c4ee98ae43dab31cea45bcaaf347` exposes 19 tools, including `probe_creature` and
`probe_subagent`. The configured provider is the owner's existing default:
`codex/gpt-6-astra-custom@context=700k,reasoning=high`.

Actual public SDK calls with that real model verified:

- Creature wrote `kt-delegation-sdk.txt`; direct MCP read and a physical file read
  independently confirmed `KT-DELEGATION-SDK:e13a5f89ef9d`.
- Another turn on the same session recalled a random token absent from its new
  prompt, without rereading a file.
- An independent subagent read the artifact and returned `SUBAGENT-SDK-OK`.
  Conversation history retained the full read result.
- New work on a busy session was rejected. Supplemental input was accepted using
  KT's existing foreground-to-background promotion behavior.
- In a separate cancellation case, activity confirmed a running Python tool.
  Job `e354c4ee98ae43dab31cea45bcaaf347_creature_8a635f1052cf461996c61a2b6da2d990`
  became `cancelled`, its session became `stopped`, and the underlying Python
  job emitted an interrupted tool event.
- Explicit restoration recalled `CANCEL-RESTORE-9b7e16f48c90` in job
  `e354c4ee98ae43dab31cea45bcaaf347_creature_a51f114e490245c59a7cfb6b5f892f11`.
- Both original probe files retained their exact contents. All test sessions
  were closed and no active MCP jobs remained at the final check.

Two probe assumptions were corrected against actual runtime evidence: text-mode
tool results use `[read]` user messages instead of native `role=tool` messages;
and supplemental input can finish the current turn after promoting its tool.
Cancelling that already completed turn correctly returns false, so active-turn
cancellation was tested independently. No implementation changes were needed.

Machine-local configuration and redacted reports are in
`temp/mcp-delegation-live/`; `previous-settings.json` records the pre-test setup
without credentials, and `public-results.json` contains the observed jobs and
nine successful checks. The server is left running with these test targets for
ChatGPT acceptance. They are acceptance definitions, not production agent presets.

## Owner-reported ChatGPT acceptance, independently checked

On 2026-09-27 the owner supplied actual ChatGPT connector-call results from the
same instance. Read-only public SDK queries independently verified all three
retained jobs, both closed sessions, conversation history and exact file contents:

| Operation | Job suffix | State |
| --- | --- | --- |
| Creature write | `creature_34ffe47791754fc0a1c8ea894175da24` | done |
| Same-session recall | `creature_d91bfcec108145efaac42ea86401f1f6` | done |
| Independent subagent read | `subagent_25295deda9cc4515bfd2dd97e9798caf` | done |

Each suffix has the prefix `e354c4ee98ae43dab31cea45bcaaf347_`.
The Creature session suffix is `session_4949008194284df49c6d1a5bc5efd7b9`;
the subagent session suffix is `session_4db2ffe18e9046bea073391a0f25727d`.
The follow-up user message omitted `CHATGPT-KT-DELEGATION-MEMORY-1`, which the
Creature nevertheless returned. The subagent's retained read result contained
`CHATGPT-KT-DELEGATION-1`, exactly matching `kt-chatgpt-delegation.txt`.
`probe.txt` still contained `CHATGPT-PROBE-1: hello from the web`.

Seven independent checks passed; machine-local evidence is saved in
`temp/mcp-delegation-live/chatgpt-first-pass-evidence.json`. No model was started
and no server configuration or workspace file was changed during this check.

The owner subsequently completed the [second-pass procedure](CHATGPT_DELEGATION_ACCEPTANCE.md)
through ChatGPT on the same instance. Another read-only public SDK check verified
nine retained-evidence assertions for session
`e354c4ee98ae43dab31cea45bcaaf347_session_8f1978db054c45e5a6f9170f2d8f93d9`:

| Operation | Job suffix | State |
| --- | --- | --- |
| Save memory | `creature_ed8231f464d148a48f8e1fc45da6e82e` | done |
| Cancel running Python | `creature_2c6ceddb5342435893bc706669362fee` | cancelled |
| Restore same conversation | `creature_01d99888f3fb4aaf92c578ced60cc8b0` | done |

All suffixes use the same instance prefix above. Retained activity shows
`python_ff0c3c73` starting in the foreground with `time.sleep(120)` and a
180-second timeout, then emitting `interrupted=true` / `final_state=interrupted`
before the delegated job's cancelled terminal event. Querying that cancelled
job succeeded as an MCP operation and returned `error="Delegation cancelled"`
as execution data. No supplemental input was recorded.

The restoration prompt omitted `CHATGPT-KT-CANCEL-MEMORY-2`; the restored turn
returned that token without any tool calls. The same session is now `closed`
with no active job. `probe.txt` still has its original exact content.

The owner's reported transient replies were `Session is busy`, `cancelled=true`,
and session state `stopped` immediately after cancellation. Those earlier replies
are attributed to the owner; the independent check verifies retained job/events
and the current closed state. Evidence is saved in
`temp/mcp-delegation-live/chatgpt-cancel-evidence.json`.

Both planned ChatGPT acceptance passes are complete. Together with the real-model
public SDK probes, they validate the first-release delegation workflow. The
previously documented automated e2e baseline failures and unmeasured coverage
remain separate limitations; this acceptance does not claim to resolve them.
