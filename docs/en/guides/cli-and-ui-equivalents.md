---
title: CLI and UI equivalents
summary: Every `kt` subcommand and its frontend surface (or the documented reason it stays CLI-only).
tags:
  - guides
  - cli
  - desktop
  - reference
---

# CLI and UI equivalents

KohakuTerrarium's `kt` CLI and Vue desktop app are two faces of the
same engine. This page is the **complete cross-reference** so you can
move between them without guessing.

Legend:

- ✅ — Shipped in the UI; no need to leave the desktop app.
- ⚠️ — Partial; the UI covers the common path, the CLI is still needed
  for edge cases (listed in *Notes*).
- 🔧 — CLI-only by design (deployment / ops). The UI deliberately
  does not expose it.

## Identity / configuration

| `kt` verb | UI surface | Status |
|---|---|---|
| `kt config provider list/add/edit/delete` | Settings → **Providers** | ✅ |
| `kt config llm list/show/add/edit/delete` | Settings → **Models** | ✅ |
| `kt config llm default <name>` | Settings → Models → preset editor → **Set as default** | ✅ |
| `kt model list/show/default` | Settings → **Models** (aliases of `config llm *`) | ✅ |
| `kt config key list/set` | Settings → Providers → inline key field | ✅ |
| `kt config key delete` | Settings → Providers → trashcan next to a saved key | ✅ |
| `kt login codex` | Settings → Providers → Codex row → **Sign in with Codex** | ✅ |
| `kt login <openai/anthropic/...>` | Settings → Providers → inline key field (those providers use API keys, not OAuth) | ✅ |
| `kt config mcp list/add/delete` | Settings → **MCP servers** | ✅ |
| `kt config mcp edit` | Settings → MCP servers → row **Edit** button → modal | ✅ |
| `kt config show / path` | Settings → **Advanced** → file table | ✅ |
| `kt config edit <name>` | Settings → Advanced → **Edit** on a file row | ✅ |

## Sessions

| `kt` verb | UI surface | Status |
|---|---|---|
| `kt list` (sessions) | Sessions tab — saved-session list | ✅ |
| `kt resume <session>` | Sessions tab → row → **Resume** | ✅ |
| `kt search <session> <query> --mode --agent -k` | Session viewer → **Find** tab | ✅ |
| `kt embedding <session> --provider --model --dimensions` | Sessions tab → row kebab menu → **Build embeddings** (or Find tab empty-state banner) | ✅ |

## Packages

| `kt` verb | UI surface | Status |
|---|---|---|
| `kt list` (catalog) | Catalog tab | ✅ |
| `kt info <agent_path>` | Catalog → card → **Info** drawer | ✅ |
| `kt install <git/local/pypi>` | Catalog → **Install from URL** modal | ✅ |
| `kt uninstall <name>` | Catalog → card → **Uninstall** | ✅ |
| `kt update [target] [--all]` | Catalog → card → **Update**, or toolbar **Update all** | ✅ |
| `kt edit @pkg/...` | Catalog → card → **Edit files** drawer (in-app YAML editor) | ✅ |
| `kt extension list` | Extensions tab (top-level) | ✅ |
| `kt extension info <name>` | Extensions tab → row | ✅ |
| `kt mcp list --agent <path>` | Settings → MCP → row **Edit** modal → "Used by" list | ✅ |

## Update / self-update

| `kt` verb | UI surface | Status |
|---|---|---|
| `kt self-update` | Settings → **Updates** | ✅ |
| `kt self-update --source/--spec` | Settings → Updates → source picker | ✅ |
| `kt self-update --check-only / --dry-run` | Settings → Updates → **Check now** | ⚠️ (no dry-run UI; rare enough to keep CLI-only) |

## About / diagnostics

| `kt` verb | UI surface | Status |
|---|---|---|
| `kt --version --verbose` | Settings → **About** → diagnostic info panel | ✅ |
| `kt serve logs --follow --lines --level` | Settings → About → **View server log** | ✅ |
| `kt serve status` | Settings → About → daemon section | ✅ |
| `kt serve start/stop/restart` | The app **is** the daemon; runs implicitly | ✅ |

## Lab / multi-node

| `kt` verb | UI surface | Status |
|---|---|---|
| `kt host` / `kt serve start --mode lab-host` | Settings → **Sites** (visible only in lab-host mode) | ⚠️ (you still launch the host process from the CLI / `kt service`) |
| `kt client` / `kt lab-client` | Settings → Sites → **Spawn client wizard** generates the exact command | ⚠️ (you paste it on the worker host's terminal) |
| Disconnect a worker | Settings → Sites → row menu → **Disconnect** | ✅ |
| Block a worker | Settings → Sites → row menu → **Block** | ✅ |
| Rotate pairing token | Settings → Sites → **Rotate pairing token** | ✅ |

## Deployment / OS service

| `kt` verb | UI surface | Status |
|---|---|---|
| `kt service install/uninstall/status/edit` | (none) | 🔧 By design — systemd unit installation runs as root on a server. Operators use the CLI or Ansible / Docker. |

## See also

- [Desktop UI walkthrough](desktop-ui-walkthrough.md) — guided tour of each tab.
- [App update](app-update.md) — full update flow internals.
- [Serving guide](serving.md) — how the daemon is wired.
- [`kt --help`](../../README.md) — terminal-side reference.
