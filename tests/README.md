# Test suite

`tests/` has three tiers (`unit/`, `integration/`, `e2e/`), each a
*different shape of test*, not just a different size. This file is the
source of truth for what each tier is and how to write them. Read it
before adding or reviewing any test.

## Running the tests

```bash
# Unit tier: fast, per-file. The file-size + dep-graph guards have
# their own CI jobs, so exclude them from the everyday run.
pytest tests/unit/ -q --ignore=tests/unit/test_file_sizes.py --ignore=tests/unit/test_dep_graph_lint.py

# The guard suites (run them too before pushing)
pytest tests/unit/test_file_sizes.py tests/unit/test_dep_graph_lint.py -q

# Integration + e2e tiers: workflow / journey tests, slower
pytest tests/integration/ tests/e2e/ -q

# Everything
pytest tests/ -q

# Coverage for one tier
pytest tests/unit/ -q --cov=src/kohakuterrarium --cov-report=term-missing

# One focused test: skip the worker spawn
pytest tests/unit/core/jobs/test_executor.py::TestSerialLock -n 0
```

`pytest` is configured in `pyproject.toml` with `asyncio_mode = "auto"`
(no `@pytest.mark.asyncio` needed) and a per-test timeout.

### Parallel runs

Every run is parallel through pytest-xdist (`addopts = "-n auto --dist
loadgroup"`). `-n auto` resolves in `tests/conftest.py`:
`KT_TEST_WORKERS` when set, otherwise `min(16, cpu_count)`, never above
64. Measured on an 86-core machine: 16 workers 204 s, 32 workers 251 s,
64 workers 306 s (the suite is I/O- and startup-bound past 16). On a 4-vCPU
hosted runner the auto value (4) is the measured best: unit 573 s serial →
199 s, integration 204 s → 118 s; 8–16 workers are no faster.

What keeps tests independent across workers:

- Every test gets a private root (`kt-<uuid>/`): `HOME`, `USERPROFILE`,
  `XDG_*`, `TMP`/`TEMP`/`TMPDIR` and `tempfile.tempdir` point inside it,
  and `KT_CONFIG_DIR` points into the test's `tmp_path`. Child processes
  inherit all of it. The operator's real home is only readable through
  `tests/helpers/kt_biome.real_home()` (captured before redirection).
- Tests that share one real resource carry `@pytest.mark.xdist_group(...)`
  and run in order on one worker: `ports` (probe-a-free-port-then-reuse),
  `mcp-subprocess` (real `mcp-serve` children), `browser` (real Chromium:
  `tests/integration/test_browser.py`, `tests/unit/browser/test_driver.py` and
  the Chromium probe tests in `tests/unit/browser/test_hidden.py`),
  and the whole e2e tier (`e2e`, added automatically). Everything else is
  distributed per test.
- "The event loop was not blocked" is witnessed by
  `tests/helpers/loop_witness.py` (the loop runs a callback while the
  operation is in flight), never by wall-clock gaps. Ordering and
  overlap are asserted with counters or events, not elapsed seconds.
  A new test that bounds elapsed time is a check on the machine, not the
  design.

### Browser-backed tests

`tests/integration/test_browser.py`, `tests/unit/browser/test_driver.py` and
the page-probe tests in `tests/unit/browser/test_hidden.py` run real Chromium
through Playwright against a loopback fixture site
(`tests/helpers/browser_site.py`, pages in `tests/fixtures/browser/site/`).
They need the `browser-use` extra and Playwright's Chromium:

```bash
uv pip install -e ".[dev,browser-use]"
playwright install chromium
```

Without Playwright or Chromium they skip; any other launch failure fails.
They read Playwright's browser cache from the operator's real home
(`KT_TEST_REAL_HOME`; `%LOCALAPPDATA%` on Windows) unless
`PLAYWRIGHT_BROWSERS_PATH` is set. CI does not install Playwright, so there
they skip. Every other browser test drives the session through the scripted
driver in `tests/helpers/browser_driver.py` and needs no browser.

