# Contributing to KohakuTerrarium

Thanks for your interest in the project. This document is short on purpose — please read it end-to-end before opening an issue or PR. Following the rules below is what keeps everyone's time well-spent.

**English only.** All issues, PRs, commit messages, and code comments in this repository must be in English. PRs in other languages will be asked to translate before review. Discussion in other languages belongs in the community channels below.

The only exception: **multi-locale documentation files** (e.g. `README.zh.md`, `docs/zh/`, other translated `*.md` under `docs/`). Translations and locale-specific doc content are welcome and expected to be in their target language. Everything else — code, comments, commits, PR titles/descriptions, issue text — stays English.

## Community

Open a thread before you write code. The fastest channels:

- **QQ group**: 1097666427
- **Discord**: https://discord.gg/xWYrkyvJ2s
- **GitHub Issues**: https://github.com/KohakuBlueleaf/KohakuTerrarium/issues

Issue templates live in [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/).

---

## Before You Open a PR

We enforce this strictly. The point is not gatekeeping — it is to make sure no one (you included) wastes time on work that conflicts with the team's in-flight direction. PRs closed under this policy can be **reopened immediately** once the discussion-and-alignment step is done; we are happy to do that.

### Feature PRs require prior approval

A "feature PR" is anything that adds new functionality, changes a public API, alters core architecture, introduces a new module/tool/sub-agent shape, or changes user-visible behavior in a non-trivial way.

Before opening a feature PR, you need **one** of:

1. An open **feature-request issue** that a maintainer has acknowledged with explicit approval (a comment giving you the green light, or an "approved" label).
2. A **discussion in the QQ group or Discord** with an owner/maintainer who has given you a clear go-ahead.

**Submitting an issue and a PR at the same time does not count.** The issue must exist first, be discussed, and be approved. Opening the PR before that approval lands will get the PR closed.

PRs that arrive without traceable approval will be closed with a short pointer to this document. This is firm but not personal — open the issue, get aligned, and reopen. We will help you do that.

### Bug fixes and minor enhancements are looser

These can go straight to PR:

- Bug fixes (clear bug, clear fix, no architectural change)
- UI/UX adjustments that do not change behavior or layout meaningfully
- Documentation fixes, typo fixes, dead-link fixes
- Test additions for existing behavior
- Small refactors confined to a single module that do not change public APIs

We still **strongly recommend** opening an issue or pinging the community channels first, even for these. Reasons:

- Your fix may collide with in-progress work the team has not pushed yet.
- The bug may already be fixed on a private branch.
- The "obvious" fix may conflict with a planned redesign of the same area.

A two-line message in Discord before you start saves hours of rework.

### What "approval" looks like

A maintainer comment like "go ahead", "approved", "please open a PR for this", a thumbs-up reaction on a concrete proposal, or an "approved" label. If you are unsure whether you have approval, you do not — ask explicitly.

---

## CI Must Be Green on Your Fork

We do not review PRs with red CI. Before opening a PR:

1. **Enable GitHub Actions on your fork** (Settings → Actions → Allow all actions). Forks have Actions disabled by default.
2. Push your branch to your fork.
3. Wait for the full CI matrix to finish on the fork. Open the PR only after it is green.

