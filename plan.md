# Stable compact history: current contract and verification

## Scope

- Worktree `.worktrees/stable-compact-prefix`, branch `fix/stable-compact-prefix`.
- Base `2923507c`; committed slices `ca5cc3b9`, `564e36b7`, `abf295a3`.
- Current uncommitted work addresses `REVIEW_abf295a3.md`: reduced checkpoint
  placement, internal metadata preservation, and default rerun after settlement.
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
without default rerun after settlement, then compaction. Its cold continuation
uses the recorded rerun ancestry. Unit tail-rerun tests cover text and multimodal
content, two successive retries, fresh event identity and single banner composition.
The earlier S1/S2/S3, repair→compact→edit and branch/cold workflows remain intact.

## Current evidence

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

## Evidence limits

- The original incident was checked at actual preparation/admission/installation,
  not a complete public edit/cold-resume against that private conversation.
- Reduced-round placement proves order, not arbitrary raw replacement coverage;
  success does not itself make every legacy reduced checkpoint compactable.
- Historical banner-prefixed messages without composition metadata are not guessed
  or migrated during capture. Strict capture remains conservative.
- Repository workflows use scripted providers/MockTransport; admission is not a
  guarantee that every external provider accepts the resulting request.
