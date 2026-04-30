---
title: 相依圖
summary: 模組匯入方向的不變條件，以及用來強制驗證它們的測試。
tags:
  - dev
  - internals
  - architecture
---

# 相依規則

這個套件有嚴格的單向匯入規範。此規範由慣例維持，並由
`scripts/dep_graph.py` 驗證。執行期相依循環目前是零；請繼續維持。

## 一段話說完規則

`utils/` 是葉節點。所有東西都可以匯入它；它本身不會從框架匯入任何內容。`modules/` 只放協定。`core/` 是 Creature runtime——它會匯入 `modules/` 和 `utils/`，但**絕不**匯入 `builtins/`、`terrarium/`、`studio/`、`bootstrap/`、`api/` 或 `cli/`。`bootstrap/` 與 `builtins/` 在 `core/` + `modules/` 之上組裝具體元件。`terrarium/` 托管 graph 中的 Creature 並匯入 `core/` + `bootstrap/`。`studio/` 位於 `terrarium/` 之上，負責管理政策。`cli/` 與 `api/` 是 `studio/` / `terrarium/` 加啟動 glue 的頂層 adapter。

## 分層

從葉節點（底部）到使用者/API 層（頂部）：

```text
  cli/, api/                    <- 使用者/API adapter
  studio/                       <- 管理 facade 與政策
  serving/                      <- 啟動 helper + 舊版相容 wrapper
  terrarium/                    <- Creature graph runtime engine
  bootstrap/, builtins/         <- 組裝 + 實作
  core/                         <- Creature runtime
  modules/                      <- 協定（以及一些基底類別）
  parsing/, prompt/, llm/, …    <- 支援套件
  testing/                      <- 依賴整個堆疊，只供測試使用
  utils/                        <- 葉節點
```

各層細節：

- **`utils/`** —— 記錄、非同步輔助工具、檔案防護。不得從框架匯入任何內容。在這裡加入框架匯入幾乎一定是錯的。
- **`modules/`** —— 協定與基底類別定義。像是 `BaseTool`、`BaseOutputModule`、`BaseTrigger` 等。不含實作，因此上層任何模組都能依賴它們。
- **`core/`** —— `Agent`、`Controller`、`Executor`、`Conversation`、`Environment`、`Session`、頻道、事件、registry。也就是 Creature runtime。`core/` 絕不能匯入 `terrarium/`、`studio/`、`builtins/`、`bootstrap/`、`serving/`、`cli/` 或 `api/`。這麼做會重新引入循環。
- **`bootstrap/`** —— 從設定建立 `core/` 元件的工廠函式（LLM、工具、IO、子 Agent、觸發器）。會匯入 `core/` 與 `builtins/`。
- **`builtins/`** —— 具體工具、子 Agent、輸入、輸出、TUI、使用者命令。內部 catalog（`tool_catalog`、`subagent_catalog`）是具有延遲載入器的葉模組。
- **`terrarium/`** —— Creature graph runtime。匯入 `core/`、`bootstrap/`、`builtins/`。但它們都不會反向匯入 `terrarium/`。
- **`studio/`** —— catalog、identity、active sessions、saved-session persistence、attach policy 與 editor 的管理 facade。依賴 `terrarium/` 以及更低層。
- **`serving/`** —— Web/desktop launch helper 加舊版相容 wrapper。新的管理程式碼應放在 `studio/`。
- **`cli/`、`api/`** —— 最上層。一個是 argparse 進入點，另一個是 FastAPI 應用。它們把管理工作交給 `studio/`，把執行期機制交給 `terrarium/`。

請參閱 [`src/kohakuterrarium/README.md`](../../src/kohakuterrarium/README.md)，其中的 ASCII 相依流程圖是唯一可信來源。

## 為什麼要有這些規則

這些規則服務三個目標：

1. **沒有循環。** 循環會導致初始化順序脆弱、部分匯入錯誤，以及在啟動時容易出問題的匯入期副作用。
2. **可測試性。** 如果 `core/` 永遠不匯入 `terrarium/`，你就能在不啟動多 Agent 執行期的情況下單元測試 controller。如果 `modules/` 只放協定，你就能很容易替換實作。
3. **清楚的變更影響面。** 修改 `utils/` 時，所有東西都會重建。修改 `cli/` 時，其他部分都不會。分層讓你能預期變更的爆炸半徑。

歷史註記：過去曾有一個循環 `builtins.tools.registry → terrarium.runtime → core.agent → builtins.tools.registry`。後來透過引入 catalog/helper 模組，並把 Terrarium root-tool 實作移到 `terrarium/` 底下拆解。`core/__init__.py` 仍使用模組層級 `__getattr__` 做延遲 public export；新的函式內匯入應透過 dep-graph allowlist 說明理由，而不是當成循環 workaround。

## 工具：`scripts/dep_graph.py`

靜態 AST 分析器。會以 UTF-8 讀取並走訪 `src/kohakuterrarium/` 下每個 `.py`，解析 `import` / `from ... import`，並把每條邊分類為：

- **runtime** —— 在模組載入時於頂層執行的匯入。
- **TYPE_CHECKING** —— 受 `if TYPE_CHECKING:` 保護，不會進入執行期圖。
- **in-function** —— 函式內匯入。預設/循環視圖會包含這些邊，以便找出隱藏循環；`--module-only` 可恢復舊版的僅頂層匯入圖。

import hygiene lint 會根據 stdlib、必要依賴、可選依賴、平台限定模組與 `scripts/dep_graph_allowlist.json` 來分類函式內匯入。每個 allowlist 條目都要寫明 reason。

### 指令

```bash
python scripts/dep_graph.py
python scripts/dep_graph.py --cycles
python scripts/dep_graph.py --lint-imports
python scripts/dep_graph.py --json
python scripts/dep_graph.py --fail
python scripts/dep_graph.py --dot > deps.dot
python scripts/dep_graph.py --plot
```

### CI / 測試入口

- `tests/unit/test_dep_graph_lint.py` 跑腳本級驗證。
- `python scripts/dep_graph.py --fail` 在本地可作為快速 smoke test。
- 若新增函式內匯入，先判斷是否能改成頂層匯入；如果不能，更新 `scripts/dep_graph_allowlist.json` 並解釋原因。
