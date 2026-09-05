---
title: Frontend layout
summary: How the Vue 3 dashboard is organised, where to extend it, and how events flow from backend to UI.
tags:
  - guides
  - frontend
  - ui
---

# Frontend Layout

For readers using or customising the web dashboard served by `kt web` / `kt app` / `kt serve`.

The dashboard uses a configurable binary split tree: every pane is either a leaf (one panel) or a split (two children with a draggable handle). Presets swap the whole tree at once; edit mode rearranges it in place.

See also: [Serving](serving.md) for how to open the dashboard.

## Core concepts

- **Panel**: a single-responsibility view (Chat, Files, Activity, State,
  Canvas, Debug, Settings, Terminal, etc.). Panels are registered in
  `stores/layoutPanels.js` and resolved by id.
- **Split tree**: a binary tree where each node is either a *leaf*
  (renders one panel) or a *split* (divides space into two children
  with a draggable handle). Splits can be horizontal (left | right) or
  vertical (top / bottom).
- **Preset**: a named split tree configuration. Switching presets
  replaces the entire tree instantly. Presets are either builtin
  (shipped with KT) or user-created.
- **Header**: top bar with instance info, preset dropdown, edit layout
  button, Ctrl+K palette trigger, and stop button.
- **Status bar**: bottom bar with model switcher, session id, job count,
  runtime.

## Default presets

| Shortcut | Preset | Layout |
|----------|--------|--------|
| Ctrl+1 | Chat Focus | chat \| status (top) + creature state (bottom) |
| Ctrl+2 | Workspace | files \| editor+terminal \| chat+activity |
| Ctrl+3 | Multi-creature | creatures \| chat \| activity+state |
| Ctrl+4 | Canvas | chat \| canvas+activity |
| Ctrl+5 | Debug | chat+state (top) / debug (bottom) |
| Ctrl+6 | Settings | settings (full screen) |

Every freshly opened instance lands on Chat Focus, whatever its shape:
the Status rail carries the creature list, so a multi-creature graph is
operable without switching layouts. The Creatures tab on that rail
exists only once the graph has more than one creature; it shows the
member count and glows when the graph grows while you are looking
elsewhere. A solo session shows no creature chrome at all.
Multi-creature stays available on Ctrl+3 for the wide layout. The last-used preset per instance is remembered in
localStorage and always wins over the default.

The panel picker and the palette list each panel with a one-line
description; the two legacy aliases (`file-tree`, `editor-status`) are
resolvable by the legacy presets but are never offered.

## Edit mode

Press **Ctrl+Shift+L** or click the edit button in the header to enter
edit mode. Each panel leaf shows an amber bar with:

- **Replace**: swap the panel with any registered panel via a picker
  modal
- **Split H / Split V**: split the current leaf into two, creating a new
  empty slot
- **Close**: remove the panel (its sibling takes the parent's space)
- **"+ Add panel"** button on empty slots

The edit mode banner at the top provides:
- **Save**: persists changes (user presets only; builtins can't be
  overwritten)
- **Save as new**: creates a new user preset with a custom name
- **Revert**: discards all changes and restores the original
- **Exit**: leaves edit mode (prompts if unsaved changes exist)

All edits happen on a deep clone of the preset. The original is never
modified until explicitly saved.

## Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+1..6 | Switch to preset |
| Ctrl+Shift+L | Toggle edit mode |
| Ctrl+K | Open command palette |
| Esc | Exit edit mode |

Ctrl+K always fires even when an input is focused. Preset shortcuts are
blocked inside text inputs/textareas.

## Command palette

Open with Ctrl+K. Fuzzy-matches against every registered command:

- `Mode: <preset>`: switch to any preset
- `Panel: <panel>`: add a panel to its preferred zone
- `Layout: edit / save as / reset`
- `Debug: open logs`

Prefix routing: `>` commands (default), `@` mentions, `#` sessions,
`/` slash commands.

## Panels

### Chat
The main conversation interface. Supports message edit+rerun,
regenerate, tool call accordion, sub-agent nesting.

### Status (tabbed rail)
Five tabs: Session (agent, model, provider, session id, status),
Creatures (only for a graph with more than one creature: every member
with focus-on-click, start / stop, and a per-creature model switch,
plus the channels; the rail icon carries the member count), Tokens (in/out/cache + context bar with compact
threshold), Jobs (running tool calls with stop button), Modules (the
full modules surface).

### Overview
The same session identity, token usage, and running jobs as one dense
scroll with no tabs. Not in any default preset; pick it from the panel
picker when you prefer everything on screen at once.

### Jobs
Model, context bar, and running jobs only. Used by the Settings preset.

### Creature State (tabbed)
Four tabs: Drives (the focused creature's drives with the same
management as the full Drives panel: pause, resume, wake, cancel,
complete, progress notes, and a New goal form; a scope toggle shows the
whole graph, and "Open full panel" opens the Drives panel as a drawer),
Scratchpad (key-value pairs from the agent's working memory), Memory
(FTS5 search over session events), Compaction (history of context
compactions).

### Drives
Every drive in the session grouped by assignee, with the full record
detail. Reachable from the panel picker, the header badge, and the
Creature State panel's "Open full panel".

### Files
File tree with refresh + a "Touched" view showing files the agent
read/wrote/errored, grouped by action.

### Editor
Monaco editor with file tabs, dirty indicators, Ctrl+S save. For
markdown files (.md/.markdown/.mdx), a toggle switches between Monaco
(code mode) and Vditor (rich WYSIWYG markdown with toolbar, math, and
code blocks).

### Canvas
Auto-detects long code blocks (15+ lines) and `##canvas##` markers from
assistant messages. Shows syntax-highlighted code with line numbers,
rendered markdown, or sandboxed HTML. Copy and download buttons in the
tab strip.

### Terminal
xterm.js terminal connected to a PTY shell (bash/PowerShell) in the
agent's working directory. Supports Nerd Font glyphs, resize, and
light/dark theme.

### Debug (tabbed)
Four tabs: Logs (live tail of the API server log via WebSocket), Trace
(waterfall of tool call timings), Prompt (current system prompt with
diff), Events (firehose of all chat store messages).

### Settings (tabbed)
Seven tabs: Session, Tokens, Jobs, Extensions (installed packages),
Triggers (active triggers), Cost (token cost estimate), Environment
(cwd + redacted env vars).

### Creatures (terrarium only)
Creature list with status dots + channel list. Click a creature to
switch the chat tab.

## Detach to window

In edit mode, panels with `supportsDetach: true` can be popped out via
the Pop Out kebab action. The detached window is a minimal shell at
`/detached/<instanceId>--<panelId>` that connects independently to the
backend.

## Status bar

Always visible at the bottom:
- Instance name + status dot
- Model quick switcher (dropdown) + settings gear
- Session id (click to copy)
- Running jobs count
- Runtime elapsed

## Technical details

The split tree is stored as a plain JSON structure:
```json
{
  "type": "split",
  "direction": "horizontal",
  "ratio": 70,
  "children": [
    { "type": "leaf", "panelId": "chat" },
    { "type": "split", "direction": "vertical", "ratio": 50,
      "children": [
        { "type": "leaf", "panelId": "activity" },
        { "type": "leaf", "panelId": "state" }
      ]
    }
  ]
}
```

The `LayoutNode.vue` component is recursive: splits render two children
with a draggable handle, leaves render the panel component via
`<component :is>`. Panel runtime props flow through Vue's
provide/inject from the route page.

## See also

- [Serving](serving.md): opening the dashboard via `kt web` / `kt app` / `kt serve`.
- [Development / Frontend](../dev/frontend.md): architecture for contributors.
