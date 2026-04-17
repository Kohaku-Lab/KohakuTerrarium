# Frontend architecture

Developer reference for the Vue 3 web dashboard. Covers the component
tree, store design, WebSocket protocols, and how to add new panels.

## Dev loop

Source lives in `src/kohakuterrarium-frontend/`. Built output lands in
`src/kohakuterrarium/web_dist/` (configured in `vite.config.js:48`),
where the FastAPI app in `api/app.py` and `serving/web.py` pick it up
as static files.

```bash
# Dev server (hot reload, points at the Python API via proxy)
npm run dev --prefix src/kohakuterrarium-frontend

# Production build (writes into src/kohakuterrarium/web_dist)
npm run build --prefix src/kohakuterrarium-frontend

# Lint / format
npm run lint   --prefix src/kohakuterrarium-frontend
npm run format --prefix src/kohakuterrarium-frontend

# Unit tests (vitest + jsdom)
npm run test   --prefix src/kohakuterrarium-frontend
```

When distributing KT, run `npm run build` so `web_dist/` is populated
before `pip install -e .` or packaging. The Python side ships the
built bundle as part of the installed package.

## Stack

- **Vue 3.5+** with `<script setup>` composition API
- **Pinia 3** for state management (options API stores for chat,
  composition API for layout/canvas/palette)
- **Vite** (rolldown-vite) with UnoCSS, unplugin-auto-import,
  unplugin-vue-components, unplugin-vue-router
- **Element Plus 2.11** for dialogs, dropdowns, selects, tooltips
- **Monaco Editor** for code editing
- **Vditor** for rich markdown editing
- **xterm.js** for the terminal panel
- **highlight.js** for canvas code viewer
- **splitpanes** (legacy, used in old SplitPane.vue component only)

## Directory structure

```
src/kohakuterrarium-frontend/src/
├── App.vue                    # Root: NavRail + router-view + global composables
├── main.js                    # Pinia + router + panel registration (sync!)
├── style.css                  # Theme variables, font stacks
├── components/
│   ├── chat/                  # ChatPanel, ChatMessage, ToolCallBlock
│   ├── chrome/                # AppHeader, StatusBar, ModelSwitcher,
│   │                            CommandPalette, ToastCenter
│   ├── common/                # StatusDot, SplitPane, GemBadge, MarkdownRenderer
│   ├── editor/                # EditorMain, MonacoEditor, VditorEditor,
│   │                            FileTree, FileTreeNode, EditorStatus
│   ├── layout/                # WorkspaceShell, LayoutNode, EditModeBanner,
│   │                            PanelHeader, PanelPicker, SavePresetModal,
│   │                            NavRail, NavItem, Zone*.vue (legacy)
│   ├── panels/                # ActivityPanel, StatePanel, FilesPanel,
│   │                            CreaturesPanel, CanvasPanel, SettingsPanel,
│   │                            DebugPanel, TerminalPanel
│   │   ├── canvas/            # CodeViewer, MarkdownViewer, HtmlViewer
│   │   ├── debug/             # LogsTab, TraceTab, PromptTab, EventsTab
│   │   └── settings/          # ModelTab, PluginsTab, ExtensionsTab, etc.
│   ├── registry/              # ConfigCard
│   └── status/                # StatusDashboard (large tabbed status panel)
├── composables/
│   ├── useKeyboardShortcuts.js  # Ctrl+1..6, Ctrl+Shift+L, Ctrl+K
│   ├── useBuiltinCommands.js    # Palette command registry
│   ├── useAutoTriggers.js       # Canvas notification, error→debug
│   ├── useArtifactDetector.js   # Scans chat for code blocks → canvas store
│   ├── useLogStream.js          # /ws/logs WebSocket composable
│   └── useFileWatcher.js        # /ws/files WebSocket composable (unused on Windows)
├── stores/
│   ├── chat.js                # WebSocket chat, messages, runningJobs, tokenUsage
│   ├── layout.js              # Presets, panels, edit mode, split tree mutations
│   ├── layoutPanels.js        # Panel + preset registration (called from main.js)
│   ├── canvas.js              # Artifact detection + storage
│   ├── files.js               # Touched files derived from chat events
│   ├── scratchpad.js          # Scratchpad REST client
│   ├── palette.js             # Command palette registry + fuzzy search
│   ├── notifications.js       # Toast + history
│   ├── instances.js           # Running instance list
│   ├── editor.js              # Open files, active file, tree
│   ├── theme.js               # Dark/light toggle
│   └── ...
├── pages/
│   ├── instances/[id].vue     # Main instance view (WorkspaceShell)
│   ├── editor/[id].vue        # Editor-focused view (WorkspaceShell)
│   ├── detached/[key].vue     # Pop-out single panel
│   ├── panel-debug.vue        # Debug page: each panel as a tab
│   ├── index.vue, new.vue, sessions.vue, registry.vue, settings.vue
│   └── ...
└── utils/
    ├── api.js                 # Axios HTTP client (all REST endpoints)
    └── layoutEvents.js        # CustomEvent bus for cross-component actions
```

