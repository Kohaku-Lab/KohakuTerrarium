---
title: 前端架构
summary: Vue 3 仪表板布局、状态存储、WebSocket 连接和 UI 贡献方式。
tags:
  - dev
  - frontend
---
# 前端架构

这篇文档面向前端和全栈开发者，主要介绍 Vue 3 仪表板的组织方式、状态放在哪里、WebSocket 如何流转，以及如何新增 panel。

## 开发流程

源码位于 `src/kohakuterrarium-frontend/`。构建产物会输出到 `src/kohakuterrarium/web_dist/`（配置见 `vite.config.js:48`），然后由 `api/app.py` 和 `serving/web.py` 中的 FastAPI 作为静态文件提供。

```bash
# 开发服务器（热更新，通过代理连接 Python API）
npm run dev --prefix src/kohakuterrarium-frontend

# 生产构建（输出到 src/kohakuterrarium/web_dist）
npm run build --prefix src/kohakuterrarium-frontend

# Lint / 格式化
npm run lint   --prefix src/kohakuterrarium-frontend
npm run format --prefix src/kohakuterrarium-frontend

# 单元测试（vitest + jsdom）
npm run test   --prefix src/kohakuterrarium-frontend
```

发版前先运行 `npm run build`，确认 `web_dist/` 已生成，再去执行 `pip install -e .` 或打包。否则 Python 包中不会包含前端 bundle。

## 技术栈

- **Vue 3.5+**，使用 `<script setup>` 和 Composition API
- **Pinia 3** 做状态管理（chat 使用 options store，layout/canvas/palette 使用 composition store）
- **Vite**（rolldown-vite），外加 UnoCSS、unplugin-auto-import、unplugin-vue-components、unplugin-vue-router
- **Element Plus 2.11**，主要用于 dialog、dropdown、select、tooltip
- **Monaco Editor**，代码编辑器
- **Vditor**，Markdown 编辑器
- **xterm.js**，终端面板
- **highlight.js**，canvas 中的代码查看器
- **splitpanes**，较老的组件，现在基本只剩 `SplitPane.vue` 还在使用

## 目录结构

```
src/kohakuterrarium-frontend/src/
├── App.vue                    # 根组件：NavRail + router-view + 全局 composable
├── main.js                    # Pinia + router + panel 注册（同步执行）
├── style.css                  # 主题变量、字体栈
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
│   └── status/                # StatusDashboard（标签页式状态面板）
├── composables/
│   ├── useKeyboardShortcuts.js  # Ctrl+1..6, Ctrl+Shift+L, Ctrl+K
│   ├── useBuiltinCommands.js    # 命令面板注册表
│   ├── useAutoTriggers.js       # Canvas 通知、error→debug
│   ├── useArtifactDetector.js   # 扫描 chat 中的代码块 → 写入 canvas store
│   ├── useLogStream.js          # /ws/logs 的 WebSocket composable
│   └── useFileWatcher.js        # /ws/files 的 WebSocket composable（Windows 上不可用）
├── stores/
│   ├── chat.js                # WebSocket chat、messages、runningJobs、tokenUsage
│   ├── layout.js              # Preset、panel、编辑模式、split tree 变更
│   ├── layoutPanels.js        # Panel + preset 注册（由 main.js 调用）
│   ├── canvas.js              # Artifact 检测与存储
│   ├── files.js               # 从 chat event 推导 touched files
│   ├── scratchpad.js          # Scratchpad REST client
│   ├── palette.js             # 命令面板注册 + 模糊搜索
│   ├── notifications.js       # Toast + 历史记录
│   ├── instances.js           # 正在运行的实例列表
│   ├── editor.js              # 已打开文件、当前文件、文件树
│   ├── theme.js               # 深浅色切换
│   └── ...
├── pages/
│   ├── instances/[id].vue     # 主实例页面（WorkspaceShell）
│   ├── editor/[id].vue        # 编辑器优先页面（WorkspaceShell）
│   ├── detached/[key].vue     # 弹出单独面板
│   ├── panel-debug.vue        # 调试页：每个 panel 一个 tab
│   ├── index.vue, new.vue, sessions.vue, registry.vue, settings.vue
│   └── ...
└── utils/
    ├── api.js                 # Axios HTTP client（所有 REST endpoint）
    └── layoutEvents.js        # 跨组件动作的 CustomEvent bus
```

