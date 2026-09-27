# ChatGPT acceptance: local MCP delegation

The operator activates `probe_creature` and `probe_subagent` on the existing
fixed HTTPS endpoint. Keep the private connection URL unchanged. Refresh the
client's tool catalog if it still exposes only the original 13 tools; the
configured server exposes 19. Never paste the private capability URL into a
public report.

## First pass: execution and conversation ownership

Paste this request into a ChatGPT conversation with the KT MCP connector enabled:

```text
Actually call the KT MCP tools to run this acceptance test. Do not simulate tool
results or run the delegated work yourself. Never modify probe.txt or
kt-chatgpt-phase2.txt.

1. Call job_status without job_id and delegation_targets. Record instance_id and
   confirm probe_creature and probe_subagent exist. Read probe.txt; record its
   original text.
2. Call delegate with target=probe_creature. Ask it to remember the conversation
   token CHATGPT-KT-DELEGATION-MEMORY-1, read kt-chatgpt-delegation.txt if it already
   exists, then write it containing
   exactly CHATGPT-KT-DELEGATION-1 (no newline), read it back, then reply
   CREATURE-CHATGPT-OK. Save both job_id and session_id.
3. Use job_wait until terminal. Confirm state=done. Independently read the file
   with the direct MCP read tool and verify its exact content.
4. Call delegate again with the same target and session_id. Ask only:
   "Without reading files, repeat the conversation token I asked you to remember."
   Do not include the token in that prompt. Wait for completion and verify recall.
5. Call delegate with target=probe_subagent, without session_id. Ask it to read
   kt-chatgpt-delegation.txt, confirm CHATGPT-KT-DELEGATION-1, and reply
   SUBAGENT-CHATGPT-OK. It must not modify files. Wait for state=done.
6. Call delegation_history for the subagent session with view=conversation.
   Confirm the retained tool result contains the file content. Close both
   sessions with delegation_close, then confirm they are closed using
   delegation_sessions. Read probe.txt again and confirm it is unchanged.

Report only observed evidence: instance_id, both session IDs, each job ID and
terminal state, the created file content, recalled token, subagent output,
history verification and close results. If a tool is missing, stop and report
which tool; do not substitute direct execution for delegation. A wait timeout is
not cancellation. If a submit reply is lost, inspect jobs/sessions before retrying.
```

## Second pass: cancellation and explicit restoration

After the first pass, create a fresh Creature conversation and remember a new
token. Ask it to execute foreground Python that sleeps for 120 seconds, with a
180-second tool timeout to leave time for connector round trips. While it
is running, confirm a second delegate on the same session is rejected as busy.
Do not send supplemental input in this cancellation case. Observe `tool_start` in
session activity before cancelling with `job_cancel`.

Confirm the job is `cancelled` and the session is `stopped`. Explicitly call
`delegate` on that same session and ask for the token without supplying it.
Confirm preserved context and close the session. A cancelled job's error field
is execution data; a successful `job_status` query must not itself be treated
as a failed tool call.

Exercise `delegation_send` separately: KT input handling may promote the current
foreground tool to the background and finish the delegated turn after replying
to the supplemental input. A subsequent `job_cancel` on that already completed
turn correctly returns false. Close the session to stop its remaining background
work, or cancel a new active turn to exercise the full-instance stop path.

The public SDK probe covers this second pass too. The operator supplied actual
ChatGPT evidence for both passes on 2026-09-27; retained jobs and history were
independently checked through the public SDK. Both passes are complete; the
procedure below remains available for regression runs. See [results](DELEGATION_RESULTS.md).

```text
Actually call KT MCP for a separate cancellation-and-restoration acceptance test.
Do not modify any files or send supplemental input during the cancellation case.

1. Record instance_id. Start a fresh probe_creature session, asking it to remember
   CHATGPT-KT-CANCEL-MEMORY-2 and reply MEMORY-SAVED without using tools. Wait for
   this initial job to finish and save session_id.
2. Delegate again to that session: execute foreground Python exactly
   "import time; time.sleep(120); print('UNEXPECTED-FINISH')", with timeout=180
   and run_in_background=false. Save the new job_id; do not wait for it to finish.
3. Immediately attempt another delegate to the same session and confirm busy
   rejection. Read delegation_history events until the Python tool_start appears.
4. Call job_cancel on the running job. Record cancelled=true. Call job_status
   and delegation_sessions; confirm job state=cancelled and session state=stopped.
   Read history to confirm an interruption/error event for that Python tool.
5. Explicitly delegate again to the same session. Ask only: "Without reading
   files, repeat the original conversation token I asked you to remember."
   Do not include the token in that prompt. Wait for completion and verify recall.
6. Close the session, confirm closed, then read probe.txt and verify it still
   contains CHATGPT-PROBE-1: hello from the web.

Report actual IDs, busy response, cancellation result, job/session states,
underlying tool interruption, restored token and close result. If the slow job
has already completed before cancellation, report that honestly; it does not
count as a successful cancellation. Close the session even if a check fails.
```
