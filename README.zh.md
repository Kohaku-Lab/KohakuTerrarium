<p align="center">
  <img src="images/banner.png" alt="KohakuTerrarium" width="800">
</p>
<p align="center">
  <strong>建造 agent 的機器，讓你不用每次想做新 agent 都從頭打造機器。</strong>
</p>
<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-KohakuTerrarium--1.0-green" alt="License">
  <img src="https://img.shields.io/badge/version-2.0.0-orange" alt="Version">
</p>

<p align="center">
  <a href="README.md">English</a> &nbsp;·&nbsp; <strong>繁體中文</strong> &nbsp;·&nbsp; <a href="README.zh-CN.md">简体中文</a>
</p>
<p align="center">
  <a href="https://terrarium.kohaku-lab.org"><strong>文件網站</strong></a>
</p>

---

## 跑起來看看 (60 秒)

```bash
pip install kohakuterrarium                 # 安裝
kt login codex                              # 認證模型提供者
kt install @kt-biome                        # 抓官方生物套件包
kt run @kt-biome/creatures/swe --mode cli   # 跑一個完整的 coding agent
```

你會拿到一個互動式 shell，裡面是完整的 coding agent：檔案工具、shell 存取、網頁搜尋、子代理、可恢復的工作階段，全都有。`Ctrl+D` 離開；`kt resume --last` 從你停下的地方原封不動接回來。

同一個 agent 當函式庫用，只要四行：

```python
from kohakuterrarium import Agent

agent = await Agent.build("@kt-biome/creatures/swe")
await agent.start()
result = await agent.run("Explain what this codebase does.")  # -> TurnResult
print(result.text, result.usage)
```

想要詳細一點？看[快速開始](docs/zh-TW/guides/getting-started.md)。想自己建？看[第一隻生物](docs/zh-TW/tutorials/first-creature.md)。想嵌進自己的程式？看[程式化使用](docs/zh-TW/guides/programmatic-usage.md)。

## 這適合你嗎？

**你大概想用 KohakuTerrarium，如果**你需要一個新形態的 agent，又不想重建底層；你想要開箱即用、又能客製化的強力 agent；你想用自己的 Python 程式驅動 agent (批次任務、bot、pipeline)；你的需求還在演化中。

**你大概不需要它，如果**現有的 agent 產品 (Claude Code、Codex…) 已經滿足你穩定的需求；你對 agent 的心智模型跟 controller / tools / triggers / sub-agents / channels 這套對不上；你需要每次操作低於 50 ms 的延遲。更誠實的討論放在[邊界](docs/zh-TW/concepts/boundaries.md)。

## KohakuTerrarium 是什麼

KohakuTerrarium 是**建造 agent 的框架**，不是又一個 agent。

過去兩年出現了一堆驚人的 agent 產品：Claude Code、Codex、Gemini CLI、OpenCode、OpenClaw、Hermes Agent…等等。它們確實是不同的產品，但它們都從零重做同一套底層：控制器迴圈、工具派發、觸發器、子代理、工作階段、持久化、多代理接線。每出現一個新形態的 agent，這套管線就得再打造一次。

KohakuTerrarium 把那套底層放在一個地方，這樣下一個新形態的 agent 只要一份設定檔加幾個自訂模組，不用開一個新 repo。

核心抽象是**生物 (creature)**：一個獨立的 agent，擁有自己的控制器、工具、子代理、觸發器、記憶與 I/O。生物由 **Terrarium 引擎**托管：它是一個圖執行期，負責頻道、生命週期、輸出接線、熱插拔，以及圖變動之後的拓樸與工作階段記帳。再往上是 **Studio** 層，負責目錄、身份、活動工作階段、持久化，以及網頁 / 桌面 / API 管理介面。可選的 **Laboratory** 傳輸層可以把主機與引擎拆到不同機器：Studio 與 Terrarium 完全不變，中間插進一段 WebSocket 跳躍而已。

所有東西都是 Python。Agent 是你可以 `await` 的物件，回傳有型別的結果，可以嵌進你的工具、你的 bot、你的批次任務，甚至嵌進別的 agent 裡面。