## Layout system

### Binary split tree

The layout is a recursive binary tree where each node is:

```js
// Split: two children with a draggable divider
{ type: "split", direction: "horizontal"|"vertical", ratio: 0-100, children: [Node, Node] }

// Leaf: renders one panel
{ type: "leaf", panelId: "chat" }
```

`LayoutNode.vue` is the recursive renderer. For splits, it renders two
children in a flex container with a pointer-captured drag handle. For
leaves, it resolves the panel component from the layout store and
renders it via `<component :is>`.

### Panel registration

Panels are registered in `stores/layoutPanels.js` at app startup
(synchronous, before `app.mount()`):

```js
layout.registerPanel({
  id: "chat",
  label: "Chat",
  component: ChatPanel,
});
```

The `component` is wrapped in `markRaw()` internally so Vue reactivity
doesn't wrap it.

### Presets

Presets are tree definitions with an id, label, and optional shortcut:

```js
const CHAT_FOCUS = {
  id: "chat-focus",
  label: "Chat Focus",
  shortcut: "Ctrl+1",
  tree: hsplit(70, leaf("chat"), vsplit(65, leaf("status-dashboard"), leaf("state"))),
};
```

Helper functions `hsplit(ratio, left, right)`, `vsplit(ratio, top, bottom)`,
and `leaf(panelId)` create the tree nodes concisely.

### Panel props

Route pages (e.g., `pages/instances/[id].vue`) provide runtime props to
panels via Vue's `provide("panelProps", computed(() => ({...})))`. The
`LayoutNode` injects this and passes the appropriate slice to each leaf
component based on `panelId`.

### Edit mode

`layout.enterEditMode()` deep-clones the active preset. All tree
mutations (replace, split, close) operate on the clone.
`layout.exitEditMode()` restores the original. `layout.saveEditMode()`
persists the clone (user presets only).

## WebSocket protocols

### Chat (`/ws/creatures/{agent_id}` or `/ws/terrariums/{id}`)
Existing — managed by `stores/chat.js`. Streams text chunks, tool
start/done, token usage, session info, compaction events.

### Logs (`/ws/logs`)
Server process log tail. Messages: `{type: "meta"|"line"|"error", ...}`.
Lines parsed into `{ts, level, module, text}`.

### Terminal (`/ws/terminal/{agent_id}`)
PTY shell in the agent's working directory. Messages:
- Client → Server: `{type: "input", data: "..."}`, `{type: "resize", rows, cols}`
- Server → Client: `{type: "output", data: "..."}`, `{type: "error", data: "..."}`

### Files (`/ws/files/{agent_id}`)
File system watcher (watchfiles). Messages:
`{type: "ready"|"change"|"error", ...}`. Changes include path + action
(added/modified/deleted). Currently unreliable on Windows.

## Adding a new panel

1. Create `components/panels/MyPanel.vue`
2. Register in `stores/layoutPanels.js`:
   ```js
   import MyPanel from "@/components/panels/MyPanel.vue";
   layout.registerPanel({ id: "my-panel", label: "My Panel", component: MyPanel });
   ```
3. Add to a preset tree:
   ```js
   tree: hsplit(50, leaf("chat"), leaf("my-panel"))
   ```
4. If the panel needs runtime props (like `instance`), add an entry to
   the route page's `panelProps` computed.

## Theme

`stores/theme.js` manages dark/light mode. Components use
`useThemeStore().dark` reactively. CSS uses `html.dark` class for dark
mode overrides. UnoCSS `dark:` prefix works throughout.

Vditor and xterm.js have their own theme systems — both watch
`themeStore.dark` and call their respective theme-switch APIs.