**CI policy (`.github/workflows/ci.yml`).** Unit and integration tiers
run on the full OS × Python matrix (3.12+ on Linux / macOS / Windows).
**The e2e tier is NOT run in CI.** It spins up real WebSocket-backed
lab clusters, subprocess workers, multi-node session mirrors, and
Vue-frontend-style HTTP/WS journeys; the resulting timing depends on
hosted-runner network + scheduler behavior that is too volatile to
gate every PR on. E2E is the developer surface for reproducing real
user behavior locally; run it before shipping anything that touches
the multi-node / Studio / serving stack. Bug anchoring and regression
protection on `main` come from the unit + integration tiers.

## The three tiers: what each one actually is

- **unit = one source file → one test (or test-class).** Test an
  individual class / method against its real dependencies (deterministic
  stubs only for genuine I/O). Shape checks (`isinstance`, `key in
  dict`, `is not None`) are legitimate **here and only here**. Goal:
  95–100% line coverage per core-lib file; any sub-95% file needs a
  written justification in `temp/BUGS.md`.

- **integration = one core-lib folder → one test-class.** Each test
  method runs a **complete feature workflow end-to-end in a single
  function**. For example, for `session/`: *initialize a session → run a
  creature that writes stuff → record turns → read it back → resume from
  it → verify*, all in ONE test. Splitting that into "init" / "write" /
  "read" / "resume" as separate tests is unit-tier thinking and
  structurally cannot catch a bug that only manifests across the
  sequence. The integration test for a folder **is the most
  comprehensive usage example of that folder**.

- **e2e = whole project → a handful of fat journey tests.** Each test is
  a single function simulating an entire user session: chat, change
  model, toggle plugins, adjust settings, interrupt, resume, branch,
  search, *all in one test*. There are deliberately few: roughly
  `{programmatic, HTTP+WS} × {creature, terrarium, studio}` + multi-node
  variants ≈ 10 tests covering the whole framework. e2e answers one
  question: *is the system runnable, end to end?*

## The discipline rules (every tier)

1. **Behavior asserts, not shape asserts.** A test that calls a mutation
   MUST observe the side effect: exact state change, event fired, file
   written, message delivered, exact return value. `assert status in
   {200, 400}` is forbidden. (Shape checks are the *exception* allowed at
   the unit tier only; see above.)

2. **Use real collaborators, not mocks.** The only legitimate seam is a
   genuine external dependency: the LLM (use
   `kohakuterrarium.testing.llm.ScriptedLLM`. **Prefer direct
   injection**: every construction entry point takes the instance via
   `llm=`, e.g. `Agent.build(cfg, llm=ScriptedLLM([...]))` /
   `engine.add_creature(path, llm=...)`. Where the framework builds a
   provider itself (config files, resume, recipes, model switch,
   compaction, summaries), the autouse seam from
   `kohakuterrarium.testing.llm_seam` already routes every factory in
   `LLM_FACTORY_TARGETS` to `ScriptedLLM`; a test that needs its own
   doubles calls `install_llm_seam(monkeypatch, factory)`. Under the seam
   constructing a real provider raises `RealProviderBlocked`: a test
   that drives a provider class against a fake transport marks itself
   `@pytest.mark.real_llm_provider`, or wraps just that construction in
   `with real_llm_providers():`. Non-loopback sockets are blocked
   (`@pytest.mark.allow_network` lifts it) and HOME, CODEX_HOME and
   KT_CONFIG_DIR point at tmp), the filesystem (`tmp_path`), the
   network. **Never invent a method on a fake that the real class does
   not have**; that bakes a bug in as the expected behavior. Use the
   real engine, real `SessionStore`, real `InProcTransport`, real
   `TestClient`.

3. **Integration tests must mirror how the codebase actually uses the
   folder.** The test is not just "the shape of how this system should
   work"; it is also "how this system *should be used*". After an
   integration test is green, ask: *does the rest of the codebase drive
   this folder the same way my test does?* If there is a usage paradigm
   the test doesn't reproduce, either extend the test to cover it, or,
   if that usage path is actually broken, file it as a bug. This is why
   integration tests are meaningful: they pin the real contract between
   layers, not a parallel test-only wiring. (Concretely: the
   `laboratory/` integration test builds `MultiNodeTerrariumService`
   exactly as `cli/serve.py` does, not a hand-wired host/client rig.)

