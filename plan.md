# Stable compact history: current status and convergence plan

## Scope and baseline

- Original implementation base: `11f0244d`; rebuilt base: `2923507c` (updated
  `origin/main`). Worktree: `.worktrees/stable-compact-prefix`.
- User authorized rebuilding `fix/stable-compact-prefix` and consolidating its
  twelve commits. No push, dependency change, or live-session operation is authorized.
- Original tip `b0e39f31` is preserved by `backup/stable-compact-prefix-b0e39f31`.
- Rebuilt history has two slices: checkpoint reconstruction/branch mutations
  (including convergence), followed by exact compaction capture binding.
- Replay had no textual conflicts. The combined `session/history.py` retains
  main's call-local `SelectionCompatibility` and this branch's compact-rule selection.
- First consolidated slice focused checks: **267 passed**. Black/Ruff and staged
  diff checks pass. Rebuilt-tree full regression: **18,804 unit passed / 40 skipped**
  (423.27s), **179 integration passed** (178.75s), **84 e2e passed** (240.45s).
  Independent merge-semantics review found no conflict or integrity issue. Frontend
  dependencies remain absent in this worktree; Vitest was not run.
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

## Slice 2 plan and acceptance

- [x] Reproduce through real producer messages: added the long-tool journey to the
  existing `test_history_ops`. Red at summary missing from replay; no event-ID hacks.
- [x] Bind captured user messages, complete canonical assistant rounds and tool
  results to recorded identities. Freeze capture and retained tail at one boundary;
  reject ambiguity instead of broadening spans heuristically.
- [x] Preserve retained/re-attached user and live tool rounds; cover inheritance
  through subsequent compaction and branch compatibility without changing cutoff proof.
- [x] Add focused negative tests for conflicting/duplicate identities, missing
  canonical boundaries, incomplete results and capture-time concurrent appends.
- [x] Verify public edit request, actual branch switches, cold resume, no old tool
  execution and intact provider call/output pairs in the existing history workflow.
- [x] Complete frozen-source full tiers and final independent diff audit.

Design constraint: reuse canonical round identities and existing compact coverage
where possible. Do not add a generic provenance layer or bypass replay proof.

Implementation and evidence:
- `compact_binding.py` resolves message identities; `compact_capture.py` owns
  inherited coverage and interval validation. This split keeps both below 600 lines;
  no producer protocol, persisted schema, migration or replay-rule change was added.
- Captured/retained messages are copied before the store read. Known-source canonical
  captures without a safe range emit `capture_unproven`, without a summarizer call,
  counter increment, splice or snapshot install. Legacy non-canonical compatibility
  is retained; malformed canonical records never opt into that fallback.
- Pending ACKs cover only their dispatch; folded results cover uniquely named jobs.
  Settled results may originate on a now-unselected consuming branch, preserving
  the existing dispatch-based restoration contract. Inherited late results do not
  expand an interval across a retained tail.
- Unknown-origin no-job injections add no event coverage and leave source unknown;
  any unrepresented persisted event inside the span still blocks it. Checkpoint-only
  unknown summaries continue through the first-slice proof, not a fabricated source.
- Audit additions were red-first: malformed canonical fallback, duplicate IDs,
  reordered rounds, missing summary inheritance, conflicting pending identifiers,
  and trailing reasoning beyond the last captured canonical end. Exact restated
  reasoning is consumed with its canonical message; arbitrary reasoning is not noise.
- Formal long-tool workflow uses real producer records and MockTransport, not hand-
  stamped IDs. Both half-cap and token-tightened boundaries consume calls0..2, keep
  calls3..5 paired, and preserve a re-attached goal only for the tightened boundary.
  Public edit makes one new request; switches/resume make none and tools stay at six.
- Cold standalone resume has a different runtime-owned system prompt from the warm
  engine. Assertions retain full dictionary/metadata equality for history and assert
  the cold system prompt itself is unchanged by switching; they do not strip metadata.
- Focused compact + history workflow + file-size checks: **2,368 passed**. Black,
  Ruff and diff checks pass (existing Python/Black target warning remains).
- First full run: unit **18,657 passed / 40 skipped**, integration **178 passed /
  1 failed**, e2e **84 passed**. The integration failure was real: Antigravity's
  cold compact crossed a `scratchpad_write`, a non-conversational record incorrectly
  treated as unproven content. Six operational record types now follow replay's
  explicit non-content handling; their new parameterized cases failed before fixing.
  Focused capture + multimodal workflow: **73 passed**. No replay safeguards removed.
- Independent final source audit found no confirmed P1/P2 blockers; the six-type
  follow-up was also source-reviewed. The reviewer raised an unproven contradictory
  retained-result/inherited-result case outside the primary interval; no reachable
  production history established it, so it remains a documented uncertainty.
- Final frozen-production full rerun: **unit 18,663 passed / 40 skipped** (370.69s),
  **integration 179 passed** (161.61s), **e2e 84 passed** (244.86s), all exits zero.
- Coverage closeout added eleven identity-rejection/compatibility cases without
  production changes: capture + branch unit **96 passed**, integration history/compact
  **2 passed**. Measured binding coverage **96%** (348 statements / 13 missed), capture
  **95%** (238 / 11). Remaining defensive guards are documented, not excluded.
- Final unit-tier recheck after those test-only additions: **18,674 passed /
  40 skipped** (377.73s). Black initially flagged the new test formatting; formatted
  that file, then full Black/Ruff/diff checks and the unit tier passed. Integration
  and e2e production code was unchanged after their green runs. No commit or push.

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

## Historical second-slice failure and separate work

1. **Baseline failure, now covered by slice 2's formal regression:** on `c4f27f41`,
   the anonymous live long-tool probe
   still fails: capture7, result IDs10/15/20, replacement range absent; a fresh edit
   loses the summary and restores consumed text, while old tool executions stay6.
   Probe: main checkout `temp/stable-compact-takeover/test_takeover_probes.py`,
   `test_live_long_tool_capture`. That old suite did not cover this contract;
   `tests/helpers/compact_capture_history.py` now anchors it in `test_history_ops`.
2. Investigate the Drive-store race separately if needed; no Drive changes here.
3. Publication or further commits require explicit user authorization.
