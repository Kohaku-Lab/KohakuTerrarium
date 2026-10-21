# Stable compact history: current contract and verification

## Scope

- Worktree `.worktrees/stable-compact-prefix`, branch `fix/stable-compact-prefix`.
- Base `2923507c`; committed slices `ca5cc3b9`, `564e36b7`, `abf295a3`, `5dbc76be`.
- Current uncommitted work fixes self-audit findings after `5dbc76be`: exact reload
  of reduced checkpoints and incomplete downstream regression coverage.
- No push, dependency change, live-session operation or budget increase. The
  frozen incident evidence is read offline; no private content is copied into tests.

## Contract

- Editing an uncompressed tail retains its compatible summary. Editing consumed
  input excludes the dependent summary. Restoration never executes old tools.
- Unknown input provenance stays unknown. Structural replacement coverage is
  inherited only from a proven compatible completion, with exact completion identity.
- Missing dependencies are restored as complete canonical rounds and uniquely
  owned settled results; original canonical identities and ordering are retained.
- **Placement is not coverage:** a checkpoint may legitimately omit calls whose
  results its retained summary explicitly consumed. Such a reduced round must not
  be expanded or rejected merely because it differs from complete raw history.
- Placement may use a detached full-canonical presentation only after exact
  remaining-payload equality, eligibility and end-identity checks, and only for
  calls whose dispatch identities were validated by the retained summary proof.
  This presentation is never installed or returned as newly consumed coverage.
  The capture resolver's full-payload checks remain unchanged.
- Persisted internal message metadata survives placement conversion; it does not
  become provider wire. Event identity, job composition, turn/branch and settled
  result identity must not disappear at this boundary.
- Rerun reads the original input associated with the post-refresh retained message,
  using the producer's existing exact content-composition relation. New branch
  persistence returns its user-event identity and the retained message adopts it.
  Unrecorded live wording is re-recorded without composing a banner twice.
- Clear cutoffs, branch lineage, uniquely owned results and configured admission
  limits remain enforced. Preparation stays detached; no alternate raw path can
  resurrect cleared or explicitly consumed records.

## Implementation boundaries

- `snapshot_history.py` supplies only proof-validated consumed dispatch identities,
  including validated consumption from known-source summaries.
- `snapshot_repair.py` preserves internal metadata and recognizes the bounded
  consumed-call reduction. Existing canonical round readers and strict binder
  still validate identity/order. Unrelated identical bodies cannot override an
  explicit canonical end identity.
- `agent_message_history.py` owns recorded-input lookup; `agent_messages.py` invokes
  it after settled-prefix restoration. `append_user_event_pair` returns the new
  persisted `user_message` id without adding writes or event types.
- No generic provenance system, database migration or new frontend behavior.

## Regression coverage

Existing snapshot fixtures now exercise:
- proven reduced body retained while a different missing round is restored;
- no consumed call/result resurrection and repeated preparation without growth;
- known/unknown source, wrong end identity, changed body, absent/foreign proof;
- identical body elsewhere with an unambiguous recorded end;
- job-context and repeated-input identities surviving internal conversion.

The existing background-tool block in `test_history_ops` now covers both with and
without default rerun after settlement, then compaction. Both scenarios now execute
cold continuation, editing, branch switching and another cold resume. Unit tail-rerun tests cover text and multimodal
content, two successive retries, fresh event identity and single banner composition.
The earlier S1/S2/S3, repair→compact→edit and branch/cold workflows remain intact.

## Verification recorded for 5dbc76be

- Red-first: review metadata probe failed both cases; reduced-round preparation
  and rerun tests failed before their fixes. Independent audit's duplicate-body
  finding failed both source variants, then passed after identity-first filtering.
- Original frozen incident, watermark **60882**, target **60870 / 113 / 1**:
  **239 original → 280 candidate/final messages**, **80,523 text tokens**, unchanged
  **256,000** budget, **round 11** retained. Preparation leaves conversation/state/
  events unchanged; installation preserves Conversation identity; repeated prepare
  produces the same messages. No active database or external provider was used.
  Output: `%TEMP%/kt-rereview-fix-check-uh_nmfgw/incident-current.json`.
- New review probes plus previous composition/job probes: **8 passed**, unchanged
  scripts against current source with explicit Windows PYTHONPATH and project
  pytest configuration.
- File-size guard initially caught `agent_messages.py` at 631 lines. Recorded-input
  lookup was moved into the existing history-lookup module. No allowlist change.
  Guard + snapshot/agent-message unit checks: **2,414 passed**.
