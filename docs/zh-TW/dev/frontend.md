---
title: 前端
summary: Vue 3 dashboard 的版面、狀態 store、WebSocket 接線，以及怎麼貢獻 UI 變更。
tags:
  - dev
  - frontend
---

# 前端架構

Vue 3 web dashboard 的開發者參考。包含元件樹、store 設計、WebSocket 協定，以及怎麼加新 panel。

## 開發迴圈

原始碼在 `src/kohakuterrarium-frontend/`。build 完的輸出會落到 `src/kohakuterrarium/web_dist/` (在 `vite.config.js:48` 設定)，然後 `api/app.py` 與 `serving/web.py` 的 FastAPI app 會把它當靜態檔案讀出來。

```bash
# Dev server (hot reload，透過 proxy 指到 Python API)
npm run dev --prefix src/kohakuterrarium-frontend

# Production build (寫入 src/kohakuterrarium/web_dist)
npm run build --prefix src/kohakuterrarium-frontend

# Lint / format
npm run lint   --prefix src/kohakuterrarium-frontend
npm run format --prefix src/kohakuterrarium-frontend

# Unit tests (vitest + jsdom)
npm run test   --prefix src/kohakuterrarium-frontend
```

要發布 KT 時，先跑 `npm run build` 把 `web_dist/` 填好，再 `pip install -e .` 或打包。Python 端會把 build 好的 bundle 當成套件的一部分一起發出去。

## 技術堆疊

- **Vue 3.5+** 配 `<script setup>` composition API
- **Pinia 3** 做狀態管理 (chat 用 options API store；layout/canvas/palette 用 composition API)
- **Vite** (rolldown-vite) 配 UnoCSS、unplugin-auto-import、unplugin-vue-components、unplugin-vue-router
- **Element Plus 2.11** 提供 dialog、dropdown、select、tooltip
- **Monaco Editor** 用來編程式碼
- **Vditor** 用來編 markdown
- **xterm.js** 撐 terminal panel
- **highlight.js** 給 canvas code viewer
- **splitpanes** (舊東西，只有老的 SplitPane.vue 還在用)

## 目錄結構

```
src/kohakuterrarium-frontend/src/
├── App.vue                    # 根元件：NavRail + router-view + 全域 composable
├── main.js                    # Pinia + router + panel 註冊 (同步！)
├── style.css                  # 主題變數、字型 stack
├── components/
│   ├── chat/                  # ChatPanel、ChatMessage、ToolCallBlock
│   ├── chrome/                # AppHeader、StatusBar、ModelSwitcher、
│   │                            CommandPalette、ToastCenter
│   ├── common/                # StatusDot、SplitPane、GemBadge、MarkdownRenderer
│   ├── editor/                # EditorMain、MonacoEditor、VditorEditor、
│   │                            FileTree、FileTreeNode、EditorStatus
│   ├── layout/                # WorkspaceShell、LayoutNode、EditModeBanner、
│   │                            PanelHeader、PanelPicker、SavePresetModal、
│   │                            NavRail、NavItem、Zone*.vue (舊)
│   ├── panels/                # ActivityPanel、StatePanel、FilesPanel、
│   │                            CreaturesPanel、CanvasPanel、SettingsPanel、
│   │                            DebugPanel、TerminalPanel
│   │   ├── canvas/            # CodeViewer、MarkdownViewer、HtmlViewer
│   │   ├── debug/             # LogsTab、TraceTab、PromptTab、EventsTab
│   │   └── settings/          # ModelTab、PluginsTab、ExtensionsTab…
│   ├── registry/              # ConfigCard
│   └── status/                # StatusDashboard (大的分頁狀態 panel)
├── composables/
│   ├── useKeyboardShortcuts.js  # Ctrl+1..6、Ctrl+Shift+L、Ctrl+K
│   ├── useBuiltinCommands.js    # Palette command registry
│   ├── useAutoTriggers.js       # Canvas 通知、error→debug
│   ├── useArtifactDetector.js   # 掃 chat 裡的 code block → canvas store
│   ├── useLogStream.js          # /ws/logs WebSocket composable
│   └── useFileWatcher.js        # /ws/files WebSocket composable (Windows 上沒在用)
├── stores/
│   ├── chat.js                # WebSocket chat、messages、runningJobs、tokenUsage
│   ├── layout.js              # Presets、panels、edit mode、split tree 變動
│   ├── layoutPanels.js        # Panel + preset 註冊 (main.js 會呼叫)
│   ├── canvas.js              # Artifact 偵測與儲存
│   ├── files.js               # 從 chat 事件衍生出來的被動過的檔案
│   ├── scratchpad.js          # Scratchpad REST client
│   ├── palette.js             # Command palette registry + 模糊搜尋
│   ├── notifications.js       # Toast + 歷史紀錄
│   ├── instances.js           # 執行中的 instance 清單
│   ├── editor.js              # 開著的檔案、當前檔案、檔案樹
│   ├── theme.js               # 暗色/亮色切換
│   └── ...
├── pages/
│   ├── instances/[id].vue     # 主 instance view (WorkspaceShell)
│   ├── editor/[id].vue        # 編輯器優先的 view (WorkspaceShell)
│   ├── detached/[key].vue     # 彈出的單一 panel
│   ├── panel-debug.vue        # Debug 頁：每個 panel 一個 tab
│   ├── index.vue、new.vue、sessions.vue、registry.vue、settings.vue
│   └── ...
└── utils/
    ├── api.js                 # Axios HTTP client (所有 REST 端點)
    └── layoutEvents.js        # 跨元件動作用的 CustomEvent bus
```