4. **No global state leaks.** Tests that mutate a module-global registry
   (`api.deps._service`, `llm.profiles.api_keys._resolver`, …) must snapshot +
   restore; the `isolate_global_state` autouse fixture in
   `tests/conftest.py` handles the known ones. (Studio session
   bookkeeping is no longer module-global: it lives per-engine/service
   in `studio/sessions/registry.py`; reach it via
   `lifecycle.meta_for(service)` / `lifecycle.stores_for(service)`.)

5. **To raise integration/e2e coverage, fatten, don't multiply.** When
   a workflow/journey test doesn't reach far enough, add more operations
   to the *existing* test function. Do NOT add more test functions;
   that drifts back toward granular per-method tests, which is the
   unit tier's job.

6. **Bugs surface as a failing assertion, then a regression guard.** A
   test that reveals a bug pins it: `xfail(strict=True, reason="BUG:
   ...")` for a whole test, or an inline `# BUG:` assertion for a single
   point inside a fat workflow. The bug is logged in `temp/BUGS.md`.
   Fixing the bug must flip the pin to assert the *correct* behavior;
   that's the regression guard. A test that *can't* surface bugs
   (because it only re-asserts current behavior) is worthless.

## Integration tier: one test-class per folder

`tests/integration/test_<folder>.py`, each a single test-class whose
methods are **full feature workflows** (one workflow = one function).
Each mirrors how the **real consumer** drives that folder.

| File | Folder | The workflow it runs | Real consumer it mirrors |
|---|---|---|---|
| `test_core.py` | `core/` | build an `Agent` → inject input → controller loop → direct + background tool dispatch → sub-agent dispatch → output routing → compaction → termination → interrupt → history ops | `terrarium/creature/host.py`, `bootstrap/agent_init.py` |
| `test_bootstrap.py` | `bootstrap/` | `Agent.from_path` on a real on-disk config → every factory fires → run a turn proving the wiring is live | `terrarium/factory.py`, `core/agent.py.__init__` |
| `test_session.py` | `session/` | init store → creature writes events → record turns → read back → close → resume → verify → memory search → fork → migration | `terrarium/persistence.py`, `studio/sessions/lifecycle.py`, `session/resume/resume.py` |
| `test_terrarium.py` | `terrarium/` | build graph → add creatures → wire channels → broadcast → privileged node mutates → auto-merge/split → session lineage → hot-plug → recipe apply: driven through `LocalTerrariumService` | `studio/sessions/lifecycle.py` |
| `test_laboratory.py` | `laboratory/` | real host+client over `InProcTransport`, wired as `cli/serve.py` does → `MultiNodeTerrariumService` → spawn-on-worker → remote chat → remote ops → deploy → session sync | `cli/serve.py` boot dispatch, `api/deps.py` |
| `test_studio.py` | `studio/` | `Studio` façade → catalog → editors create workspace creature → sessions start+chat → persistence save/resume/fork → identity → attach | `api/routes/*`, `cli/*` |
| `test_api.py` | `api/` | `create_app()` + `TestClient` → create session → WS chat → settings → history → hot-plug → save/resume → delete | frontend `utils/api.js` call sequence |
| `test_modules.py` | `modules/` | load real plugin/tool/trigger/subagent/output/user_command modules into a real `Agent` → exercise each protocol through a real turn | `bootstrap/*` loads, `core/agent_*` invokes |
| `test_prompt.py` | `prompt/` | assemble a real agent's system prompt through the aggregator → on-demand `##info##` skill load → template rendering | `core/agent_init`, `bootstrap` |
| `test_parsing.py` | `parsing/` | feed real streamed LLM output through `StreamParser` across chunk boundaries → exact tool-call/command/text events | `core/controller/controller.py` |
| `test_commands.py` | `commands/` | a real controller turn emits `##info##` / `##read##` → command resolves docs/skills into the conversation | `core/controller`, `core/agent_handlers` |
| `test_mcp.py` | `mcp/` | real in-process stdio MCP server → connect → list → call → disconnect, cross-task as `core/agent/mcp.py` drives it | `core/agent/mcp.py` |
| `test_compose.py` | `compose/` | build real `>>` / `&` / `\|` / `*` pipelines over engine-backed runnables and run them | user code (compose is a public API) |
| `test_serving.py` | `serving/` | `KohakuManager` + `AgentSession` → create session → stream chat → lifecycle | `api/`, `compose/` |
| `test_packages.py` | `packages/` | install a local package → list → info → resolve a `pkg:` ref → uninstall | `cli/packages.py`, `terrarium/config.py` |
| `test_browser.py` | `browser/` | a real `Agent` from an on-disk config with the `browser` plugin and a native-tool-call `ScriptedLLM`, against the loopback fixture site through real Chromium and the real proxy → navigate → snapshot → act batch + delta → dialog → new tab → upload → download → screenshot → eval → blocked egress → stale ref → interrupt → runtime disable / enable → stop, no Chromium left; and a second workflow with no plugin and no agent: a session reads pages under an external check → refused redirect and loopback URL → download-only URL → `async with` exit, no Chromium left → the one-shot `read_page` | `builtins/plugins/browser/plugin.py` + the `browser_*` tools, toggled via `terrarium/creature/ops.py`; a component such as `web_fetch` using `kohakuterrarium.browser` directly |
| `test_llm.py` | `llm/` | define profiles/presets → save/load the store → resolve a provider config → build tool schemas + messages → api-key round-trip (live providers excluded) | `bootstrap/llm.py` |

