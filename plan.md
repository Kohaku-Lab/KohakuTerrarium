# Stable compact history: current status and convergence plan

## Scope and baseline

- Implementation base: `11f0244d`; convergence baseline: `c4f27f41` on
  `fix/stable-compact-prefix`, in `.worktrees/stable-compact-prefix`.
- Slice 1 (proven legacy-checkpoint reuse/whole-round repair) and `/branch`
  are committed. Slice 2 (precise capture binding for new compactions) is not done.
- Current status: behavior-preserving convergence implemented and verified.
- The user authorized a separate convergence commit after verification. No pushes,
  history rewrites, dependency changes, or live-session operations are authorized.
- Earlier chronological investigations remain available in `c4f27f41:plan.md`.
  Their preliminary counts and completion claims are not current acceptance evidence.

## Invariants to preserve

- Reuse requires compatible ancestry and proven coverage; unknown provenance alone
  proves nothing. Restore complete canonical rounds with uniquely owned results,
  never a covered/cutoff-crossing round or a partial call group.
- A rejected cutoff-bearing candidate cannot fall back past its cutoff.
- Reconstructed prompts pass retention/context admission before installation;
  ordinary continuation and unexpanded reruns do not acquire an extra admission gate.
- Keep system prompts, structured messages, conversation identity, branch counters,
  compact state and persisted selection coherent. Restoration does not execute tools.
- Preparation must not install state. Installation is explicit and occurs under the
  existing caller's history-mutation guard; branch-specific elision remains distinct.

## Convergence checklist

- [x] Make the ambiguous-owner negative reach the production repair call. The old
  fixture bypassed it; the corrected fixture reaches gap10/target12 and admits only
  when its second conflicting owner is removed in an in-memory mutation probe.
- [x] Deep-copy materialized event/state payloads in refusal snapshots, preserving
  conversation identity separately; pin refusal categories instead of any error.
- [x] Merge duplicate round/agent constructors, cutoff staging, threshold and
  final-prompt cases; remove the repeated bare-display case and deepcopy tautology.
- [x] Remove the unused previous-branch wording lookup and its seven helper tests.
  Keep current regenerate wording, canonical-target and branch-isolation tests.
- [x] Replace the production raw-reload compatibility wrapper with direct preparation
  and installation in its test harness; no production callers remained.
- [x] Make settled refresh return an admitted detached candidate or `None`. Remove
  the shadow agent/controller/compact-manager copies and serialization comparisons.
- [x] Make `prepare_persisted_edit` pure; `PreparedEdit.install` consumes its candidate
  explicitly at the guarded caller. Raw and branch candidate representations retain
  their distinct installation/elision policies, not a new generic framework.
- [x] Share the summary-dependency predicate and non-message state installer.
- [x] Use a real Conversation in mid-turn append tests; retain the event-origin tests.
- [x] Finish focused/full-tier verification and independent diff audit.
- [x] Confirm public history workflows preserve request content, branch/cold
  restoration and no-tool-rerun behavior; archived-copy public-entry checks also pass.

## Verification

Current convergence run:
- Red-first: target and branch edit preparation changed live state before explicit
  installation (2 failures); four settled-result scenarios also exposed mutation.
- Corrected fingerprint materializes the real store's payloads before deepcopy;
  copying the table adapter itself initially failed and was corrected.
- Focused production/test convergence check: 178 passed, including public history
  integration. Independent source/diff audit found no blocker; no coverage/proof/
  repair/capture algorithm changes. Black, Ruff and diff checks pass.
- In-memory probes now detect both checkpoint-message and event-payload mutations
  that the old shallow refusal fingerprint missed.
- Current reduction excluding this plan: production -92 lines; tests -97 lines.
  No modules added; candidate-specific elision behavior remains explicit.
- Final sequential tiers: unit **18,611 passed / 40 skipped** (410.52s), integration
  **179 passed** (179.61s), e2e **84 passed** (235.92s); all exit codes zero.
  Unit cases decreased by eight: seven tests of the removed helper and three
  redundant cases removed/merged, with two explicit preparation-purity cases added.
- Post-refactor archived COPY verification: public edit emitted one mocked provider
  request and completed the reply, no execution error, no tool executions, unchanged
  256,000 budget. Actual branch commands and cold resume still match the pre-refactor
  full dictionaries: original239/digest65909c8b7a97356d and edited261/6e48d8faaa52375e.
  All 33 harness checks pass; source/archive hashes unchanged. Private verification
  report: `convergence-report.json` beside the existing temporary harness, not tracked.
- This closes the bounded convergence task only. Slice 2 remains open below;
  no commit, push, or new dependency installation was performed.

Last committed baseline (`c4f27f41`):
- Unit: 18,619 passed / 40 skipped; integration: 179 passed; e2e: 84 passed.
- Black, Ruff and diff checks passed; Black warned about Python 3.14 versus its
  3.15 target. Frontend dependencies were absent, so Vitest did not run. The three
  sessions guides have pre-existing whole-file Prettier failures.
- Earlier unit Windows PermissionError and multi-node timeout passed later checks.
  Drive graph merge previously failed both a full e2e run and isolated recheck with
  `UNIQUE constraint failed: drive_meta.key`; the final run passed without Drive
  changes. This is non-recurrence, not a claimed Drive fix.

## Incident evidence and limits

- Isolated archived-copy public edit/rerun: one new mocked provider wire request,
  completed reply, no execution error and no old tool execution. Original configured
  context budget 256,000 was retained, not increased.
- Actual `/branch 113 1` / `/branch 113 4`, repeated: 239 / 261 messages, respectively.
  Both cold resumes matched complete dictionaries including metadata; source hashes
  stayed unchanged. These were commands, not substituted private reload calls.
- Edited provider request had 244 input items; that is not the same representation
  as 261 conversation messages. Prior 260/279 candidate counts used different setups;
  the old 263/239 harness did not prove a generated rerun and is not acceptance evidence.
- Private DBs/pickles and request payloads are not committed. Anonymous public history
  integration and local MockTransport fixtures are the reproducible repository proof.

## Deferred work (not part of convergence)

1. **Slice 2:** bind captured user/assistant/result messages to canonical events,
   preserving the unconsumed tail. On `c4f27f41`, the anonymous live long-tool probe
   still fails: capture7, result IDs10/15/20, replacement range absent; a fresh edit
   loses the summary and restores consumed text, while old tool executions stay6.
   Probe: main checkout `temp/stable-compact-takeover/test_takeover_probes.py`,
   `test_live_long_tool_capture`. Full-suite green does not cover this open contract.
2. Investigate the Drive-store race separately if needed; no Drive changes here.
3. Publication or further commits require explicit user authorization.
