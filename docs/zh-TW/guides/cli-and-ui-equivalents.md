---
title: CLI 與 UI 對照表
summary: 每個 `kt` 子指令在前端的位置（或刻意保持 CLI-only 的原因）。
tags:
  - guides
  - cli
  - desktop
  - reference
---

# CLI 與 UI 對照表

KohakuTerrarium 的 `kt` CLI 和 Vue 桌面 app 是同一個引擎的兩種介面。
本頁是**完整的對照參考**，讓你在兩者之間切換時無需猜測。

圖例：

- ✅ — 已在 UI 提供，無須離開桌面 app。
- ⚠️ — 部分支援；UI 涵蓋常用路徑，少數邊緣場景仍需 CLI（見 *備註*）。
- 🔧 — 刻意保持 CLI-only（部署 / 維運）。UI 刻意不暴露。

## 身份 / 設定

| `kt` 指令 | UI 入口 | 狀態 |
|---|---|---|
| `kt config provider list/add/edit/delete` | 設定 → **Providers** | ✅ |
| `kt config llm list/show/add/edit/delete` | 設定 → **Models** | ✅ |
| `kt config llm default <name>` | 設定 → Models → 預設編輯器 → **設為預設** | ✅ |
| `kt model list/show/default` | 設定 → **Models**（`config llm *` 的別名） | ✅ |
| `kt config key list/set` | 設定 → Providers → 行內金鑰欄位 | ✅ |
| `kt config key delete` | 設定 → Providers → 已儲存金鑰旁的垃圾桶 | ✅ |
| `kt login codex` | 設定 → Providers → Codex 列 → **使用 Codex 登入** | ✅ |
| `kt login <openai/anthropic/...>` | 設定 → Providers → 行內金鑰欄位（這些供應商使用 API 金鑰，沒有 OAuth） | ✅ |
| `kt config mcp list/add/delete` | 設定 → **MCP servers** | ✅ |
| `kt config mcp edit` | 設定 → MCP servers → 列 **編輯** 按鈕 → 彈窗 | ✅ |
| `kt config show / path` | 設定 → **進階** → 檔案表格 | ✅ |
| `kt config edit <name>` | 設定 → 進階 → 列 **編輯** | ✅ |

## 工作階段（Sessions）

| `kt` 指令 | UI 入口 | 狀態 |
|---|---|---|
| `kt list`（sessions） | Sessions 分頁 — 已儲存 session 列表 | ✅ |
| `kt resume <session>` | Sessions 分頁 → 列 → **繼續** | ✅ |
| `kt search <session> <query> --mode --agent -k` | Session viewer → **Find** 分頁 | ✅ |
| `kt embedding <session> --provider --model --dimensions` | Sessions 分頁 → 列 ⋮ 選單 → **建立嵌入向量**（或 Find 分頁空狀態橫幅） | ✅ |

## 套件（Packages）

| `kt` 指令 | UI 入口 | 狀態 |
|---|---|---|
| `kt list`（目錄） | Catalog 分頁 | ✅ |
| `kt info <agent_path>` | Catalog → 卡片 → **資訊** 抽屜 | ✅ |
| `kt install <git/local/pypi>` | Catalog → **從 URL 安裝** 彈窗 | ✅ |
| `kt uninstall <name>` | Catalog → 卡片 → **解除安裝** | ✅ |
| `kt update [target] [--all]` | Catalog → 卡片 → **更新**，或工具列 **全部更新** | ✅ |
| `kt edit @pkg/...` | Catalog → 卡片 → **編輯檔案** 抽屜（內建 YAML 編輯器） | ✅ |
| `kt extension list` | Extensions 分頁（頂層） | ✅ |
| `kt extension info <name>` | Extensions 分頁 → 列 | ✅ |
| `kt mcp list --agent <path>` | 設定 → MCP → 列 **編輯** 彈窗 → "Used by" 清單 | ✅ |

## 更新 / 自我更新

| `kt` 指令 | UI 入口 | 狀態 |
|---|---|---|
| `kt self-update` | 設定 → **Updates** | ✅ |
| `kt self-update --source/--spec` | 設定 → Updates → 來源選擇 | ✅ |
| `kt self-update --check-only / --dry-run` | 設定 → Updates → **立即檢查** | ⚠️（無 dry-run UI；罕見到保留 CLI-only） |

## 關於 / 診斷

| `kt` 指令 | UI 入口 | 狀態 |
|---|---|---|
| `kt --version --verbose` | 設定 → **關於** → 診斷資訊面板 | ✅ |
| `kt serve logs --follow --lines --level` | 設定 → 關於 → **檢視伺服器日誌** | ✅ |
| `kt serve status` | 設定 → 關於 → daemon 區段 | ✅ |
| `kt serve start/stop/restart` | app **本身就是** daemon；隱式執行 | ✅ |

## Lab / 多節點

| `kt` 指令 | UI 入口 | 狀態 |
|---|---|---|
| `kt host` / `kt serve start --mode lab-host` | 設定 → **Sites**（僅 lab-host 模式可見） | ⚠️（host process 仍需透過 CLI / `kt service` 啟動） |
| `kt client` / `kt lab-client` | 設定 → Sites → **啟動客戶端精靈** 生成精確指令 | ⚠️（你需貼到 worker 主機的終端機） |
| 中斷 worker | 設定 → Sites → 列選單 → **中斷連線** | ✅ |
| 封鎖 worker | 設定 → Sites → 列選單 → **封鎖** | ✅ |
| 輪換配對金鑰 | 設定 → Sites → **輪換配對金鑰** | ✅ |

## 部署 / OS 服務

| `kt` 指令 | UI 入口 | 狀態 |
|---|---|---|
| `kt service install/uninstall/status/edit` | （無） | 🔧 刻意如此 — systemd unit 安裝需在伺服器上以 root 執行。維運使用 CLI 或 Ansible / Docker。 |

## 另請參閱

- [桌面 UI 導覽](desktop-ui-walkthrough.md) — 各分頁的引導。
- [App update](app-update.md) — 完整更新流程內部細節。
- [Serving 指南](serving.md) — daemon 的串接方式。
- [`kt --help`](../../README.md) — 終端機側參考。