想立刻玩開箱即用的生物，看 [**kt-biome**](https://github.com/Kohaku-Lab/kt-biome)，這是官方套件包，裡面是建在這個框架上的好用 agent 與外掛。

## 它定位在哪裡

|  | 產品 | 框架 | 工具 / 包裝層 |
|--|------|------|---------------|
| **LLM App** | ChatGPT、Claude.ai | LangChain、LangGraph、Dify | DSPy |
| **Agent** | ***kt-biome***、Claude Code、Codex、OpenCode、OpenClaw、Hermes Agent… | ***KohakuTerrarium***、smolagents | （無） |
| **多代理** | ***kt-biome*** | ***KohakuTerrarium*** | CrewAI、AutoGen |

大多數工具要嘛在 agent 這一層以下，要嘛直接跳到多代理編排、對「agent 是什麼」的想像卻很薄。KohakuTerrarium 從 agent 本身開始。

一隻生物由這些組成：

- **Controller (控制器)**：推理迴圈
- **Input (輸入)**：事件如何進入 agent
- **Output (輸出)**：結果如何離開 agent
- **Tools (工具)**：可以採取哪些動作
- **Triggers (觸發器)**：什麼會喚醒它
- **Sub-agents (子代理)**：內部委派，處理專門任務

一個生態瓶 (terrarium) 透過頻道、生命週期管理與可觀測性，把多隻生物橫向組起來。

## 主要特色

- **Agent 層級的抽象。** 六模組的生物模型是一等公民。新形態的 agent 是「寫一份設定、或許加幾個自訂模組」，不是「重蓋執行期」。
- **真正的 Python API。** `Agent.build`、有型別的 `TurnResult` 輪次搭配真的會取消的 timeout、有型別的串流事件、`@kt.tool` 把任何函式變成 agent 工具、直接注入 LLM 實例、預設嚴格報錯而不是默默 fallback。
- **內建工作階段持久化與恢復。** 引擎負責建立並持有 session 檔 (`session=` / `Terrarium(session_dir=)`)；幾小時後用 `kt resume` 或 `Terrarium.resume` 接回來。`SessionReader` 可以離線重播任何已完成的執行。
- **可搜尋的工作階段歷史。** 每個事件都有索引。`kt search` 和 `search_memory` 工具讓你 (以及 agent 自己) 查到過去的工作。
- **非阻塞的上下文壓縮。** 長時間執行的 agent 在背景壓縮上下文的同時繼續工作。
- **完整的內建工具與子代理。** 檔案、shell、網頁、JSON、notebook、搜尋、編輯、規劃、審查、研究，特權節點上還有 `group_*` 系列的圖編輯工具。
- **MCP 支援。** 可按 agent 或全域連接 stdio / streamable-HTTP MCP 伺服器；四個 meta-tool 讓 prompt 不管接幾台伺服器都保持精簡。
- **套件系統 + 市集。** `kt install @name` 透過 [TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket) 解析；`kohakuterrarium.packages.ensure("@name")` 是腳本端的冪等原語。
- **組合代數。** 用 `>>`、`&`、`|`、`*`、`.iterate` 運算子把 agent 串成 pipeline。
- **多個執行期介面。** CLI、TUI、網頁 dashboard、原生桌面 app，開箱即用。
- **可選的四層認證。** Host token、管理員密碼、多使用者帳號，每層獨立 opt-in；預設全關。見[認證](docs/zh-TW/guides/authentication.md)。

## 快速開始

> **建議 Python 版本**：3.12 以上。CI 驗證 3.12+；3.10 與 3.11 仍可安裝執行 (`requires-python = ">=3.10"`)，但屬於 best-effort 支援。

### 1. 安裝 KohakuTerrarium

```bash
# 從 PyPI
pip install kohakuterrarium
# 選用附加：pip install "kohakuterrarium[full]"

# 或從原始碼 (開發用；專案慣例使用 uv)
git clone https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
uv pip install -e ".[dev]"

# 建置網頁前端 (從原始碼跑 `kt web` / `kt app` 需要)
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

### 2. 安裝開箱即用的生物與外掛

```bash
kt install @kt-biome                 # 官方套件包，透過 TerrariumMarket
kt marketplace search                # 瀏覽所有上架的套件
kt install <git-url>                 # 用 URL 安裝任何第三方套件
kt install ./my-creatures -e         # 可編輯的本地安裝
```

來源設定、版本鎖定、環境變數覆寫，見 [`docs/zh-TW/guides/packages.md`](docs/zh-TW/guides/packages.md)。

### 3. 認證模型提供者

```bash
kt login codex                       # Codex OAuth (ChatGPT 訂閱)
kt model default gpt-5.4
# 或 API-key 提供者：`kt config key set <provider>`
```

支援 Codex OAuth、OpenRouter/OpenAI、原生 Anthropic、Google Gemini、Kimi Code、GLM Coding Plan，以及任何 OpenAI 相容 API。

### 4. 跑點東西

```bash
kt run @kt-biome/creatures/swe --mode cli       # 單一生物
kt terrarium run @kt-biome/terrariums/swe_team  # 多代理團隊
kt serve start                                  # 網頁 dashboard
kt app                                          # 原生桌面
kt doctor                                       # 檢查環境設定
```

## 選擇你的路徑

### 我現在就想跑點東西

- [快速開始](docs/zh-TW/guides/getting-started.md)
- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome)
- [CLI 參考](docs/zh-TW/reference/cli.md)
- [範例](examples/README.md)

### 我要自己建一隻生物

- [第一隻生物教學](docs/zh-TW/tutorials/first-creature.md)
- [撰寫生物](docs/zh-TW/guides/creatures.md)
- [自訂模組](docs/zh-TW/guides/custom-modules.md)
- [外掛](docs/zh-TW/guides/plugins.md)
- [第一個自訂工具教學](docs/zh-TW/tutorials/first-custom-tool.md)

### 我要做多代理組合

- [第一個生態瓶教學](docs/zh-TW/tutorials/first-terrarium.md)
- [生態瓶使用指南](docs/zh-TW/guides/terrariums.md)
- [多代理概念](docs/zh-TW/concepts/multi-agent/README.md)

### 我要嵌進 Python

- [第一次 Python 嵌入教學](docs/zh-TW/tutorials/first-python-embedding.md)
- [程式化使用](docs/zh-TW/guides/programmatic-usage.md)
- [組合代數](docs/zh-TW/guides/composition.md)
- [Python API 參考](docs/zh-TW/reference/python.md)

### 我想搞清楚這東西怎麼運作

- [概念文件](docs/zh-TW/concepts/README.md)
- [詞彙表](docs/zh-TW/concepts/glossary.md)：白話定義
- [Why KohakuTerrarium](docs/zh-TW/concepts/foundations/why-kohakuterrarium.md)
- [什麼是 agent](docs/zh-TW/concepts/foundations/what-is-an-agent.md)

### 我要部署上線

- [Docker 部署](docs/zh-TW/guides/deployment-docker.md)：AIO、host + workers、分散式 compose 範例
- [systemd 部署](docs/zh-TW/guides/deployment-systemd.md)：`kt service install` + 強化過的 unit
- [反向代理部署](docs/zh-TW/guides/deployment-reverse-proxy.md)：nginx / Cloudflare Tunnel + TLS
- [Laboratory](docs/zh-TW/guides/laboratory.md)：多節點 lab-host / lab-client 模式

### 我要貢獻框架本身

- [開發首頁](docs/zh-TW/dev/README.md)
- [內部結構](docs/zh-TW/dev/internals.md)
- [測試](docs/zh-TW/dev/testing.md)
- [`AGENTS.md`](AGENTS.md)：給 coding agent 用的一頁式簡報
- 每個子套件的 README 在 [`src/kohakuterrarium/`](src/kohakuterrarium/README.md)

## 核心心智模型

### 生物 (Creature)

```text
    List, Create, Delete  +------------------+
                    +-----|   Tools System   |
      +---------+   |     +------------------+
      |  Input  |   |          ^        |
      +---------+   V          |        v
        |   +---------+   +------------------+   +--------+
        +-->| Trigger |-->|    Controller    |-->| Output |
User input  | System  |   |    (Main LLM)    |   +--------+
            +---------+   +------------------+
                              |          ^
                              v          |
                          +------------------+
                          |    Sub Agents    |
                          +------------------+
```

生物是一個獨立的 agent，有自己的執行期、工具、子代理、提示詞與狀態。

```bash
kt run path/to/creature
kt run @package/path/to/creature
```

### 執行期層級

```text
使用者 / API / 桌面端
        |
        v
+----------------------+     不做推理
| Studio / App 層      |  目錄、身份、活動工作階段、持久化、
|                      |  attach、編輯器、即時 trace
+----------------------+
        |
        v
+----------------------+     可選：僅在多節點模式下
| Laboratory (Lab)     |  WebSocket 傳輸 + 自訂封包，
|                      |  讓主機橫跨 N 台 worker 機器
+----------------------+     對 Studio + Terrarium 透明
        |
        v
+----------------------+     無 LLM；只管結構
| Terrarium 引擎       |  生物圖、拓樸、頻道、熱插拔、
|                      |  輸出接線、工作階段的
|                      |  合併 / 分割記帳
+----------+-----------+
           |
   +-------+----------------+
   |                        |
特權節點                 工作生物
(面向使用者、擁有        swe / coder / reviewer / ...
 group 工具，由配方
 的 `root:` 指定)
   |
   v
每個生物內部的子代理
(縱向 / 私有委派)
```

- **Studio** 是網頁 dashboard、桌面 app 與 HTTP API 共用的管理框架。它負責目錄視圖、身份與設定、活動工作階段、持久化、attach / 恢復、編輯器與即時 trace。它不做推理。
- **Laboratory (Lab)** 是 Studio 與 Terrarium 之間可選的網路層。單機模式下連 import 都不會發生。在 `--mode lab-host` 下，一台主機透過 WebSocket 協調 N 台 worker 機器上的生物；Studio 與 Terrarium 完全不用改。見 [Laboratory 概念](docs/zh-TW/concepts/laboratory.md)與[使用指南](docs/zh-TW/guides/laboratory.md)。
- **Terrarium** 是托管行程內所有運行中生物的執行期引擎。獨立 agent 是一張單生物圖；團隊則是一張連通圖。引擎不跑 LLM，但擁有*結構*：哪些生物屬於同一個元件、哪些頻道存在、輪次結束的輸出送到哪、哪個 session store 對應哪張圖，以及拓樸變動後的自動合併 / 自動分割記帳。
- **特權節點 (privileged node)** 是被授予 `group_*` 工具的生物 (圖編輯器：生成 / 移除生物、建立 / 刪除頻道、啟動 / 停止成員)。配方的 `root:` 關鍵字會把一個節點升為特權並套上標準的使用者接線；也可以用 inline 的 `privileged: true` 或程式化的 `is_privileged=True` 來授權。
- **生物 (Creature)** 擁有推理：控制器、工具、觸發器、子代理、外掛、記憶、I/O、提示詞與私有狀態。生物不需要知道自己是獨立執行還是圖中的一個節點。
- **子代理**是單一生物內部的縱向 / 私有委派。一個控制器能在內部拆解任務時，優先用子代理；多個平級生物需要橫向合作時，才用 Terrarium。

### 頻道與輸出接線

- **頻道 (channel)**：具名的廣播管線。每個監聽者都會收到每一則訊息。適合條件式 / 選擇性 / 觀察型的流量。
- **輸出接線 (output wiring)**：確定性的 pipeline 邊，把生物輪次結束的輸出自動送到指定目標，不需要 `send_message`。

### 模組

一隻生物有六個概念模組。**其中五個是使用者可擴充的**：你可以在設定或 Python 裡換掉實作。第六個是控制器，也就是驅動它們的推理迴圈。

| 模組 | 做什麼 | 自訂範例 |
|------|--------|----------|
| **Input** | 接收外部事件 | Discord listener、webhook、語音輸入 |
| **Output** | 送出 agent 輸出 | Discord sender、TTS、檔案寫入 |
| **Tool** | 執行動作 | API 呼叫、資料庫存取、RAG 檢索 |
| **Trigger** | 產生自動事件 | 計時器、排程器、頻道 watcher |
| **Sub-agent** | 委派任務執行 | 規劃、程式審查、研究 |

另外還有**外掛**，負責修改模組*之間*的連接而不 fork 它們 (prompt 外掛、生命週期 hook、權限把關)。見[外掛使用指南](docs/zh-TW/guides/plugins.md)。

### 環境與工作階段

- **環境 (Environment)**：生態瓶的共享狀態 (共用頻道)。
- **工作階段 (Session)**：生物的私有狀態 (scratchpad、私有頻道、子代理狀態)。

預設私有，共享需明確 opt-in。

## 程式化使用

Agent 是 async Python 值，回傳有型別的結果。三個層級的介面，由小到大：

先從一個裸 agent 開始：建構、跑一個輪次、讀 `TurnResult`。

```python
import asyncio
from kohakuterrarium import Agent, tool

@tool
def count_words(text: str) -> str:
    """Count the words in a text."""
    return str(len(text.split()))

async def main():
    agent = await Agent.build(
        "@kt-biome/creatures/general",
        llm="default",            # profile 名稱、preset，或一個 provider 實例
        tools=[count_words],      # 普通函式直接變成 agent 工具
    )
    await agent.start()
    result = await agent.run("How many words in the README?", timeout=300)
    print(result.status, result.text, result.usage)   # 失敗有型別，不會被吞掉
    await agent.stop()

asyncio.run(main())
```

再來是引擎：托管多隻生物，各自有工作目錄與持久化的工作階段。

```python
from kohakuterrarium import Terrarium

async with Terrarium() as engine:
    worker = await engine.add_creature(
        "@kt-biome/creatures/swe",
        llm="fast",                          # 名字打錯？這裡就會報錯，不會等到跑一半
        pwd=workdir,                         # 每隻生物獨立 cwd，不動全域 chdir
        session=workdir / "run.kohakutr",    # 引擎負責建立 + 關閉 store
    )
    result = await worker.run("Fix the failing test.", timeout=1800)
```

一個引擎托管 60 隻生物跟托管一隻一樣輕鬆。完整的批次模式約 50 行，見 [`examples/code/batch_grading.py`](examples/code/batch_grading.py)；事後重播任何一次執行用 [`SessionReader`](docs/zh-TW/reference/python.md)。

最後是組合代數，在 agent 和普通 callable 之上組 pipeline：

```python
from kohakuterrarium.compose import agent, factory

async with await agent("@kt-biome/creatures/swe") as swe:
    result = await (swe >> extract_code >> reviewer)(task)

# 運算子：>> (sequence)、& (parallel)、| (fallback)、* (retry)
safe = (expert * 2) | generalist
results = await (analyst & writer & designer)(task)

async for draft in (writer >> reviewer).iterate(task):
    if "APPROVED" in draft:
        break
```

更多：[程式化使用](docs/zh-TW/guides/programmatic-usage.md)、[組合](docs/zh-TW/guides/composition.md)、[Python API](docs/zh-TW/reference/python.md)、[`examples/code/`](examples/)。

## 執行期介面

### CLI 與 TUI

- **cli**：豐富的行內終端體驗
- **tui**：全螢幕 Textual 應用
- **plain**：簡單 stdout/stdin，給 pipe 與 CI 用

見 [CLI 參考](docs/zh-TW/reference/cli.md)。

### 網頁 dashboard

Vue 的 dashboard + 由 Studio 管理層驅動的 FastAPI 伺服器。

```bash
kt web                       # 一次性、前台執行
kt serve start               # 長期常駐
# 前端開發：npm run dev --prefix src/kohakuterrarium-frontend
```

見 [HTTP API](docs/zh-TW/reference/http.md)、[Serving 指南](docs/zh-TW/guides/serving.md)、[前端架構](docs/zh-TW/dev/frontend.md)。

### 桌面 app

`kt app` 把網頁 UI 開在原生桌面視窗裡 (需要 `pywebview`)。

### 部署 (Docker / systemd / 多節點)

GHCR 上提供三個官方 Docker 映像，依形狀選擇：

```bash
# AIO：lab-host + 內嵌 worker 在同一容器
docker run -d -p 8001:8001 -v kt:/home/kt/.kohakuterrarium \
  ghcr.io/kohaku-lab/kohakuterrarium:2.0.0

# Host + workers (不同機器)：兩個映像、同一共享 token
docker run -d -p 8001:8001 -p 8100:8100 \
  -e KT_HOST_TOKEN=$TOKEN ghcr.io/kohaku-lab/kohakuterrarium-host:2.0.0
docker run -d -e KT_HOST_URL=ws://host:8100 -e KT_HOST_TOKEN=$TOKEN \
  -e KT_CLIENT_NAME=worker-a ghcr.io/kohaku-lab/kohakuterrarium-client:2.0.0
```

或用 systemd 一行指令安裝強化過的原生服務：

```bash
sudo kt service install --all                              # AIO unit
sudo kt service install --host                             # host unit
sudo kt service install --client --name worker-a --host-url ws://… --host-token …
sudo systemctl enable --now kohakuterrarium-host kohakuterrarium-client@worker-a
```

`examples/deployment/` 下備有可直接套用的 compose 檔 (AIO、host + workers、分散式) 與 nginx TLS 終止範本。`/healthz` + `/readyz` 端點供 Docker `HEALTHCHECK` 與反向代理 active health 使用。

見 [Docker 部署](docs/zh-TW/guides/deployment-docker.md)、[systemd 部署](docs/zh-TW/guides/deployment-systemd.md)、[反向代理部署](docs/zh-TW/guides/deployment-reverse-proxy.md)。

## 工作階段、記憶、恢復

工作階段預設存在 `~/.kohakuterrarium/sessions/` (除非停用)。

```bash
kt resume            # 互動選擇
kt resume --last     # 接最近的一個
kt resume swe_team   # 用名稱前綴恢復
```

同一個 store 也驅動可搜尋的歷史：

```bash
kt embedding <session>                       # 建 FTS + vector 索引
kt search <session> "auth bug fix"           # 混合 / 語意 / FTS 搜尋
```

agent 可以透過 `search_memory` 工具搜尋自己的歷史，Python 則可以重播任何一次執行：

```python
from kohakuterrarium import SessionReader

with SessionReader("runs/student-42.kohakutr") as r:
    for turn in r.turns():
        print(turn.user_text, "->", turn.assistant_text[:80], turn.tool_calls)
```

`.kohakutr` 檔案儲存對話、工具呼叫、事件、scratchpad、子代理狀態、頻道訊息、job、可恢復的觸發器與設定 metadata。

見[工作階段](docs/zh-TW/guides/sessions.md)、[記憶](docs/zh-TW/guides/memory.md)。

## 套件、預設、範例

生物是設計來被打包、安裝、重用、分享的。

```bash
kt install @kt-biome                              # 市集
kt install https://github.com/someone/pack.git    # git URL
kt install ./my-creatures -e                      # 可編輯的本地安裝
kt list
kt update --all
```

用套件參照執行已安裝的設定，在 Python 裡也能用：

```bash
kt run @cool-creatures/creatures/my-agent
kt terrarium run @cool-creatures/terrariums/my-team
```

```python
from kohakuterrarium import packages

packages.ensure("@kt-biome")   # 冪等；放在任何腳本開頭都安全
```

可用資源：

- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome)：官方生物、生態瓶、外掛套件包
- `examples/agent-apps/`：設定驅動的生物範例
- `examples/code/`：Python 使用範例
- `examples/terrariums/`：多代理範例
- `examples/plugins/`：外掛範例

見 [examples/README.md](examples/README.md)。

## Codebase 地圖

```text
src/kohakuterrarium/
  core/              # Agent 執行期：控制器、turn API、executor、事件、environment
  bootstrap/         # Agent 初始化工廠 (LLM、工具、I/O、觸發器、外掛)
  cli/               # `kt` 指令分派
  studio/            # 管理門面：目錄、身份、工作階段、持久化、attach、編輯器
  terrarium/         # 執行期引擎：生物圖、拓樸、頻道、輸出接線、熱插拔
  builtins/          # 內建工具、子代理、I/O 模組、TUI、使用者指令、CLI UI
  builtin_skills/    # Markdown skill 檔 (按需載入的說明)
  session/           # 工作階段持久化、SessionReader、記憶搜尋、embeddings
  serving/           # 啟動 / 傳輸輔助
  api/               # 基於 Studio 與 Terrarium 的 FastAPI HTTP / WebSocket adapter
  compose/           # 組合代數原語
  mcp/               # MCP client 管理器
  modules/           # 工具、輸入、輸出、觸發器、子代理、使用者指令的 base protocol
  llm/               # LLM 提供者、profile、API 金鑰管理
  parsing/           # 工具呼叫解析與串流處理
  prompt/            # 提示詞聚合、外掛、skill 載入
  errors.py          # 有型別的例外階層 (KTError 與其家族)
  validate.py        # `kt doctor` 背後的事前檢查
  testing/           # 測試基礎建設 (ScriptedLLM、TestAgentBuilder、recorder)

src/kohakuterrarium-frontend/   # Vue 網頁前端
examples/                       # 範例生物、生態瓶、程式碼、外掛
docs/                           # 教學、使用指南、概念、參考、開發
```

每個子套件都有自己的 README，說明檔案、相依方向與不變式。

## 文件地圖

完整文件在 [`docs/`](docs/zh-TW/README.md)。

### 教學
[第一隻生物](docs/zh-TW/tutorials/first-creature.md) · [第一個生態瓶](docs/zh-TW/tutorials/first-terrarium.md) · [第一次 Python 嵌入](docs/zh-TW/tutorials/first-python-embedding.md) · [第一個自訂工具](docs/zh-TW/tutorials/first-custom-tool.md) · [第一個外掛](docs/zh-TW/tutorials/first-plugin.md)

### 使用指南
[快速開始](docs/zh-TW/guides/getting-started.md) · [撰寫生物](docs/zh-TW/guides/creatures.md) · [生態瓶](docs/zh-TW/guides/terrariums.md) · [工作階段](docs/zh-TW/guides/sessions.md) · [記憶](docs/zh-TW/guides/memory.md) · [設定檔](docs/zh-TW/guides/configuration.md) · [程式化使用](docs/zh-TW/guides/programmatic-usage.md) · [組合](docs/zh-TW/guides/composition.md) · [自訂模組](docs/zh-TW/guides/custom-modules.md) · [外掛](docs/zh-TW/guides/plugins.md) · [MCP](docs/zh-TW/guides/mcp.md) · [套件](docs/zh-TW/guides/packages.md) · [Serving](docs/zh-TW/guides/serving.md) · [Laboratory](docs/zh-TW/guides/laboratory.md) · [Docker 部署](docs/zh-TW/guides/deployment-docker.md) · [systemd 部署](docs/zh-TW/guides/deployment-systemd.md) · [反向代理部署](docs/zh-TW/guides/deployment-reverse-proxy.md) · [範例](docs/zh-TW/guides/examples.md)

### 概念
[詞彙表](docs/zh-TW/concepts/glossary.md) · [Why KohakuTerrarium](docs/zh-TW/concepts/foundations/why-kohakuterrarium.md) · [什麼是 agent](docs/zh-TW/concepts/foundations/what-is-an-agent.md) · [組合一個 agent](docs/zh-TW/concepts/foundations/composing-an-agent.md) · [模組](docs/zh-TW/concepts/modules/README.md) · [Agent 作為 Python 物件](docs/zh-TW/concepts/python-native/agent-as-python-object.md) · [組合代數](docs/zh-TW/concepts/python-native/composition-algebra.md) · [多代理](docs/zh-TW/concepts/multi-agent/README.md) · [模式](docs/zh-TW/concepts/patterns.md) · [邊界](docs/zh-TW/concepts/boundaries.md)

### 參考
[CLI](docs/zh-TW/reference/cli.md) · [HTTP](docs/zh-TW/reference/http.md) · [Python API](docs/zh-TW/reference/python.md) · [設定檔](docs/zh-TW/reference/configuration.md) · [內建模組](docs/zh-TW/reference/builtins.md) · [外掛 hook](docs/zh-TW/reference/plugin-hooks.md)

## Roadmap

近期方向：更可靠的生態瓶流程、更豐富的 UI 輸出 / 互動模組 (CLI / TUI / 網頁)、更多內建生物、外掛與整合，以及更完善的 daemon 工作流 (給長時間執行與遠端使用)。見 [ROADMAP.md](ROADMAP.md)。

## 貢獻

- [貢獻指南](CONTRIBUTING.md)
- [開發首頁](docs/zh-TW/dev/README.md)
- [測試](docs/zh-TW/dev/testing.md)
- [內部結構](docs/zh-TW/dev/internals.md)
- [前端架構](docs/zh-TW/dev/frontend.md)

## 授權

[KohakuTerrarium License 1.0](LICENSE)：以 Apache-2.0 為基礎，加上命名與標示要求。

- 衍生作品名稱須包含 `Kohaku` 或 `Terrarium`。
- 衍生作品須在可見位置附上指向本專案的標示與連結。

Copyright 2024-2026 Shih-Ying Yeh (KohakuBlueLeaf) 與貢獻者。

## 社群
- QQ: 1097666427
- Discord: https://discord.gg/xWYrkyvJ2s
- Forum: https://linux.do/

## FAQ

### 一般問題

**KohakuTerrarium 是什麼？**
一個 Python 原生的 agent 建造框架。公開的層級：**生物 (Creature)** 是 agent 單位，**Terrarium** 是擁有生物圖的執行期引擎 (拓樸、頻道、工作階段，自己不跑 LLM)，**Studio** 是引擎之上的管理層 (目錄、工作階段、持久化、API)。

**它跟其他 agent 框架差在哪？**
職責切得很乾淨：生物擁有推理與工具，引擎擁有圖拓樸 / 頻道 / 生命週期 / 工作階段記帳，Studio 擁有管理介面。橫向團隊用 Terrarium 配方與頻道；Python 端的請求 pipeline 用組合代數。

### 安裝與設定

**需要什麼 Python 版本？**
Python 3.10 以上；**建議 3.12+** (CI 驗證的就是這個範圍)。用 `pip install kohakuterrarium` 安裝。

**支援哪些 LLM 提供者？**
Codex OAuth、OpenAI/OpenRouter 式提供者、原生 Anthropic、Google Gemini、Kimi Code、GLM Coding Plan、本地 OpenAI 相容伺服器 (Ollama、vLLM)，以及其他 OpenAI 相容的雲端供應商。用 `kt login`、`kt config llm add` 或 API 金鑰設定。`kt doctor` 可以驗證設定。

**可以用本地模型嗎？**
可以。把 LLM endpoint 指向你的本地伺服器 (Ollama、vLLM 等)，並在生物設定或 LLM profile 裡設定模型名稱。

### 核心概念

**什麼是「生物 (Creature)」？**
獨立的 agent 單位：控制器、工具、觸發器、子代理、外掛、記憶、I/O、提示詞、私有狀態。可以單獨跑，也可以當 Terrarium 圖裡的一個節點。

**什麼是「Terrarium」？**
托管生物圖的執行期引擎。它不跑 LLM、沒有推理迴圈，但擁有所有結構性決策：連通元件、頻道註冊、熱插拔、輸出接線、工作階段的合併 / 分割記帳。

**什麼是「外掛 (Plugin)」？**
基於 hook 的擴充，包住框架行為：工具呼叫、LLM 呼叫、子代理執行的前後 hook，加上生命週期 callback。沙箱、預算、權限把關全都是普通外掛。

### 開發

**怎麼建立自訂生物？**
寫一份 YAML 設定，定義工具、提示詞與行為；也可以在 Python 裡用 `Agent.build` / `engine.add_creature` 建。見[第一隻生物](docs/zh-TW/tutorials/first-creature.md)。

**可以把 agent 嵌進我的 Python 應用嗎？**
可以，而且這是一等公民介面。`await agent.run(...)` 回傳有型別的 `TurnResult`；`run_stream` 產出有型別的事件；引擎處理工作目錄、工作階段與大量並行的生物。見 [`examples/code/`](examples/code/) 與[程式化使用指南](docs/zh-TW/guides/programmatic-usage.md)。

**多代理組合怎麼運作？**
橫向團隊用 Terrarium 配方 / 頻道 / 輸出接線。不需要長駐圖的時候，Python 端的輕量請求 pipeline 用 `compose` (`>>`、`&`、`|`、retry)。

### 疑難排解

**為什麼我的生物沒有回應？**
先跑 `kt doctor`，它會一次檢查提供者認證、profile 解析與設定有效性。然後再確認網路連線與 API 金鑰。

**怎麼除錯 agent 行為？**
用 `kt run --verbose` 看詳細 log。用 `kt resume` 恢復或檢視先前的工作、用 `kt search` 搜尋、用 `SessionReader` 重播，或在網頁 / 桌面 UI 用 Studio 的工作階段檢視器。

**哪裡可以求助？**
- QQ 群：1097666427
- Discord: https://discord.gg/xWYrkyvJ2s
- Forum: https://linux.do/
