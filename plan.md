# Stable compact history: current contract and verification

## Scope

- Worktree: `.worktrees/stable-compact-prefix`; branch: `fix/stable-compact-prefix`.
- Base: `2923507c`. Committed slices: `ca5cc3b9` (checkpoint reconstruction and
  branch mutations), `564e36b7` (precise capture binding).
- Current work addresses the review of `564e36b7`, without commits, pushes,
  dependency changes or operations on private/live sessions.
- Earlier investigation and acceptance counts are historical, not evidence for
  the current changes. The old pre-rebuild tip remains in
  `backup/stable-compact-prefix-b0e39f31`.

## Continuous-use contract

- Editing an uncompressed tail retains its compatible summary. Editing consumed
  input excludes the dependent summary. No restoration executes old tools.
- Unknown input provenance stays unknown. A summary can inherit structural
  replacement coverage only from a proven compatible completion; unknown source
  does not imply either zero coverage or invented known provenance.
- Missing dependencies are restored as complete canonical rounds with uniquely
  owned settled results. Restored identities and placement must also be usable by
  the next compaction, not merely the next model request.
- Retained/re-attached user input and unconsumed results remain outside replacement
  coverage. Late settlement does not change the call's ordering identity.
- Preparation remains detached; installation occurs under the existing mutation
  guard. Refusals cannot cross a clear cutoff or mutate the rejected candidate's
  conversation, snapshots or selected branch. Configured budgets are not raised.

## Review fixes

1. **Repeated unknown summary:** capture now reuses the existing checkpoint
   completion proof. It validates identity, lineage, ordering bounds and consumed
   results, while preserving `compact_source_path=None`. Earlier completion
   markers whose coverage is already wholly represented are accounted for too.
   Canonical captures with no safe range skip before summarization regardless of
   source certainty; malformed source declarations do not become unknown provenance.
2. **Default job context:** producer records its exact job-status prefix in message
   metadata. Producer and binder share the content composition function; binding
   still compares the complete reconstructed input and checks branch/event identity.
   It does not strip guessed prefixes, accept substrings or bypass content checks
   for an explicit id. Captured and retained messages use the same relation.
3. **Repair identity and placement:** projected repairs name the canonical end, as
   normal replay does. Repair placement and capture share `resolve_message_bindings`
   instead of maintaining separate payload matching rules. Groups go before their
   later kept neighbours even when results settled late; repeated replies are
   disambiguated by the same neighbouring input identities as capture.
4. **Ambiguity scope:** only recovered providers need unique provider/job mappings;
   their result jobs still require unique ownership across the whole event log.
   Unrelated future sibling aliases do not reject a valid group. Off-path settled
   results of a retained ancestor call are not discarded.

`compact_lineage.py` only extracts existing path readers to remove the import
cycle between capture and checkpoint proof. No general provenance framework,
persisted event type, database migration or frontend behavior was added.

## Repository regression coverage

The existing `test_history_ops` workflow now includes:
- known S1 → unknown S2 → S3 → tail edit → branch round trips → cold resume;
- default `include_job_status=True` while a background job runs, followed by job
  settlement, compaction and existing replay/cold-history checks;
- public checkpoint repair → repeat edit → branch switch → compact → tail edit,
  with original tool execution count and provider pairing unchanged.

Existing unit fixtures cover canonical-end identity, unrelated alias versus
shared relevant-job rejection, ordering with late results/repeated replies,
text/multimodal prefix equality and tampering, retained-user exclusion, malformed
source rejection and known/unknown unproven-capture terminal behavior.

Observed red tests preceded the fixes. In particular the new repair→compact
workflow exposed an additional order reversal after the end-id fix; two placement
regressions then exposed settlement-based ordering and repeated-text ambiguity.
Those failures drove reuse of the capture resolver rather than new exemptions.

## Verification status

- Focused capture/repair/history workflow: **179 passed** before the final
  malformed-source cases. Their two new negative cases failed, then were fixed.
- Focused run including file-size guards: **2,384 passed, 1 failed** because the
  shared resolver file reached 610 lines. Its obsolete module rationale was reduced
  to a responsibility description; it is now 600 lines, without an allowlist change.
- First full run: **18,822 unit passed / 40 skipped**, **179 integration passed**,
  **84 e2e passed**, **2,553 Vitest passed** (277 files).
- Independent audit found an earlier-completion identity borrowing bug despite
  that green run. A public-range regression failed first; the proof now returns
  its selected completion id and capture checks the exact candidate id. Both
  cases and the existing history/proof tests pass (**114 passed**). Bounded
  independent re-audit found no unresolved blocker in this delta.
- Final full backend run after the identity fix: **18,824 unit passed / 40 skipped**,
  **179 integration passed**, **84 e2e passed**. Reports are in
  `temp/stable-compact-takeover/review-final-{unit,integration,e2e}.xml` in the main
  checkout. Frontend source is unchanged; **2,553 Vitest tests**,
  `npm run format:check` and `npm run build` passed in this review-fix pass.
- Black check over `src/ tests/`, Ruff and `git diff --check` passed. Black reports
  its Python 3.14/3.15 target safety warning; no formatter safety bypass was used.
  No file-size allowlist or test timeout was relaxed.
- Reviewer evidence scripts `composition-probes.py` and `job-banner-probe.py`:
  **4 passed** against the modified worktree. Initial launches failed on Windows
  path conversion and then missing project asyncio configuration; explicit
  Windows PYTHONPATH and `-c pyproject.toml` resolved test discovery/configuration,
  without editing the evidence scripts or assertions.
- Focused coverage run: **362 passed**, aggregate **95%**. Binding and repair are
  **96%** each, controller context **100%**; capture **94%**, prefix proof **91%**,
  extracted lineage readers **85%** in this selection. These are not full-suite
  coverage figures and do not replace the continuous-use assertions.

## Evidence limits

- No private incident database or full conversation was used or modified in this
  review-fix pass. Prior incident results remain historical evidence only.
- Historical banner-prefixed snapshots lacking captured prefix metadata are not
  guessed or migrated; ambiguous histories still refuse exact capture.
- Unknown-source summaries remain checkpoint-based, not generally eligible raw
  replay replacements. Structural inheritance does not establish their input source.
- Provider transport in repository workflows is scripted/MockTransport; successful
  framework admission does not guarantee an external provider accepts every prompt.