## 版面系統

### 二元 split tree

版面是一棵遞迴的二元樹，每個節點是：

```js
// Split：兩個子節點加一個可拖的分隔線
{ type: "split", direction: "horizontal"|"vertical", ratio: 0-100, children: [Node, Node] }

// Leaf：渲染一個 panel
{ type: "leaf", panelId: "chat" }
```

`LayoutNode.vue` 是遞迴的 renderer。遇到 split 就在 flex container 裡畫兩個子節點加一個 pointer-captured 的拖曳手把；遇到 leaf 就從 layout store 查出對應的 panel 元件，用 `<component :is>` 掛上去。

### Panel 註冊

Panel 在 app 啟動時於 `stores/layoutPanels.js` 註冊 (同步、在 `app.mount()` 之前)：

```js
layout.registerPanel({
  id: "chat",
  label: "Chat",
  component: ChatPanel,
});
```

`component` 會被包一層 `markRaw()`，讓 Vue 的 reactivity 不會把它包起來。

### Presets

Preset 是帶 id、label、與可選快捷鍵的 tree 定義：

```js
const CHAT_FOCUS = {
  id: "chat-focus",
  label: "Chat Focus",
  shortcut: "Ctrl+1",
  tree: hsplit(70, leaf("chat"), vsplit(65, leaf("status-dashboard"), leaf("state"))),
};
```

Helper 函式 `hsplit(ratio, left, right)`、`vsplit(ratio, top, bottom)`、`leaf(panelId)` 讓 tree 節點可以寫得精簡。

### Panel props

Route 頁面 (例如 `pages/instances/[id].vue`) 透過 Vue 的 `provide("panelProps", computed(() => ({...})))` 餵執行期 props。`LayoutNode` 把它 inject 進來，再依照 `panelId` 把對應的一塊切給每個 leaf 元件。

### Edit mode

`layout.enterEditMode()` 會深 clone 當前 preset。所有 tree 變動 (replace、split、close) 都跑在 clone 上。`layout.exitEditMode()` 還原原本；`layout.saveEditMode()` 把 clone 存起來 (只限 user preset)。

## WebSocket 協定

### Chat (`/ws/creatures/{agent_id}` 或 `/ws/terrariums/{id}`)
已存在 — 由 `stores/chat.js` 管理。串流文字 chunk、tool start/done、token usage、session info、壓縮事件。

### Logs (`/ws/logs`)
Server 程序的 log tail。訊息：`{type: "meta"|"line"|"error", ...}`。Line 會解析成 `{ts, level, module, text}`。

### Terminal (`/ws/terminal/{agent_id}`)
代理工作目錄下的 PTY shell。訊息：
- Client → Server：`{type: "input", data: "..."}`、`{type: "resize", rows, cols}`
- Server → Client：`{type: "output", data: "..."}`、`{type: "error", data: "..."}`

### Files (`/ws/files/{agent_id}`)
檔案系統 watcher (watchfiles)。訊息：`{type: "ready"|"change"|"error", ...}`。Change 會帶 path + action (added/modified/deleted)。目前在 Windows 上不太可靠。

## 加新 panel

1. 建 `components/panels/MyPanel.vue`
2. 在 `stores/layoutPanels.js` 註冊：
   ```js
   import MyPanel from "@/components/panels/MyPanel.vue";
   layout.registerPanel({ id: "my-panel", label: "My Panel", component: MyPanel });
   ```
3. 加進某個 preset 的 tree：
   ```js
   tree: hsplit(50, leaf("chat"), leaf("my-panel"))
   ```
4. 如果這個 panel 需要執行期 props (像 `instance`)，請在對應 route 頁面的 `panelProps` computed 裡加上一個 entry。

## 主題

`stores/theme.js` 管 dark/light 模式。元件用 `useThemeStore().dark` 反應式讀取。CSS 以 `html.dark` class 做暗色覆寫。UnoCSS 的 `dark:` prefix 整個 app 都通。

Vditor 與 xterm.js 各自有主題系統 — 兩邊都 watch `themeStore.dark`、呼叫它們各自的主題切換 API。
