---
title: 前端布局
summary: Vue 3 Dashboard 怎么编排、在哪里扩展、事件怎么从后端流到 UI。
tags:
 - guides
 - frontend
 - ui
---

# 前端布局

给使用或自定义 `kt web` / `kt app` / `kt serve` 提供的 web dashboard 的读者。

Dashboard 用的是可设置的二元 split tree：每个区块不是 leaf (一个 panel) 就是 split (两个子节点加一个可拖的分隔线)。Preset 可以一次换掉整棵树；edit 模式可以就地调整。

参考：[服务部署指南](serving.md) — 怎么把 dashboard 打开。

## 核心概念

- **Panel**：单一职责的 view (Chat、Files、Activity、State、Canvas、Debug、Settings、Terminal 等等)。Panel 在 `stores/layoutPanels.js` 注册，以 id 查询。
- **Split tree**：二元树，每个节点不是 *leaf* (渲染一个 panel) 就是 *split* (把空间切成两半，中间有个可拖的分隔线)。Split 可以是水平 (左 | 右) 或垂直 (上 / 下)。
- **Preset**：一棵具名的 split tree 设置。切 preset 会直接换掉整棵树。Preset 分两种：内置 (KT 附带的) 与 user 自定义。
- **Header**：顶列，放 instance 信息、preset 下拉、编辑布局按钮、Ctrl+K 开 palette、stop 按钮。
- **Status bar**：底列，放 model 快速切换、session id、job 数、执行时间。

## 默认的 preset

| 快捷键 | Preset | 布局 |
|----------|--------|--------|
| Ctrl+1 | Chat Focus | chat \| status-dashboard (上) + state (下) |
| Ctrl+2 | Workspace | files \| editor+terminal \| chat+activity |
| Ctrl+3 | Multi-creature | creatures \| chat \| activity+state |
| Ctrl+4 | Canvas | chat \| canvas+activity |
| Ctrl+5 | Debug | chat+state (上) / debug (下) |
| Ctrl+6 | Settings | settings (全萤幕) |

Instance 页会自动：Creature用 Chat Focus、Terrarium用 Multi-creature。每个 instance 上次用的 preset 存在 localStorage。

## Edit 模式

按 **Ctrl+Shift+L** 或点 header 的编辑按钮进 edit 模式。每个 panel leaf 会出现一条琥珀色的 bar：

- **Replace**：通过 picker modal 把 panel 换成任何已注册的 panel
- **Split H / Split V**：把当前 leaf 切两半，产生一个新的空 slot
- **Close**：移除这个 panel (兄弟节点会接手父节点的空间)
- **"+ Add panel"** 按钮，出现在空 slot 上

顶部的 edit 模式 banner 提供：
- **Save**：存回去 (只限 user preset；内置 preset 不能覆盖)
- **Save as new**：用自定义名字另存新的 user preset
- **Revert**：丢掉所有变更，还原原本
- **Exit**：离开 edit 模式 (如果有未存的变更会问一下)

所有编辑都跑在 preset 的深 clone 上。除非你明确存档，原本永远不会被动到。

## 键盘快捷键

| 快捷键 | 动作 |
|----------|--------|
| Ctrl+1..6 | 切换到某个 preset |
| Ctrl+Shift+L | 切换 edit 模式 |
| Ctrl+K | 开 command palette |
| Esc | 离开 edit 模式 |

Ctrl+K 就算 input 聚焦也会触发。Preset 快捷键在 text input/textarea 里会被挡掉。

## Command palette

按 Ctrl+K 打开。对所有已注册指令做模糊比对：

- `Mode: <preset>`：切换到任一 preset
- `Panel: <panel>`：把 panel 加到它偏好的区域
- `Layout: edit / save as / reset`
- `Debug: open logs`

前缀路由：`>` 指令 (默认)、`@` mention、`#` 会话、`/` slash 指令。

## Panel 介绍

### Chat
主要对话介面。支持消息编辑+重跑、重新生成、工具调用折叠、子代理巢状显示。

### Activity (分页)
三个分页：Session (id、cwd、Creature/频道)、Tokens (in/out/cache + context bar 与压缩门槛)、Jobs (执行中的工具调用与 stop 按钮)。

### State (分页)
四个分页：Scratchpad (代理工作记忆的 key-value)、Tool History (这个会话所有工具调用)、Memory (对会话事件做 FTS5 搜索)、Compaction (历次压缩记录)。

### Files
文件树加 refresh，再加一个 "Touched" view：按动作分组显示代理读过/写过/错过的文件。

### Editor
Monaco editor，有文件 tab、脏状态指示、Ctrl+S 存档。Markdown 档 (.md/.markdown/.mdx) 可以切换 Monaco (代码模式) 与 Vditor (有工具列、数学、代码区块的 WYSIWYG markdown)。

### Canvas
自动侦测助理消息里的长 code block (15+ 行) 与 `##canvas##` 标记。显示语法 highlight 的代码 (附行号)、渲染好的 markdown、或 sandboxed HTML。Tab 上有复制与下载按钮。

### Terminal
xterm.js terminal，连到代理工作目录下的 PTY shell (bash/PowerShell)。支持 Nerd Font 字符、resize、明暗主题。

### Debug (分页)
四个分页：Logs (通过 WebSocket 即时 tail API server log)、Trace (工具调用时序瀑布图)、Prompt (目前 system prompt 加 diff)、Events (chat store 所有消息)。

### Settings (分页)
七个分页：Session、Tokens、Jobs、Extensions (已安装包)、Triggers (当前触发器)、Cost (token 成本估算)、Environment (cwd + 打马赛克的环境变量)。

### Creatures (只有Terrarium才有)
Creature列表加状态 dot、加频道列表。点一只 Creature就切到它的 chat tab。

## 弹出成独立视窗

在 edit 模式下，`supportsDetach: true` 的 panel 可以通过 Pop Out kebab 动作弹出去。弹出的视窗是个最小壳 `/detached/<instanceId>--<panelId>`，独立连到后端。

## Status bar

永远在底部：
- Instance 名称加状态 dot
- Model 快速切换 (下拉) 加设置齿轮
- Session id (点一下复制)
- 执行中的 job 数
- 已跑时间

## 技术细节

Split tree 存成纯 JSON：
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

`LayoutNode.vue` 是递回元件：split 会渲两个子节点加一个可拖的分隔线，leaf 用 `<component:is>` 渲 panel 元件。Panel 的执行期 props 通过 Vue 的 provide/inject 从 route 页流下来。

## 延伸阅读

- [服务部署指南](serving.md) — 用 `kt web` / `kt app` / `kt serve` 打开 dashboard。
- [Development / Frontend](../dev/frontend.md) — 给贡献者的架构文件。