`utils/` has no standalone cross-folder workflow; its helpers are
exercised *through their consumers* (`file_guard` / `file_walk` via real
tool calls in `test_core.py` / `test_modules.py`).

## E2E tier: a handful of fat journey tests

`tests/e2e/test_<journey>.py`, each a single test function simulating an
entire user session. One per natively-different usage variation.

| File | Journey (one fat test) |
|---|---|
| `test_prog_creature.py` | programmatic: one creature: multi-turn chat → tool calls → sub-agent dispatch → change model → toggle plugin → adjust setting → interrupt → resume → branch/regenerate → memory search |
| `test_prog_terrarium.py` | programmatic: recipe apply → multi-creature graph → channel broadcast → privileged node mutates the team → hot-plug → auto-split → stop → resume |
| `test_prog_studio.py` | programmatic: Studio: catalog → create workspace creature → start → chat → save → list → resume → fork → viewer → delete |
| `test_api_creature.py` | HTTP+WS: `TestClient`: create session → WS chat stream → model/plugin endpoints → interrupt → history/branch → resume |
| `test_api_terrarium.py` | HTTP+WS: create terrarium → runtime-graph WS → channel send → hot-plug via API → topology endpoints |
| `test_api_studio.py` | HTTP+WS: workspace CRUD → catalog → saved-session list/viewer/diff/resume/fork → attach |
| `test_api_settings.py` | HTTP+WS: the configuration surface: identity/profiles/keys, MCP registry, default-model, UI prefs, native-tool & plugin options |
| `test_multinode_journey.py` | multi-node: lab-host + subprocess lab-client → spawn-on-worker → cross-node chat streaming → remote ops → session sync → resume on a different node |
| `test_multinode_real.py` | multi-node: real uvicorn + WebSocket Lab transport API journeys, including worker spawn/chat/control, identity sync, resume, and topology |
| `test_multinode_*audit*.py` / probe files | multi-node regression probes for cross-node forwarding, orphan tool calls, and API/local-vs-remote parity |

E2E "done" = feature-completeness: every core-lib feature is exercised
end-to-end by at least one journey. Coverage % is reported as a side
effect, not chased with granular tests.

## Coverage targets

"Core-lib" = everything under `api/`, `studio/`, `laboratory/`,
`terrarium/`, `core/`, `bootstrap/`, `prompt/`, `parsing/`, `modules/`,
`mcp/`, `commands/`, `compose/`, `session/`, `llm/`, `serving/`,
`packages/`, `utils/`.

- **Unit**: 95–100% line coverage per core-lib file. Any file below 95%
  needs a standalone written justification in `temp/BUGS.md`.
- **Integration + e2e**: high workflow coverage of core-lib; the gate is
  feature-completeness, not a single coverage number.
- **`builtins/` and `builtin_skills/`** are out of the hard target but
  welcome to test where deterministic.

`temp/BUGS.md` is the internal bug + coverage tracker (not part of the
public tree; `temp/` is gitignored). It records every bug the suite
surfaced, its fix, the regression guard, and the per-tier coverage
audit with each sub-95% file's justification.

## Bounded async-history conformance model

`tests/helpers/history_protocol.py` contains a finite specification and a
real-runtime adapter. The continuation unit module executes the generated
transition witnesses. The existing `test_history_ops` integration workflow
also carries one overlap trace per launch mode through branch return,
stop/close, cold resume, and another request.