- Black check over `src/ tests/`, Ruff and `git diff --check` passed. Black retains
  its Python 3.14/3.15-target warning; no safety bypass was used.
- Final bounded independent review found no additional confirmed defect after the
  duplicate-body correction. It was read-only and did not rerun test tiers.
- Current full backend run: **18,840 unit passed / 40 skipped**, **179 integration
  passed**, **84 e2e passed**. Reports: main checkout
  `temp/stable-compact-takeover/rereview-{unit,integration,e2e}.xml`.
- Initial Vitest run with four workers: **2,551 passed / 2 timed out** at the
  unchanged palette scan and dynamic panel import (5 seconds). Isolated checks:
  **9 passed**; complete single-worker run: **277 files / 2,553 passed**.
  No assertions, timeouts, dependencies or frontend code changed. Reduced resource
  contention is consistent with the results but is not a proven root cause.
  Frontend `format:check` and production build passed. Build warnings include
  component naming conflicts, mixed static/dynamic imports and large chunks.
- Commit preflight repeated snapshot/agent-message units and the history integration
  workflow: **211 passed**.

## Self-audit after 5dbc76be

Confirmed findings and corrections:

1. **Continuation regression:** targeted restoration accepted the reduced checkpoint,
   but saving it and reloading the exact path provided no consumption proof. The
   settled-result check rejected it and raw fallback revived consumed calls/results.
   The relevant exact-reload gap predated the patch, but accepting this new
   representation exposed it. The earlier repeated-prepare assertion did not test
   this transition. The extended existing success case failed all four variants
   before the correction.
2. **Integration coverage regression:** the False/True loop ended before cold resume,
   overwriting the first scenario's saved path. Moving the existing downstream block
   into that loop restores both journeys without adding a new function or matrix.
3. **Weak rerun fixture:** its first retry lacked production turn/branch metadata,
   so it entered the unrecorded fallback. The same text/multimodal cases now carry
   real recorded identity and job context, verify two new branches, and reject a
   stale branch identity without changing messages, events or dispatch count.

The production fix is limited to `snapshot_history.py`: exact-path proof uses the
exclusive path-end boundary and a watermark-bounded completion. Only validated
consumption exemptions transfer to exact reload; unknown-source coverage widening
remains restricted to the pre-existing nonexact path. Missing or invalid proof
cannot lend consumption. Legacy exact checkpoints do not acquire a new mandatory
proof requirement. No capture checks, raw replay rules or budgets were relaxed.

Test size is kept bounded: no new test functions, helpers or files in the tracked
patch. Existing success cases add persistence → exact reload → sibling preparation
with full-message equality; existing proof negatives add exact reload and one
future-result case for each source variant. This adds **two collected unit cases**,
not another broad matrix. The integration diff is primarily indentation. Assertions
about metadata, nonmutation, canonical identity and strict-capture refusal remain
because they protect different contracts.

Verification on the final uncommitted code:
- Full unit: **18,842 passed / 40 skipped**; integration: **179 passed**;
  e2e: **84 passed**; Vitest: **277 files / 2,553 passed** (single worker).
- Reports: `temp/stable-compact-takeover/selfaudit-{unit,integration,e2e}.xml`
  and `selfaudit-vitest.json` in the main checkout; detailed logs alongside.
- Black, Ruff, file-size guard and diff whitespace checks passed. Black's existing
  Python 3.14/3.15-target warning remains; no safety bypass.
- Unmodified external review probes: **8 passed**.
- Frozen incident remains **239 → 280**, **80,523 text tokens / 256,000 budget**,
  round 11 retained, preparation nonmutating, installation identity-preserving and
  repeated preparation idempotent. Output:
  `%TEMP%/kt-selfaudit-incident-f_5pf4u0/incident-current.json`.
- Final independent static review found no further confirmed defect; it did not
  itself execute tests. Its two noted negative-test gaps (invalid consumed identity
  and stale retry identity) were subsequently covered without changing production.

The methodological correction is to test a newly accepted representation through
its next consumer, not merely to repeat the same preparation or cite full-suite
pass counts. Next review should continue to check prepare → persist → reload →
mutation transitions, with unknown provenance left unknown.

## Evidence limits

- The original incident was checked at actual preparation/admission/installation,
  not a complete public edit/cold-resume against that private conversation.
- Reduced-round placement proves order, not arbitrary raw replacement coverage;
  success does not itself make every legacy reduced checkpoint compactable.
- Historical banner-prefixed messages without composition metadata are not guessed
  or migrated during capture. Strict capture remains conservative.
- Repository workflows use scripted providers/MockTransport; admission is not a
  guarantee that every external provider accepts the resulting request.
