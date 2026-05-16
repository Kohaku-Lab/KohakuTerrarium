# Test suite

`tests/` has three tiers — `unit/`, `integration/`, `e2e/` — each a
*different shape of test*, not just a different size. This file is the
source of truth for what each tier is and how to write them. Read it
before adding or reviewing any test.

## Running the tests

```bash
# Unit tier — fast, per-file. The file-size + dep-graph guards have
# their own CI jobs, so exclude them from the everyday run.
pytest tests/unit/ -q --ignore=tests/unit/test_file_sizes.py --ignore=tests/unit/test_dep_graph_lint.py

# The guard suites (run them too before pushing)
pytest tests/unit/test_file_sizes.py tests/unit/test_dep_graph_lint.py -q

# Integration + e2e tiers — workflow / journey tests, slower
pytest tests/integration/ tests/e2e/ -q

# Everything
pytest tests/ -q

# Coverage for one tier
pytest tests/unit/ -q --cov=src/kohakuterrarium --cov-report=term-missing
```

`pytest` is configured in `pyproject.toml` with `asyncio_mode = "auto"`
(no `@pytest.mark.asyncio` needed) and a per-test timeout.

**CI policy (`.github/workflows/ci.yml`).** Unit and integration tiers
run on the full OS × Python matrix (3.12+ on Linux / macOS / Windows).
**The e2e tier is NOT run in CI.** It spins up real WebSocket-backed
lab clusters, subprocess workers, multi-node session mirrors, and
Vue-frontend-style HTTP/WS journeys; the resulting timing depends on
hosted-runner network + scheduler behavior that is too volatile to
gate every PR on. E2E is the developer surface for reproducing real
user behavior locally — run it before shipping anything that touches
the multi-node / Studio / serving stack. Bug anchoring and regression
protection on `main` come from the unit + integration tiers.

## The three tiers — what each one actually is

- **unit = one source file → one test (or test-class).** Test an
  individual class / method against its real dependencies (deterministic
  stubs only for genuine I/O). Shape checks (`isinstance`, `key in
  dict`, `is not None`) are legitimate **here and only here**. Goal:
  95–100% line coverage per core-lib file; any sub-95% file needs a
  written justification in `temp/BUGS.md`.

- **integration = one core-lib folder → one test-class.** Each test
  method runs a **complete feature workflow end-to-end in a single
  function** — e.g. for `session/`: *initialize a session → run a
  creature that writes stuff → record turns → read it back → resume from
  it → verify*, all in ONE test. Splitting that into "init" / "write" /
  "read" / "resume" as separate tests is unit-tier thinking and
  structurally cannot catch a bug that only manifests across the
  sequence. The integration test for a folder **is the most
  comprehensive usage example of that folder**.

- **e2e = whole project → a handful of fat journey tests.** Each test is
  a single function simulating an entire user session — chat, change
  model, toggle plugins, adjust settings, interrupt, resume, branch,
  search — *all in one test*. There are deliberately few: roughly
  `{programmatic, HTTP+WS} × {creature, terrarium, studio}` + multi-node
  variants ≈ 10 tests covering the whole framework. e2e answers one
  question: *is the system runnable, end to end?*

## The discipline rules (every tier)

1. **Behavior asserts, not shape asserts.** A test that calls a mutation
   MUST observe the side effect — exact state change, event fired, file
   written, message delivered, exact return value. `assert status in
   {200, 400}` is forbidden. (Shape checks are the *exception* allowed at
   the unit tier only — see above.)

2. **Use real collaborators, not mocks.** The only legitimate seam is a
   genuine external dependency: the LLM (use
   `kohakuterrarium.testing.llm.ScriptedLLM`, monkeypatched in at BOTH
   `kohakuterrarium.bootstrap.llm.create_llm_provider` and
   `kohakuterrarium.bootstrap.agent_init.create_llm_provider` — and at
   `kohakuterrarium.core.agent_model.create_llm_from_profile_name` if the
   test exercises a model switch), the filesystem (`tmp_path`), the
   network. **Never invent a method on a fake that the real class does
   not have** — that bakes a bug in as the expected behavior. Use the
   real engine, real `SessionStore`, real `InProcTransport`, real
   `TestClient`.