### Bound and invariants

The fixed setup creates sibling branch views A/B, starts one tool on B, and
accepts one same-view input while it is running. The generated suffix has at
most one job cancellation and one compaction. Tools are tested as declared
background, caller-requested background, and promoted direct executions.

The independent model distinguishes:

- Job execution: running -> cancellation cleanup (optional) -> completed.
- Delivery: completed but handoff parked -> queued -> consuming -> settled.
  Declared background execution enqueues synchronously from its completion
  callback; it has no separate asynchronous handoff edge in this model.
- Compaction: idle -> summarizing -> complete-but-flushing -> settled.
  Cancellation before task entry or during summary emits `skipped`;
  cancellation during flush retains the already emitted `complete`.

Expected transitions are constructed from immutable specification state,
not inferred from runtime fields. After every transition the adapter checks:

1. Cancellation requests do not publish terminal job state before execution
   and cleanup settle. Suppressed cancellation can finish successfully.
2. A final result is persisted once, queued once, and consumed by one provider
   request; completion feedback is not fabricated twice.
3. Every started compaction has at most one terminal, with the same round.
   Applying a summary closes its event stream before checkpoint flush ends.
4. History changes are rejected while execution, handoff, queued input,
   consumption, or compaction is active. Rejection leaves conversation,
   selected/live branch views, persisted events, provider calls, and inbox
   count unchanged.
5. Once all fences settle, the public branch continuation succeeds and its
   provider request contains A, not B's inputs, result, or summary.

The single compact attempt always uses round 1. This does **not** redefine
round as a unique attempt ID; unsuccessful attempts can reuse a round in the
real implementation.

### Coverage and replay

Breadth-first traversal finds a shortest prefix to each model state, then
adds each outgoing edge. A witness that is a strict prefix of another is
removed; replaying the remaining traces still covers every model edge.

| Launch mode | Reachable states | Edges | Runtime witnesses | Longest generated suffix |
|---|---:|---:|---:|---:|
| Declared background | 40 | 83 | 48 | 7 |
| Requested background | 50 | 105 | 60 | 8 |
| Promoted direct | 50 | 105 | 60 | 8 |

Counts are per launch mode, not 140 distinct unlabelled shapes. Graph tests
verify that the generated witnesses cover its states and edges. Three
additional traces exercise suppressed cancellation. Three sensitivity
tests inject historical fault classes in memory: premature cancelled job
state, a missing pre-entry compact terminal, and a duplicate post-complete
terminal. Each must fail with the expected invariant diagnostic, not a
timeout or unrelated assertion. These are targeted mutations, not runs of
entire old revisions, and normal conformance tests do not apply them.
Three adapter regressions additionally require detection of an extra provider
request and a leaked B-only tail, and preservation of the primary divergence
when cleanup also fails. These were red on the initial adapter before its
oracles and failure handling were corrected. The cleanup injection raises
only after real cleanup succeeds; it checks diagnostic preservation, not
resource recovery from a genuinely stuck cleanup.

```bash
python -m pytest tests/unit/core/conversation/test_continuation.py -k history_model -n 0
python -m pytest tests/integration/test_core.py -k history_ops -n 0
```

Each generated case includes its launch/action sequence in the pytest ID.
Generated-step failures report the first divergent prefix for that witness
and retain the underlying assertion or runtime exception as the cause.
A second cleanup failure is appended to the diagnostic without replacing
the primary failure. Replay one case with its full node ID. This is not a
global counterexample minimizer.

### Runtime seams and limits

The adapter uses `asyncio.Event` barriers around real operations: tool
execution/cleanup, the promoted-result callback, inbox wake-up, a scripted
provider response, summarizer output, and the session call after compact
completion. Each wrapper eventually delegates to the original method. It
installs the inbox wrapper before agent start so the single consumer cannot
race its first wait. Setup and cleanup are not included in graph counts.
There are no sleeps, external model checkers, new dependencies, production
hooks, or CI changes.

This is **finite-model transition conformance**, not exhaustive runtime
model checking. Different histories that reach the same abstract state can
have different concrete data; only the generated witnesses are executed,
not every path through the graph or every asyncio interleaving. Private
runtime fields are observed and used as barriers, not replaced as behavior
in the passing runs. A guard probe is non-mutating at admitted intermediate
states; the actual public branch switch is exercised after orderly release.