The CI matrix that must pass is defined in [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

- **Lint**: `ruff check` + `black --check` (Python 3.13)
- **Tests**: `pytest tests/unit/` on Python 3.10, 3.11, 3.12, 3.13, 3.14 × Linux / Windows / macOS (3.14 on Windows is excluded — pythonnet has no wheel yet)
- **File-size guards**: `pytest tests/unit/test_file_sizes.py`
- **Frontend**: `npm ci` + `npm run format:check` + `npm run build` in `src/kohakuterrarium-frontend/`, plus a check that the build output landed in `src/kohakuterrarium/web_dist/`
- **Wheel build**: builds the wheel, installs it into a clean venv, runs `kt --help` and `kt app --help`

Local pre-flight (run all of these before pushing):

```bash
# Python
ruff check src/ tests/
black --check src/ tests/
pytest tests/unit/ -q --ignore=tests/unit/test_file_sizes.py
pytest tests/unit/test_file_sizes.py -q

# Frontend
cd src/kohakuterrarium-frontend
npm ci
npm run format:check
npm run build
```

If you cannot run a piece locally (e.g. no Windows machine for the matrix), that is what your fork's CI is for. Push and watch it.

---

## Setup

```bash
git clone --recurse-submodules https://github.com/KohakuBlueleaf/KohakuTerrarium.git
cd KohakuTerrarium
uv pip install -e ".[dev]"

# Frontend (optional — only if you'll touch the web UI)
cd src/kohakuterrarium-frontend
npm install
```

Submodules are needed for some tests; if you forgot `--recurse-submodules`, run `git submodule update --init --recursive` after cloning.

---

## Code Conventions

The full conventions live in **[CLAUDE.md](CLAUDE.md)**. Read it before submitting code. The non-negotiables:

### Python

- **Python 3.10+ minimum.** Use modern type hints — `list`, `dict`, `tuple`, `X | None`. Never `List`, `Dict`, `Tuple`, `Optional`, `Union` from `typing`.
- **Import grouping** (in this order, blank line between groups):
  1. Built-in modules
  2. Third-party packages
  3. `kohakuterrarium.*` modules
- **Within each group**: `import` statements before `from` imports, shorter dotted paths first, then alphabetical.
- **No imports inside functions.** The only exceptions are optional dependencies and lazy imports specifically used to avoid long startup time. Circular-import workarounds are not an excuse — restructure instead.
- **No naive `print()` in library code.** Use the structured logger (built on `stdlib logging`, not loguru). Format `[HH:MM:SS] [module.name] [LEVEL] message`. Avoid reserved `LogRecord` attribute names in `extra=` kwargs (`name`, `msg`, `args`, `levelname`, `module`, `lineno`, etc. — full list in CLAUDE.md).
- **Max 600 lines per file** (hard cap 1000, enforced by `tests/unit/test_file_sizes.py`). Split modules before they grow.
- **Full asyncio.** Mark sync modules as "requires blocking" or "can be `to_thread`".
- **Prefer `match-case`** over deeply nested `if-elif-else`.
- **Never use `sys.path` hacks** in examples or tests. Always import from the installed package.

### Frontend

The Vue 3 frontend lives in [`src/kohakuterrarium-frontend/`](src/kohakuterrarium-frontend/). It is JavaScript only — **no TypeScript**. Run `npm run format:check` and `npm run build` before pushing.

### Architecture rules

These are the rules that get most-violated by drive-by PRs. Read CLAUDE.md "Core Architecture Concepts" in full before touching anything in `core/`, `terrarium/`, or `modules/subagent/`.

- **Creature** — self-contained agent. Has its own LLM, tools, sub-agents, memory, I/O. Does **not** know it is in a terrarium.
- **Terrarium** — pure wiring. **No LLM, no intelligence, no decisions.** Loads creatures, creates channels, manages lifecycle.
- **Root agent** — a creature that sits **outside** the terrarium and manages it via tools. **Never** a peer of creatures inside.
- **Sub-agents inside a creature** are vertical (private internal delegation). **Creatures connected by a terrarium** are horizontal (peer, opaque). Never mix the two compositions.
- **Controller is an orchestrator.** Its outputs should be short — tool calls, sub-agent dispatches, status updates. Long user-facing content comes from output sub-agents.
- **Tool execution is async, non-blocking, parallel.** Start tools the moment `##tool##` is detected during streaming. Never queue them, never run sequentially, never block LLM output to wait for them.

### Post-implementation checklist

Before opening the PR:

1. Re-read your diff against CLAUDE.md rules. Especially in-function imports, `print()` calls, and the import order.
2. Run `black src/ tests/` and `ruff check src/ tests/`.
3. Add tests for new code under `tests/unit/`. Test suites can use simpler output than library code.
4. Split commits along logical boundaries — one concept per commit, working on each commit.

---

## Project Structure (abbreviated)

```
src/kohakuterrarium/
  core/             # Runtime engine (agent, controller, executor, events, channels)
  bootstrap/        # Agent initialization factories
  cli/              # `kt` entry point and subcommands
  modules/          # Plugin protocols (input, trigger, tool, output, subagent, plugin, user_command)
  builtins/         # Built-in implementations (tools, sub-agents, inputs, outputs, TUI, slash commands)
  builtin_skills/   # On-demand markdown docs for tools/sub-agents
  llm/              # LLM provider abstraction + presets + profile resolution
  prompt/           # Prompt aggregation and templating
  parsing/          # Stream parser (state machine for tool-call detection)
  commands/         # Framework commands (##info##, ##read##)
  session/          # Session persistence (.kohakutr files via KohakuVault)
  serving/          # Transport-agnostic agent/terrarium serving layer
  terrarium/        # Multi-agent runtime
  api/              # FastAPI HTTP API + WebSocket
  testing/          # Test infrastructure (ScriptedLLM, recorders, harness)
  utils/            # Shared utilities (logging, async, file_guard)

src/kohakuterrarium-frontend/   # Vue 3 web dashboard
creatures/                       # Creature config templates
terrariums/                      # Terrarium config templates
examples/                        # Example agent apps, terrariums, code samples
docs/                            # Documentation (en, zh)
tests/                           # Test suites
```

For the full annotated tree, see CLAUDE.md.

---

## Adding Things

We removed the inline code snippets from this document on purpose — they drift from reality every release. Use the existing implementations as the source of truth:

- **A built-in tool** — read [`src/kohakuterrarium/builtins/tools/README.md`](src/kohakuterrarium/builtins/tools/README.md) and copy the pattern from a small existing tool such as `glob.py` or `grep.py`. Add the matching skill doc under [`src/kohakuterrarium/builtin_skills/tools/`](src/kohakuterrarium/builtin_skills/tools/).
- **A built-in sub-agent** — read [`src/kohakuterrarium/builtins/subagents/README.md`](src/kohakuterrarium/builtins/subagents/README.md) and use an existing config such as `explore.py` or `research.py` as the template. Add the matching skill doc under [`src/kohakuterrarium/builtin_skills/subagents/`](src/kohakuterrarium/builtin_skills/subagents/).
- **An LLM preset** — see `src/kohakuterrarium/llm/presets.py` for the dict shape, or use `kt config llm add` to register a user preset.
- **An example agent or terrarium** — copy an existing folder under `examples/agent-apps/` or `examples/terrariums/` and adapt the config + system prompt.
- **A package (shareable creature/terrarium bundle)** — see the package system documented under `kt install --help` and `kt info`.

Any of these still counts as a feature contribution and falls under the **Feature PRs require prior approval** rule above. Open the issue first.

---

## Reporting Issues

Use the templates in [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/). Include:

- Python version, OS, install method (`uv pip install -e .` vs wheel vs other)
- For agent-behavior issues: the exact creature/terrarium config and a minimal reproducible scenario
- Relevant logs (the structured logger output, not just stdout)
- For frontend issues: browser, console errors, and what the API/WS traffic looked like

Search existing issues first — the duplicate rate is high.

---

## License

This project is licensed under the **[KohakuTerrarium License 1.0](LICENSE)**, based on Apache-2.0 with two extra requirements:

- Derivative works must include `Kohaku` or `Terrarium` in their name.
- Derivative works must provide visible attribution with a link to this project.

By contributing, you agree that your contributions are licensed under the same terms.

Copyright 2024-2026 Shih-Ying Yeh (KohakuBlueLeaf) and contributors.
