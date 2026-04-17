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
| Ctrl+1 | Chat Focus | chat \| status-dashboard (top) + state (bottom) |
| Ctrl+2 | Workspace | files \| editor+terminal \| chat+activity |
| Ctrl+3 | Multi-creature | creatures \| chat \| activity+state |
| Ctrl+4 | Canvas | chat \| canvas+activity |
| Ctrl+5 | Debug | chat+state (top) / debug (bottom) |
| Ctrl+6 | Settings | settings (full screen) |

The instance page auto-selects Chat Focus for creatures and
Multi-creature for terrariums. The last-used preset per instance is
remembered in localStorage.

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

### Activity (tabbed)
Three tabs: Session (id, cwd, creatures/channels), Tokens (in/out/cache
+ context bar with compact threshold), Jobs (running tool calls with
stop button).

### State (tabbed)
Four tabs: Scratchpad (key-value pairs from the agent's working memory),
Tool History (all tool calls from the session), Memory (FTS5 search over
session events), Compaction (history of context compactions).

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

- [Serving](serving.md) — opening the dashboard via `kt web` / `kt app` / `kt serve`.
- [Development / Frontend](../dev/frontend.md) — architecture for contributors.