Out of this slice: tool cancellation before its first instruction, multiple
jobs or compactions, stale-cancel successor leases, repeated branch
mutations, summary provenance variations, merge/split remapping, checkpoint
eviction, crash durability, error outcomes other than cancellation, and
fairness/unbounded liveness. Existing targeted tests still cover several of
these, but this model does not claim that coverage. Orderly cold restore is
not a crash-recovery proof.

## Two-generation compaction ownership model

`tests/helpers/compact_protocol.py` adds a separate finite specification for
two compaction attempts. It keeps the job/history model above unchanged;
these are complementary bounds, not a Cartesian product of both models.
The compact-manager unit module executes all **64 complete model paths**
through **25 reachable states and 41 transitions** (at most eight actions).
The graph check pins these counts and confirms state/edge coverage.

An attempt can be created but unentered, summarizing, complete but flushing,
settled successfully, or skipped before/after entry. Its position identifies
the attempt; emitted `round` numbers still count successful compactions.
Thus two cancelled attempts can both be round 1, and a cancellation after
`complete(1)` is followed by a new attempt at round 2. Assertions check the
whole ordered terminal stream, successful count, coroutine/summary-call
counts, and the complete provider-facing prompt. Its expected system and
user/assistant tail come from the pre-compaction fixture; summary text and
round come from the model, not from a post-splice runtime snapshot.

The cancellation operations cover ordinary settlement, two callers that
cancel the same entered task, and successor creation from an old task's done
callback. Both cancel callers must retain the successor's task reference,
lease identity, and single-flight admission. Every active state also rejects
a busy trigger without changing task/lease, lifecycle events, or prompt.
The terminal state receives another idle `cancel()` to check idempotence.

### The schedulable handoff window

The runtime wrapper only records coroutine entry before delegating; it does
not pause a synchronous finalizer. A done callback, registered before the
old cancellation waiter, starts the successor after the old execution has
settled and released its lease. On the default cooperative asyncio loop,
that successor owns the new lease but has **not entered its coroutine** when
each old waiter returns. The adapter explicitly asserts this entry order.
A one-caller handoff transition ends at successor-created. The two-caller
transition additionally joins the other caller and waits for the successor's
summarizer, ending at successor-summarizing. It makes no claim that the
successor entered while either old waiter was still suspended.

The adapter delegates real manager, output-router, session-store, and LLM
operations behind Event barriers. It does not replace task scheduling,
release leases, clear manager fields, or call finalizers in passing runs.
There is no eager/custom task-factory coverage. Alternative event-loop
ordering and pauses inside synchronous cleanup are not implied by these
paths.

Nine negative cases inject stale cleanup after real cancellation: force
release, task-reference clearing, and lease-reference clearing, each at the
old attempt's three cancellation boundaries. Every case must fail at the
handoff with its specific ownership diagnostic. These mutations reproduce
fault classes, not complete historical revisions. Six adapter regressions
corrupt the system message, latest assistant text, or tail order, both after
applying attempt 1 and after cancelling attempt 2. All six escaped the first
substring-based oracle; each now fails with the full-prompt diagnostic.

The existing `test_compaction_and_termination` integration workflow extends
four representative paths through persistence, stop/close, cold resume,
round/event readback, and another provider request. An ordinary request
checkpoints the settled history before closing; this is not a crash during
flush or a new durability guarantee.

```bash
python -m pytest tests/unit/core/compact/test_manager.py -k compact_model -n 0
python -m pytest tests/integration/test_core.py -k compaction_and_termination -n 0
```

Path IDs and first-divergent-prefix diagnostics work as in the first model.
Enumerating every path **inside this small model** does not enumerate every
runtime instruction interleaving. More than two attempts, summary failures,
vetoes, graph changes, concurrent history mutation, provenance-metadata
variations, arbitrary task factories, and stuck-cleanup recovery remain
outside this slice.

## Two-job result ownership model