## 布局系统

### 二叉分割树

布局本质上是一棵递归二叉树。每个节点不是 split，就是 leaf：

```js
// Split：两个子节点，中间有可拖动的分隔条
{ type: "split", direction: "horizontal"|"vertical", ratio: 0-100, children: [Node, Node] }

// Leaf：渲染一个 panel
{ type: "leaf", panelId: "chat" }
```

`LayoutNode.vue` 负责递归渲染。遇到 split 时，会在 flex 容器中渲染两个子节点，并加上可拖动的 handle；遇到 leaf 时，则去 layout store 查找对应的 panel 组件，再通过 `<component :is>` 渲染。

### Panel 注册

Panel 会在应用启动时注册到 `stores/layoutPanels.js`，而且必须同步注册，也就是要在 `app.mount()` 之前完成：

```js
layout.registerPanel({
  id: "chat",
  label: "Chat",
  component: ChatPanel,
});
```

`component` 内部会先经过一层 `markRaw()`，避免 Vue 将其纳入响应式系统。

### Preset

Preset 就是一份布局树，再加上 id、label，必要时还可以带快捷键：

```js
const CHAT_FOCUS = {
  id: "chat-focus",
  label: "Chat Focus",
  shortcut: "Ctrl+1",
  tree: hsplit(70, leaf("chat"), vsplit(65, leaf("status-dashboard"), leaf("state"))),
};
```

`hsplit(ratio, left, right)`、`vsplit(ratio, top, bottom)`、`leaf(panelId)` 这些辅助函数，就是为了减少手写树结构的负担。

### Panel props

路由页面，例如 `pages/instances/[id].vue`，会通过 `provide("panelProps", computed(() => ({...})))` 向 panel 提供运行时 props。然后由 `LayoutNode` 再 `inject` 这个值，并按 `panelId` 将对应 props 传给每个 leaf 组件。

### 编辑模式

`layout.enterEditMode()` 会深拷贝当前激活的 preset。后续的 replace、split、close 等操作，修改的都是这个副本。
`layout.exitEditMode()` 会丢弃副本，回到原始版本。
`layout.saveEditMode()` 会保存副本，但只保存用户 preset。

## WebSocket 协议

### Chat（`/ws/creatures/{agent_id}` 或 `/ws/terrariums/{id}`）

这一部分已经有现成实现，位于 `stores/chat.js`。它会流式接收文本 chunk、tool start/done、token usage、session 信息以及 compaction 事件。

### Logs（`/ws/logs`）

服务端日志 tail。消息格式为 `{type: "meta"|"line"|"error", ...}`。
日志行会被解析为 `{ts, level, module, text}`。

### Terminal（`/ws/terminal/{agent_id}`）

agent 工作目录下的 PTY shell。消息格式如下：
- Client → Server: `{type: "input", data: "..."}`、`{type: "resize", rows, cols}`
- Server → Client: `{type: "output", data: "..."}`、`{type: "error", data: "..."}`

### Files（`/ws/files/{agent_id}`）

文件系统 watcher（watchfiles）。消息格式为：
`{type: "ready"|"change"|"error", ...}`。`change` 会带 `path` 和 `action`（added/modified/deleted）。这一部分在 Windows 上还不够稳定。

## 新增一个 panel

1. 新建 `components/panels/MyPanel.vue`
2. 在 `stores/layoutPanels.js` 中注册：
   ```js
   import MyPanel from "@/components/panels/MyPanel.vue";
   layout.registerPanel({ id: "my-panel", label: "My Panel", component: MyPanel });
   ```
3. 把它加入某个 preset tree：
   ```js
   tree: hsplit(50, leaf("chat"), leaf("my-panel"))
   ```
4. 如果这个 panel 需要运行时 props，例如 `instance`，就在路由页面的 `panelProps` computed 中加上一项。

## 主题

`stores/theme.js` 负责管理深浅色模式。组件中直接使用 `useThemeStore().dark`。CSS 侧使用 `html.dark` 进行深色覆盖，UnoCSS 的 `dark:` 前缀也可以继续使用。

Vditor 和 xterm.js 还有各自的主题系统。它们都会监听 `themeStore.dark`，然后调用各自的切换 API。
