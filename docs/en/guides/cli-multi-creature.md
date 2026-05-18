---
title: Rich CLI multi-creature
summary: Working with a multi-creature terrarium in `kt run --mode cli` — roster, focus switching, @name retargeting, slash commands, and the Ctrl+A overlay.
tags:
  - guides
  - cli
  - terrarium
---

# Rich CLI multi-creature

`kt run --mode cli` opens the rich inline CLI. With a single-creature
config nothing changes from 1.4 — you get the bordered input box,
live region, slash commands, prompt-toolkit history. With a
**multi-creature terrarium**, the CLI surfaces every creature in a
**roster row** above the input and gives you focus switching,
per-creature drafts, `@name` retargeting, and topology-aware slash
commands.

## The roster

When more than one creature is loaded, a single horizontal row
appears just above the input box:

```
  ┌─ Creatures ─────────────────────────────────────────────────────┐
  │ ▸clawd ● Edit src/sprite.py    physics ○ idle 2m   power-up ⚠   │
  └─────────────────────────────────────────────────────────────────┘
  [clawd]> _
```

Each slot is `<focus-marker><name> <glyph> <activity>`:

| Glyph | State | Meaning |
|---|---|---|
| `●` | working | LLM generating or a tool/sub-agent is running |
| `○` | idle | nothing in flight |
| `⚠` | waiting | the creature is asking you (`ask_user`, permgate) |
| `✗` | failed | last turn ended with an exception |
| `■` | stopped | explicitly stopped |

The `▸` arrow marks the **focused** creature — the one your input
goes to. When a creature you're not focused on has new activity since
you last viewed it, a `●N` badge appears next to its name.

### When the terminal is narrow

If there's no room for every creature, idle / stopped ones collapse
to a count:

```
  ┌─ Creatures ─────────────────────────────────────────────────────┐
  │ ▸power-up ⚠ needs choice   ●clawd Edit src...   ●collision...   │
  │   +2 idle  +1 stopped                                           │
  └─────────────────────────────────────────────────────────────────┘
```

Working and waiting creatures always stay visible by name (those are
the ones that need your attention).

## Focus switching

| Key | What it does |
|---|---|
| `Tab` | Focus next creature |
| `Shift+Tab` | Focus previous creature |
| `Ctrl+A` | Open agent overlay (see below) |

When you switch focus, the rich CLI gives you a **full context
swap** — you see only the newly-focused creature's history, not an
interleaved log of every creature:

- The input prompt prefix changes: `[clawd]> ` → `[physics]> `
- The live region (in-flight streaming + active tools) swaps to the
  new creature's buffer
- The **footer** repaints from the new creature's agent — model name,
  context-size budget, token totals all reflect what `physics` is
  actually running, not what `clawd` was running
- **Your in-progress input draft stays with the creature you were
  targeting** — switch back, your half-written message is still there
- **The terminal scrollback is wiped and replayed**: every committed
  message, tool result panel, and notice for the new creature is
  re-emitted into scrollback. PgUp / mouse scroll then see only that
  creature's history. The shared interleaved log is gone — Tab is a
  true context switch, not a peek.

The redraw is driven by a per-creature commit log captured in memory
as the conversation runs. Streaming text that hasn't finished yet
isn't captured (it's still in the live region of the creature that
produced it), but everything committed to scrollback — user messages,
finished assistant turns, tool blocks, sub-agent panels — replays
faithfully on each switch.

A natural consequence: long sessions with many creatures means each
Tab triggers a real screen redraw. For a few hundred turns this is
imperceptible; for very long sessions, switching is the slowest
interactive action in the CLI.

## `@name` retargeting

To send a single message to a creature without changing focus, prefix
the input with `@<name>`:

```
  [clawd]> @physics what's the collision check returning?
                ↑ goes to physics, focus stays on clawd
```

`@all <msg>` broadcasts to every creature, but only when the focused
creature is **privileged** (recipe-root or user-spawned top-level).

`@name` messages are recorded in the **recipient's** scrollback log,
not the sender's — Tab to `physics` later and you'll see your question
and physics's answer together rather than orphaned. `@all` broadcasts
are recorded into every creature's log so each one carries the same
visible context when you switch to it.

## Slash commands

The pre-existing slash commands (`/clear`, `/model`, `/status`,
`/scratchpad`, …) act on the **focused** creature. Topology-aware
commands new in 1.5:

| Command | What it does |
|---|---|
| `/stop` | Stop the focused creature |
| `/stop <name>` | Stop a specific creature |
| `/start` / `/start <name>` | Start a stopped creature |
| `/spawn <recipe>` | Spawn a new creature (privileged focus only) |
| `/jobs` | List the focused creature's running jobs |
| `/channels` | List channels the focused creature participates in |
| `/scratchpad` | Show the focused creature's scratchpad |

## Ctrl+A — agent overlay

Press `Ctrl+A` to open a full-list overlay grouped by state:

```
  ┌─ Agent view ───────────────────────────────────────────────────┐
  │ Filter: [           ]                              Esc to close│
  ├────────────────────────────────────────────────────────────────┤
  │ Needs input                                                    │
  │   ⚠ power-up        needs: double jump or wall climb?    15s   │
  │                                                                │
  │ Working                                                        │
  │   ● clawd           Edit src/sprite.py                    3s   │
  │   ● collision       bash: pytest tests/collision.py      12s   │
  │                                                                │
  │ Idle                                                           │
  │   ○ physics         idle 2m                                    │
  │                                                                │
  │ Stopped                                                        │
  │   ■ debug-helper    stopped 30m ago                            │
  ├────────────────────────────────────────────────────────────────┤
  │ ↑↓ select  Space peek  Enter focus  Esc close                  │
  └────────────────────────────────────────────────────────────────┘
```

| Key | Action |
|---|---|
| `↑` / `↓` | Move selection |
| `Space` | Peek selected creature (right pane preview, no focus change) |
| `Enter` | Focus selected creature and close overlay |
| `→` | (when peeking) promote peek to focus |
| `Esc` | Close overlay |
| _typing_ | Filter the list by name / activity (case-insensitive) |

### Peek

`Space` on a selected row opens a right-side panel showing the last
30 seconds of that creature's output. **Typing while peek is open
routes your message to the peeked creature** — handy for replying to
an `ask_user` prompt without changing focus.

## When to use this vs other frontends

| Want… | Use |
|---|---|
| Single-creature chat, fast, in scrollback | `kt run --mode cli` (this guide) |
| Visual graph editor, multi-tab, web | `kt app` / `kt serve` (web UI) |
| Tree of creatures, channel transcripts, mouse | `kt run --mode tui` (Textual) |

The rich CLI's multi-creature surface is opinionated toward
keyboard-first use; if you need visual topology editing or want to
attach from a browser, the web UI is the right tool.

## See also

- [`kt --help`](../../README.md) — full CLI reference
- [CLI ↔ UI equivalents](cli-and-ui-equivalents.md) — every `kt` verb's UI surface
- [Configuration guide](configuration.md) — what a terrarium recipe looks like
