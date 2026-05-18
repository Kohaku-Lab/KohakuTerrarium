---
title: Rich CLI 多 creature
summary: 在 `kt run --mode cli` 中使用多 creature terrarium —— roster、焦点切换、@name 重定向、slash 命令、Ctrl+A 总览。
tags:
  - guides
  - cli
  - terrarium
---

# Rich CLI 多 creature

`kt run --mode cli` 打开 rich 行内 CLI。**单 creature 配置**与 1.4 行为
一致 —— 带边框的输入框、live 区域、slash 命令、prompt-toolkit 历史。
**多 creature terrarium** 则在输入框上方显示一行 **roster**，并提供
焦点切换、按 creature 保留的草稿、`@name` 重定向、以及拓扑感知的
slash 命令。

## Roster

加载多个 creature 时，输入框上方会出现一行：

```
  ┌─ Creatures ─────────────────────────────────────────────────────┐
  │ ▸clawd ● Edit src/sprite.py    physics ○ idle 2m   power-up ⚠   │
  └─────────────────────────────────────────────────────────────────┘
  [clawd]> _
```

每个槽位是 `<焦点标记><名字> <状态符号> <活动摘要>`：

| 符号 | 状态 | 含义 |
|---|---|---|
| `●` | working | LLM 正在生成，或工具 / 子 agent 正在运行 |
| `○` | idle | 没有进行中的活动 |
| `⚠` | waiting | creature 正在等你回答（`ask_user`、permgate） |
| `✗` | failed | 上一轮发生异常 |
| `■` | stopped | 已显式停止 |

`▸` 标记当前**焦点 creature** —— 你的输入会发到它。非焦点 creature 自
上次查看以来有新活动时，名字旁边会出现 `●N` 徽章。

### 终端较窄时

如果空间不够，idle / stopped 的 creature 会折叠为计数：

```
  ┌─ Creatures ─────────────────────────────────────────────────────┐
  │ ▸power-up ⚠ needs choice   ●clawd Edit src...   ●collision...   │
  │   +2 idle  +1 stopped                                           │
  └─────────────────────────────────────────────────────────────────┘
```

working 与 waiting 的 creature 始终以名字可见（它们是需要你注意的）。

## 焦点切换

| 按键 | 作用 |
|---|---|
| `Tab` | 切到下一个 creature |
| `Shift+Tab` | 切到上一个 creature |
| `Ctrl+A` | 打开 agent 总览（见下） |

切换焦点时，rich CLI 提供**完整的上下文切换** —— 你只会看到新焦点
creature 的历史，而不是所有 creature 的交错日志：

- 输入提示前缀变化：`[clawd]> ` → `[physics]> `
- live 区域（正在流式输出 + 活跃工具）切到该 creature 的缓冲
- **底栏**根据新 creature 的 agent 重绘 —— 模型名、上下文预算、token
  累计都反映 `physics` 实际在跑的内容，而不是 `clawd` 之前的内容
- **未发送的草稿与你之前定位的 creature 一起保留** —— 切回来时半写
  的消息还在
- **终端滚动历史会清空并重新回放**：新焦点 creature 的每一条已提交
  消息、工具结果面板、提示都会重新发到滚动区。PgUp / 鼠标向上滚动
  之后只会看到该 creature 的历史。共享的交错日志不复存在 —— Tab 是
  真正的上下文切换，而不是「偷看」。

重绘由对话进行时在内存中捕获的「每 creature commit 日志」驱动。
尚未完成的流式文本不会被捕获（它还在产生它的 creature 的 live
区里），但所有已提交到滚动区的内容 —— 用户消息、完成的助理回合、
工具块、子代理面板 —— 都会在每次切换时忠实重放。

一个自然的代价：长时间运行、creature 数量多时，每次 Tab 都会触发
真正的屏幕重绘。几百回合内你几乎感觉不到；非常长的会话中，切换会
是 CLI 里最慢的交互动作。

## `@name` 重定向

要在不切换焦点的情况下向某个 creature 发一条消息，在输入前加
`@<name>`：

```
  [clawd]> @physics 碰撞检测返回了什么？
                ↑ 发给 physics，焦点仍在 clawd
```

`@all <msg>` 广播到所有 creature，但只有焦点是**特权** creature
（recipe 根或用户启动的顶层）时才有效。

`@name` 消息记录在**接收方**的滚动历史中，不在发送方 ——
之后切到 `physics` 你能看到问题和 physics 的回答在一起，不会孤立。
`@all` 广播会写入每个 creature 的历史，这样切到哪个 creature
都能看到同样的可见上下文。

## Slash 命令

原有的 slash 命令（`/clear`、`/model`、`/status`、`/scratchpad` 等）
作用于**焦点** creature。1.5 新增的拓扑感知命令：

| 命令 | 作用 |
|---|---|
| `/stop` | 停止焦点 creature |
| `/stop <name>` | 停止指定 creature |
| `/start` / `/start <name>` | 启动已停止的 creature |
| `/spawn <recipe>` | 启动一个新 creature（仅特权焦点） |
| `/jobs` | 列出焦点 creature 正在运行的 jobs |
| `/channels` | 列出焦点 creature 参与的 channels |
| `/scratchpad` | 显示焦点 creature 的草稿区 |

## Ctrl+A — agent 总览

按 `Ctrl+A` 打开按状态分组的完整列表：

```
  ┌─ Agent view ───────────────────────────────────────────────────┐
  │ Filter: [           ]                              Esc to close│
  ├────────────────────────────────────────────────────────────────┤
  │ Needs input                                                    │
  │   ⚠ power-up        needs: double jump or wall climb?    15s   │
  │ Working                                                        │
  │   ● clawd           Edit src/sprite.py                    3s   │
  │   ● collision       bash: pytest tests/collision.py      12s   │
  │ Idle                                                           │
  │   ○ physics         idle 2m                                    │
  │ Stopped                                                        │
  │   ■ debug-helper    stopped 30m ago                            │
  ├────────────────────────────────────────────────────────────────┤
  │ ↑↓ select  Space peek  Enter focus  Esc close                  │
  └────────────────────────────────────────────────────────────────┘
```

| 按键 | 动作 |
|---|---|
| `↑` / `↓` | 移动选择 |
| `Space` | peek 选中 creature（右侧预览，不切焦点） |
| `Enter` | 切到选中 creature 并关闭总览 |
| `→` | （peek 时）将 peek 提升为焦点 |
| `Esc` | 关闭总览 |
| _键入_ | 按名字 / 活动过滤列表（不区分大小写） |

### Peek

`Space` 在选中行打开右侧面板，显示该 creature 最近 30 秒的输出。
**peek 打开时键入的内容会发到 peek 的 creature** —— 用于不切焦点回答
`ask_user` 提示。

## 何时选用此前端

| 你想…… | 使用 |
|---|---|
| 单 creature 聊天，快速、写入滚动历史 | `kt run --mode cli`（本指南） |
| 可视化图编辑、多 tab、web | `kt app` / `kt serve`（web UI） |
| creature 树、channel 转录、鼠标 | `kt run --mode tui`（Textual） |

rich CLI 的多 creature 表面偏向键盘优先；如需可视化拓扑编辑或浏览器
attach，请用 web UI。

## 另请参阅

- [`kt --help`](../../README.md) — 完整 CLI 参考
- [CLI 与 UI 对照](cli-and-ui-equivalents.md) — 每个 `kt` 命令对应的 UI 位置
- [配置指南](configuration.md) — terrarium recipe 的样子