3. **Integration tests must mirror how the codebase actually uses the
   folder.** The test is not just "the shape of how this system should
   work" — it is also "how this system *should be used*". After an
   integration test is green, ask: *does the rest of the codebase drive
   this folder the same way my test does?* If there is a usage paradigm
   the test doesn't reproduce, either extend the test to cover it, or —
   if that usage path is actually broken — file it as a bug. This is why
   integration tests are meaningful: they pin the real contract between
   layers, not a parallel test-only wiring. (Concretely: the
   `laboratory/` integration test builds `MultiNodeTerrariumService`
   exactly as `cli/serve.py` does — not a hand-wired host/client rig.)

4. **No global state leaks.** Tests that mutate a module-global registry
   (`lifecycle._meta`, `api.deps._service`, …) must snapshot + restore —
   the `isolate_global_state` autouse fixture in `tests/conftest.py`
   handles the known ones.

5. **To raise integration/e2e coverage, fatten — don't multiply.** When
   a workflow/journey test doesn't reach far enough, add more operations
   to the *existing* test function. Do NOT add more test functions —
   that drifts back toward granular per-method tests, which is the
   unit tier's job.

6. **Bugs surface as a failing assertion, then a regression guard.** A
   test that reveals a bug pins it — `xfail(strict=True, reason="BUG:
   ...")` for a whole test, or an inline `# BUG:` assertion for a single
   point inside a fat workflow. The bug is logged in `temp/BUGS.md`.
   Fixing the bug must flip the pin to assert the *correct* behavior —
   that's the regression guard. A test that *can't* surface bugs
   (because it only re-asserts current behavior) is worthless.

## Integration tier — one test-class per folder

`tests/integration/test_<folder>.py`, each a single test-class whose
methods are **full feature workflows** (one workflow = one function).
Each mirrors how the **real consumer** drives that folder.

| File | Folder | The workflow it runs | Real consumer it mirrors |
|---|---|---|---|
| `test_core.py` | `core/` | build an `Agent` → inject input → controller loop → direct + background tool dispatch → sub-agent dispatch → output routing → compaction → termination → interrupt → history ops | `terrarium/creature_host.py`, `bootstrap/agent_init.py` |
| `test_bootstrap.py` | `bootstrap/` | `Agent.from_path` on a real on-disk config → every factory fires → run a turn proving the wiring is live | `terrarium/factory.py`, `core/agent.py.__init__` |
| `test_session.py` | `session/` | init store → creature writes events → record turns → read back → close → resume → verify → memory search → fork → migration | `terrarium/persistence.py`, `studio/sessions/lifecycle.py`, `session/resume.py` |
| `test_terrarium.py` | `terrarium/` | build graph → add creatures → wire channels → broadcast → privileged node mutates → auto-merge/split → session lineage → hot-plug → recipe apply — driven through `LocalTerrariumService` | `studio/sessions/lifecycle.py` |
| `test_laboratory.py` | `laboratory/` | real host+client over `InProcTransport`, wired as `cli/serve.py` does → `MultiNodeTerrariumService` → spawn-on-worker → remote chat → remote ops → deploy → session sync | `cli/serve.py` boot dispatch, `api/deps.py` |
| `test_studio.py` | `studio/` | `Studio` façade → catalog → editors create workspace creature → sessions start+chat → persistence save/resume/fork → identity → attach | `api/routes/*`, `cli/*` |
| `test_api.py` | `api/` | `create_app()` + `TestClient` → create session → WS chat → settings → history → hot-plug → save/resume → delete | frontend `utils/api.js` call sequence |
| `test_modules.py` | `modules/` | load real plugin/tool/trigger/subagent/output/user_command modules into a real `Agent` → exercise each protocol through a real turn | `bootstrap/*` loads, `core/agent_*` invokes |
| `test_prompt.py` | `prompt/` | assemble a real agent's system prompt through the aggregator → on-demand `##info##` skill load → template rendering | `core/agent_init`, `bootstrap` |
| `test_parsing.py` | `parsing/` | feed real streamed LLM output through `StreamParser` across chunk boundaries → exact tool-call/command/text events | `core/controller.py` |
| `test_commands.py` | `commands/` | a real controller turn emits `##info##` / `##read##` → command resolves docs/skills into the conversation | `core/controller`, `core/agent_handlers` |
| `test_mcp.py` | `mcp/` | real in-process stdio MCP server → connect → list → call → disconnect, cross-task as `core/agent_mcp.py` drives it | `core/agent_mcp.py` |
| `test_compose.py` | `compose/` | build real `>>` / `&` / `\|` / `*` pipelines over engine-backed runnables and run them | user code (compose is a public API) |
| `test_serving.py` | `serving/` | `KohakuManager` + `AgentSession` → create session → stream chat → lifecycle | `api/`, `compose/` |
| `test_packages.py` | `packages/` | install a local package → list → info → resolve a `pkg:` ref → uninstall | `cli/packages.py`, `terrarium/config.py` |
| `test_llm.py` | `llm/` | define profiles/presets → save/load the store → resolve a provider config → build tool schemas + messages → api-key round-trip (live providers excluded) | `bootstrap/llm.py` |

