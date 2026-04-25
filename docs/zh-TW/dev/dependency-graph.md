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

`utils/` 是葉節點。所有東西都可以匯入它；它本身不會從框架匯入任何內容。`modules/` 只放協定。`core/` 是執行期本體——它會匯入 `modules/` 和 `utils/`，但**絕不**匯入 `builtins/`、`terrarium/` 或 `bootstrap/`。`bootstrap/` 與 `builtins/` 會匯入 `core/` + `modules/`。`terrarium/` 與 `serving/` 會匯入 `core/` + `bootstrap/`。`cli/` 與 `api/` 位於 `serving/` + `terrarium/` 之上。

## 分層

從葉節點（底部）到傳輸層（頂部）：

```
  cli/, api/                    <- 傳輸層
  serving/, terrarium/          <- 協調層
  bootstrap/, builtins/         <- 組裝 + 實作
  core/                         <- 執行期引擎
  modules/                      <- 協定（以及一些基底類別）
  parsing/, prompt/, llm/, …    <- 支援套件
  testing/                      <- 依賴整個堆疊，只供測試使用
  utils/                        <- 葉節點
```

各層細節：

- **`utils/`** —— 記錄、非同步輔助工具、檔案防護。不得從框架匯入任何內容。在這裡加入框架匯入幾乎一定是錯的。
- **`modules/`** —— 協定與基底類別定義。像是 `BaseTool`、`BaseOutputModule`、`BaseTrigger` 等。不含實作，因此上層任何模組都能依賴它們。
- **`core/`** —— `Agent`、`Controller`、`Executor`、`Conversation`、`Environment`、`Session`、頻道、事件、registry。也就是執行期本體。`core/` 絕不能匯入 `terrarium/`、`builtins/`、`bootstrap/`、`serving/`、`cli/` 或 `api/`。這麼做會重新引入循環。
- **`bootstrap/`** —— 從設定建立 `core/` 元件的工廠函式（LLM、工具、IO、子 Agent、觸發器）。會匯入 `core/` 與 `builtins/`。
- **`builtins/`** —— 具體工具、子 Agent、輸入、輸出、TUI、使用者命令。內部 catalog（`tool_catalog`、`subagent_catalog`）是具有延遲載入器的葉模組。
- **`terrarium/`** —— 多 Agent 執行期。匯入 `core/`、`bootstrap/`、`builtins/`。但它們都不會反向匯入 `terrarium/`。
- **`serving/`** —— `KohakuManager`、`AgentSession`。依賴 `core/` 與 `terrarium/`。與傳輸方式無關。
- **`cli/`、`api/`** —— 最上層。一個是 argparse 進入點，另一個是 FastAPI 應用。兩者都消費 `serving/`。

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
- **TYPE_CHECKING** —— 受 `if TYPE_CHECKING:` 保護。不會進入執行期圖。
- **in-function** —— 函式內的匯入。預設/循環視圖會包含這些邊，以便發現隱藏循環；`--module-only` 可恢復舊的僅頂層匯入圖。

import hygiene lint 會依 stdlib、必要依賴、選用依賴、平台限定模組，以及 `scripts/dep_graph_allowlist.json` 分類函式內匯入。每個 allowlist 條目都需要寫明 reason。

### 指令

```bash
# Summary stats + cross-group edge counts (default)
python scripts/dep_graph.py

# Runtime SCC cycle detection（預設包含函式內匯入）
python scripts/dep_graph.py --cycles

# In-function import policy report
python scripts/dep_graph.py --lint-imports

# JSON dump（graph + lint result）
python scripts/dep_graph.py --json

# parse errors / cycles / lint violations 時以非零碼退出
python scripts/dep_graph.py --fail

# Graphviz DOT output (pipe into `dot -Tsvg`)
python scripts/dep_graph.py --dot > deps.dot

# Render a matplotlib group + module plot into plans/
python scripts/dep_graph.py --plot

# Stats + cycles + import lint
python scripts/dep_graph.py --all
```

關鍵輸出：

- **Top fan-out** —— 匯入最多其他模組的模組。通常會是組裝程式碼（`bootstrap/`、`core/agent.py`）。
- **Top fan-in** —— 被匯入次數最多的模組。通常應以 `utils/`、`modules/base`、`core/events.py` 為主。
- **Cross-group edges** —— 類似長條圖的讀值，表示有多少邊跨越套件邊界。如果出現新的 `core/` → `terrarium/` 邊，請調查。
- **SCCs** —— 應該永遠是空的。如果 Tarjan 演算法找到了非平凡 SCC，代表執行期圖存在循環。循環報告會包含 sample path 與構成循環的 import 語句。
- **Import hygiene** —— `--lint-imports` 會報告不允許的函式內匯入。optional/platform 匯入會自動允許；刻意保留的例外放在 `scripts/dep_graph_allowlist.json`。

`--plot` 旗標會輸出 `plans/dep-graph.png`（群組層級、環狀配置）。當重構重新整理相依邊時，這張圖很適合用在 PR 審查。

### 什麼時候該執行

- 在新增子套件的 PR 之前。
- 當你懷疑有循環匯入時（症狀：啟動時出現提到 partially initialized module 的 `ImportError`）。
- 大型重構之後，作為健全性檢查。

執行 `python scripts/dep_graph.py --fail`，並確認輸出包含：

```
None found. The runtime import graph is acyclic.
```

如果它以非零碼退出，請先修掉 parse error、循環或 lint violation 再合併。CI 會在獨立的 `test-dep-graph` job 中執行同一套 `tests/unit/test_dep_graph_lint.py` guard。

## 新增套件

先選對層級。問自己：

- **它有執行期行為，還是只有基底類別／協定？** 協定放在 `modules/`。執行期則放在 `core/` 或專用子套件。
- **它需要 `core.Agent` 嗎？** 如果需要，它就位於 `core/` 之上，而不是裡面。
- **它是內建項目（隨 KT 一起出貨）還是擴充？** 內建放在 `builtins/`；擴充放在獨立套件中，並透過 package manifest 接入。

接著遵守該層的匯入規則：

- `utils/` 不匯入任何框架側內容。
- `modules/` 可匯入 `utils/` 與核心型別，除此之外不行。
- `core/` 可匯入 `modules/`、`utils/`、`llm/`、`parsing/`、`prompt/`。絕不能匯入 `terrarium/`、`serving/`、`builtins/`、`bootstrap/`。
- `bootstrap/` 與 `builtins/` 會匯入 `core/` + `modules/`。
- 其他一切都位於這之上。

如果某條新邊看起來很彆扭，那多半真的有問題。請引入一個葉輔助模組（例如 `tool_catalog`）來拆掉循環，而不是用函式內匯入硬撐。函式內匯入並不鼓勵（見 CLAUDE.md 的 Import Rules），是最後手段，不是第一選項。

## 另見

- [CLAUDE.md 的 Import Rules](../../CLAUDE.md) —— 這套規範所強制的慣例。
- [`src/kohakuterrarium/README.md`](../../src/kohakuterrarium/README.md) —— 正典 ASCII 流程圖。
- [internals.md](internals.md) —— 逐流程說明各子套件用途的地圖。
