---
title: CLI 与 UI 对照表
summary: 每个 `kt` 子命令在前端的位置（或刻意保持 CLI-only 的原因）。
tags:
  - guides
  - cli
  - desktop
  - reference
---

# CLI 与 UI 对照表

KohakuTerrarium 的 `kt` CLI 和 Vue 桌面应用是同一个引擎的两种界面。
本页面是**完整的对照参考**，让你在二者之间切换时无需猜测。

图例：

- ✅ — 已在 UI 提供，无需离开桌面应用。
- ⚠️ — 部分支持；UI 覆盖常用路径，少数边缘场景仍需 CLI（见 *备注*）。
- 🔧 — 故意保持 CLI-only（部署 / 运维）。UI 刻意不暴露。

## 身份 / 配置

| `kt` 命令 | UI 入口 | 状态 |
|---|---|---|
| `kt config provider list/add/edit/delete` | 设置 → **Providers** | ✅ |
| `kt config llm list/show/add/edit/delete` | 设置 → **Models** | ✅ |
| `kt config llm default <name>` | 设置 → Models → 预设编辑器 → **设为默认** | ✅ |
| `kt model list/show/default` | 设置 → **Models**（`config llm *` 的别名） | ✅ |
| `kt config key list/set` | 设置 → Providers → 内联密钥字段 | ✅ |
| `kt config key delete` | 设置 → Providers → 已保存密钥旁的垃圾桶 | ✅ |
| `kt login codex` | 设置 → Providers → Codex 行 → **使用 Codex 登录** | ✅ |
| `kt login <openai/anthropic/...>` | 设置 → Providers → 内联密钥字段（这些提供方使用 API 密钥，无 OAuth） | ✅ |
| `kt config mcp list/add/delete` | 设置 → **MCP servers** | ✅ |
| `kt config mcp edit` | 设置 → MCP servers → 行 **编辑** 按钮 → 弹窗 | ✅ |
| `kt config show / path` | 设置 → **高级** → 文件表 | ✅ |
| `kt config edit <name>` | 设置 → 高级 → 行 **编辑** | ✅ |

## 会话（Sessions）

| `kt` 命令 | UI 入口 | 状态 |
|---|---|---|
| `kt list`（会话） | Sessions 标签 — 已保存会话列表 | ✅ |
| `kt resume <session>` | Sessions 标签 → 行 → **继续** | ✅ |
| `kt search <session> <query> --mode --agent -k` | Session viewer → **Find** 标签 | ✅ |
| `kt embedding <session> --provider --model --dimensions` | Sessions 标签 → 行 ⋮ 菜单 → **构建嵌入向量**（或 Find 标签空状态横幅） | ✅ |

## 包管理（Packages）

| `kt` 命令 | UI 入口 | 状态 |
|---|---|---|
| `kt list`（目录） | Catalog 标签 | ✅ |
| `kt info <agent_path>` | Catalog → 卡片 → **信息** 抽屉 | ✅ |
| `kt install <git/local/pypi>` | Catalog → **从 URL 安装** 弹窗 | ✅ |
| `kt uninstall <name>` | Catalog → 卡片 → **卸载** | ✅ |
| `kt update [target] [--all]` | Catalog → 卡片 → **更新**，或工具栏 **全部更新** | ✅ |
| `kt edit @pkg/...` | Catalog → 卡片 → **编辑文件** 抽屉（应用内 YAML 编辑器） | ✅ |
| `kt extension list` | Extensions 标签（顶级） | ✅ |
| `kt extension info <name>` | Extensions 标签 → 行 | ✅ |
| `kt mcp list --agent <path>` | 设置 → MCP → 行 **编辑** 弹窗 → "Used by" 列表 | ✅ |

## 更新 / 自更新

| `kt` 命令 | UI 入口 | 状态 |
|---|---|---|
| `kt self-update` | 设置 → **Updates** | ✅ |
| `kt self-update --source/--spec` | 设置 → Updates → 源选择 | ✅ |
| `kt self-update --check-only / --dry-run` | 设置 → Updates → **立即检查** | ⚠️（无 dry-run UI；罕见到保持 CLI-only） |

## 关于 / 诊断

| `kt` 命令 | UI 入口 | 状态 |
|---|---|---|
| `kt --version --verbose` | 设置 → **关于** → 诊断信息面板 | ✅ |
| `kt serve logs --follow --lines --level` | 设置 → 关于 → **查看服务器日志** | ✅ |
| `kt serve status` | 设置 → 关于 → 守护进程区段 | ✅ |
| `kt serve start/stop/restart` | 应用 **本身就是** 守护进程；隐式运行 | ✅ |

## Lab / 多节点

| `kt` 命令 | UI 入口 | 状态 |
|---|---|---|
| `kt host` / `kt serve start --mode lab-host` | 设置 → **Sites**（仅 lab-host 模式可见） | ⚠️（host 进程仍需通过 CLI / `kt service` 启动） |
| `kt client` / `kt lab-client` | 设置 → Sites → **启动客户端向导** 生成精确命令 | ⚠️（你需将其粘贴到 worker 主机的终端） |
| 断开 worker | 设置 → Sites → 行菜单 → **断开连接** | ✅ |
| 封锁 worker | 设置 → Sites → 行菜单 → **封锁** | ✅ |
| 轮换配对令牌 | 设置 → Sites → **轮换配对令牌** | ✅ |

## 部署 / 系统服务

| `kt` 命令 | UI 入口 | 状态 |
|---|---|---|
| `kt service install/uninstall/status/edit` | （无） | 🔧 故意为之 — systemd 单元安装需要在服务器上以 root 运行。运维使用 CLI 或 Ansible / Docker。 |

## 另见

- [桌面 UI 导览](desktop-ui-walkthrough.md) — 各标签的引导。
- [App update](app-update.md) — 完整更新流程内部细节。
- [Serving 指南](serving.md) — 守护进程如何接线。
- [`kt --help`](../../README.md) — 终端侧参考。