`tests/helpers/two_job_protocol.py` specifies two independently identified,
declared-background tools on sibling branch B. The initial model state is
**after both tools have started**. Each can be cancelled or completed in
either order; the single consumer may claim both results together or in
separate batches. The model tracks job outcome, ordered delivery, batch
membership, consumption and the history-switch fence. Its breadth-first
edge witnesses cover **76 states and 88 transitions with 29 runtime traces**;
this is edge coverage of the specified finite graph, not all paths or all
runtime schedules. The graph test pins the counts and checks edge coverage.

Independent barriers gate the actual tools and the real inbox consumer;
completion events are delivered by the real executor. Assertions check
per-job status, result and persisted payload, ordered result events, exact
batch identities and provider input, cancellation attribution, rejected
branch-switch atomicity, and branch isolation after settlement. Targeted
provider-input mutations check detection of swapped, extra and repeated
results, reversed batch order and an extra request. The existing history
integration workflow additionally cold-restores one combined batch and one
separate cancellation/success sequence after an orderly branch switch, then
switches back to B to check the reconstructed result identities and outcomes.
Replay represents results as tool messages in original call order rather
than preserving the live feedback-batch grouping or delivery order.

This workflow exposed a replay defect: a cancelled job with empty output
was reconstructed as an empty tool result even though the stored event
retained the cancellation outcome. Replay now includes the cancellation
marker, preserving any partial text or structured output. Unit regressions
cover cancellation/interruption flags and successful structured output.

The first attempted model permitted job 2 to finish while job 1's provider
response was blocked. The real runtime may fold that completion into the
active turn (`core/agent/turn/mid_turn.py`), so a model expecting a later
inbox batch timed out. This is a model mismatch, not an established product
failure. This slice excludes completion during active consumption; cancellation
requests can still occur in that window. Four separate deterministic fold
witnesses cover either job arriving first and a successful or cancelled
second completion while the first provider request is blocked. Each checks
live fold attribution, rejects branch switching until the turn settles,
switches B→A→B, and verifies each outcome once; the existing integration
workflow also closes and cold-resumes all four. A targeted mutation disables
the same-turn projection and must fail at the folded-outcome assertion.
These examples are not a model of every fold schedule.

The replay fix only projects same-turn results when job and provider identity
are unambiguous, an assistant round intervenes, and no user/injected or
compaction boundary is crossed. It retains explicit/canonical announcements
and assistant text; repeated or ambiguous identities, partial group
announcements, and unsupported boundaries are conservatively left in their
original order. The prior later-turn projection behavior is separate. This
is not a guarantee that all ambiguous legacy histories can be repaired.
Requested/promoted launches, more than two jobs, crash recovery, and arbitrary
event-loop schedules remain outside this model. Orderly cold restore is not a
crash test.

```bash
python -m pytest tests/unit/core/conversation/test_continuation.py -k two_job_model -n 0
python -m pytest tests/integration/test_core.py -k history_ops -n 0
```

## Out-of-scope (deterministic test is impractical)

Excluded from coverage targets; covered by e2e where possible, manual
testing otherwise.

| Path | Reason |
|---|---|
| `cli/` | argparse + interactive prompts; covered by e2e for the resumable paths |
| `builtins/cli_rich/`, `builtins/tui/` | Rich / Textual terminal UI; visual |
| `llm/openai/provider.py`, `llm/codex/`, `llm/litellm_provider.py` | Need a live provider |
| `terrarium/engine/cli/tui.py`, `terrarium/engine/cli/rich.py`, `terrarium/engine/cli/output.py` | CLI / TTY UI |
| `studio/attach/pty/posix.py`, `studio/attach/pty/windows.py`, `studio/attach/pty/router.py` | Platform-specific PTY backends, gated at import |
| `studio/identity/codex/oauth.py` | 3rd-party OAuth flow |
| `session/memory/embedding.py` (non-deterministic embedders) | 3rd-party model dependency |
| `laboratory/_internal/transport/ws.py` | `websockets` library: `InProcTransport` is the test transport |
| `builtins/inputs/cli*.py`, `builtins/inputs/tui*.py`, `builtins/outputs/*` (terminal/audio) | CLI / TUI / audio UI |
| `serving/web.py` (the pywebview / uvicorn boot path) | Desktop bundling, manual test only |
| `browser/driver.py` (without Chromium) | The only module that imports Playwright; covered by the browser-backed tests, which skip where Playwright's Chromium is missing (CI included) |
| `api/main.py`, `__briefcase__.py` | Server / bundler entry points: module-level side effects only |