`utils/` has no standalone cross-folder workflow — its helpers are
exercised *through their consumers* (`file_guard` / `file_walk` via real
tool calls in `test_core.py` / `test_modules.py`).

## E2E tier — a handful of fat journey tests

`tests/e2e/test_<journey>.py`, each a single test function simulating an
entire user session. One per natively-different usage variation.

| File | Journey (one fat test) |
|---|---|
| `test_prog_creature.py` | programmatic: one creature — multi-turn chat → tool calls → sub-agent dispatch → change model → toggle plugin → adjust setting → interrupt → resume → branch/regenerate → memory search |
| `test_prog_terrarium.py` | programmatic: recipe apply → multi-creature graph → channel broadcast → privileged node mutates the team → hot-plug → auto-split → stop → resume |
| `test_prog_studio.py` | programmatic: Studio — catalog → create workspace creature → start → chat → save → list → resume → fork → viewer → delete |
| `test_api_creature.py` | HTTP+WS: `TestClient` — create session → WS chat stream → model/plugin endpoints → interrupt → history/branch → resume |
| `test_api_terrarium.py` | HTTP+WS: create terrarium → runtime-graph WS → channel send → hot-plug via API → topology endpoints |
| `test_api_studio.py` | HTTP+WS: workspace CRUD → catalog → saved-session list/viewer/diff/resume/fork → attach |
| `test_api_settings.py` | HTTP+WS: the configuration surface — identity/profiles/keys, MCP registry, default-model, UI prefs, native-tool & plugin options |
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
public tree — `temp/` is gitignored). It records every bug the suite
surfaced, its fix, the regression guard, and the per-tier coverage
audit with each sub-95% file's justification.

## Out-of-scope (deterministic test is impractical)

Excluded from coverage targets — covered by e2e where possible, manual
testing otherwise.

| Path | Reason |
|---|---|
| `cli/` | argparse + interactive prompts; covered by e2e for the resumable paths |
| `builtins/cli_rich/`, `builtins/tui/` | Rich / Textual terminal UI; visual |
| `llm/openai.py`, `llm/anthropic_provider.py`, `llm/codex_*.py`, `llm/litellm_provider.py` | Need a live provider |
| `terrarium/engine_cli.py`, `terrarium/engine_rich_cli.py`, `terrarium/cli_output.py` | CLI / TTY UI |
| `studio/attach/pty_posix.py`, `studio/attach/pty_windows.py`, `studio/attach/pty_router.py` | Platform-specific PTY backends, gated at import |
| `studio/identity/codex_oauth.py` | 3rd-party OAuth flow |
| `session/embedding.py` (non-deterministic embedders) | 3rd-party model dependency |
| `laboratory/_internal/transport_ws.py` | `websockets` library — `InProcTransport` is the test transport |
| `builtins/inputs/cli*.py`, `builtins/inputs/tui*.py`, `builtins/outputs/*` (terminal/audio) | CLI / TUI / audio UI |
| `serving/web.py` (the pywebview / uvicorn boot path) | Desktop bundling, manual test only |
| `api/main.py`, `__briefcase__.py` | Server / bundler entry points — module-level side effects only |
