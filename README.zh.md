<p align="center">
  <img src="images/banner.png" alt="KohakuTerrarium" width="800">
</p>
<p align="center">
  <strong>建造 agent 的機器 — 這樣你就不用每次想做新 agent 都從頭打造機器。</strong>
</p>
<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-KohakuTerrarium--1.0-green" alt="License">
  <img src="https://img.shields.io/badge/version-1.0.1-orange" alt="Version">
</p>

<p align="center">
  <a href="README.md">English</a> &nbsp;·&nbsp; <strong>繁體中文</strong>
</p>

---

## 快速看看 (60 秒)

```bash
pip install kohakuterrarium                                         # 安裝
kt login codex                                                      # 認證
kt install https://github.com/Kohaku-Lab/kt-biome.git               # 抓官方套件
kt run @kt-biome/creatures/swe --mode cli                           # 跑一個 agent
```

你會拿到一個互動式 shell，裡面是完整的 coding agent — 有檔案工具、shell 存取、網頁搜尋、子代理、可恢復的工作階段。`Ctrl+D` 離開；`kt resume --last` 再接回來。

想要詳細一點？看[快速開始](docs/zh-TW/guides/getting-started.md)。想自己建？看[第一隻生物](docs/zh-TW/tutorials/first-creature.md)。

## 這適合你嗎？

**你大概想用 KohakuTerrarium 如果** 你需要一個新的 agent 形態又不想重建底層；你想要 OOTB 的生物又能客製化；你想把 agent 行為嵌進現有的 Python 程式；你的需求還在演化。

**你大概不想要它如果** 現有的 agent 產品 (Claude Code、Codex…) 已經滿足你穩定的需求；你對 agent 的心智模型跟 controller / tools / triggers / sub-agents / channels 這套對不上；你需要每操作 <50ms 的延遲。更誠實的討論放在[邊界](docs/zh-TW/concepts/boundaries.md)。

## KohakuTerrarium 是什麼

KohakuTerrarium 是**建造 agent 的框架** — 不是又一個 agent。

過去兩年出現了一堆驚人的 agent 產品：Claude Code、Codex、OpenClaw、Gemini CLI、Hermes Agent、OpenCode…等等。它們確實不一樣，但它們都從零重做同一套底層：控制器迴圈、工具派遣、觸發器系統、子代理機制、工作階段、持久化、多代理接線。每一個新形態的 agent，底層的管線都得再打造一次。

KohakuTerrarium 的工作就是把那套底層放在一個地方，這樣下一個新形態的 agent 只要一份設定檔 + 幾個自訂模組，不用開一個新 repo。

核心抽象是 **生物 (creature)**：一個獨立的 agent，擁有自己的控制器、工具、子代理、觸發器、記憶、I/O。生物可以橫向組合成一個 **生態瓶 (terrarium)** — 一個純接線層。所有東西都是 Python，所以 agent 可以被嵌在其他 agent 的工具、觸發器、外掛、輸出裡。

想立刻玩 OOTB 生物，看 [**kt-biome**](https://github.com/Kohaku-Lab/kt-biome) — 官方套件，裡面有好用的 agent 與外掛，都是建在這個框架上的。

## 它定位在哪裡

|  | 產品 | 框架 | 工具 / 包裝層 |
|--|------|------|---------------|
| **LLM App** | ChatGPT、Claude.ai | LangChain、LangGraph、Dify | DSPy |
| **Agent** | ***kt-biome***、Claude Code、Codex、OpenCode、OpenClaw、Hermes Agent… | ***KohakuTerrarium***、smolagents | — |
| **多代理** | ***kt-biome*** | ***KohakuTerrarium*** | CrewAI、AutoGen |

大多數工具要嘛在 agent 這一層以下，要嘛直接跳到多代理編排而對 agent 本身的概念很薄。KohakuTerrarium 從 agent 本身開始。

一隻生物由這些組成：

- **Controller (控制器)** — 推理迴圈
- **Input (輸入)** — 事件如何進入 agent
- **Output (輸出)** — 結果如何離開 agent
- **Tools (工具)** — 可執行的動作
- **Triggers (觸發器)** — 喚醒條件
- **Sub-agents (子代理)** — 內部委派給專門任務

一個生態瓶透過頻道、生命週期管理、可觀察性，把多隻生物橫向組起來。

## 主要特色

- **Agent 層級的抽象。** 六模組的生物模型是一等公民。每一個新形態的 agent 都是「寫一份設定 + 或許幾個自訂模組」，不是「重蓋執行期」。
- **內建工作階段持久化與恢復。** Session 儲存的是操作狀態，不只是聊天紀錄。幾小時後用 `kt resume` 把工作接回來。
- **可搜尋的工作階段歷史。** 每個事件都被索引。`kt search` 和 `search_memory` 工具讓你 (以及 agent) 可以查到過去的工作。
- **非阻塞的上下文壓縮。** 長時間執行的 agent 在背景壓縮上下文時繼續工作。
- **完整的內建工具與子代理。** 檔案、shell、網頁、JSON、搜尋、編輯、規劃、審查、研究、生態瓶管理。
- **MCP 支援。** 可以按生物或全域連接 stdio / HTTP MCP 伺服器；工具會自動出現在 prompt 裡。
- **套件系統。** 從 Git 或本地路徑安裝生物 / 生態瓶 / 外掛 / LLM 預設；以繼承方式組合已安裝的套件。
- **Python 原生。** Agent 就是 async Python 物件。可以把它們塞進其他 agent 的工具、觸發器、外掛、輸出。
- **組合代數。** 用 `>>`、`&`、`|`、`*`、`.iterate` 運算子把 agent 串成 pipeline。
- **多個執行期介面。** CLI、TUI、網頁 dashboard、桌面 app 都是開箱可用。
- **實用的 OOTB 生物，透過 [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome)。** 先跑強大的預設 agent；之後再客製化或繼承。

## 快速開始

### 1. 安裝 KohakuTerrarium

```bash
# 從 PyPI
pip install kohakuterrarium
# 選用附加: pip install "kohakuterrarium[full]"

# 或從原始碼 (開發用 — 專案慣例使用 uv)
git clone https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
uv pip install -e ".[dev]"

# 從原始碼跑 `kt web` / `kt app` 需要先建置前端
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

### 2. 安裝 OOTB 生物與外掛

```bash
# 官方套件
kt install https://github.com/Kohaku-Lab/kt-biome.git

# 任何第三方套件
kt install <git-url>
kt install ./my-creatures -e        # 可編輯安裝
```

### 3. 認證模型提供者

```bash
# Codex OAuth (ChatGPT 訂閱)
kt login codex
kt model default gpt-5.4

# 或任何 OpenAI 相容提供者：`kt config provider add`
```

支援 OpenRouter、OpenAI、Anthropic、Google Gemini，以及任何 OpenAI 相容 API。

### 4. 跑看看

```bash
# 單一生物
kt run @kt-biome/creatures/swe --mode cli
kt run @kt-biome/creatures/researcher

# 多代理生態瓶
kt terrarium run @kt-biome/terrariums/swe_team

# 網頁 dashboard
kt serve start

# 原生桌面
kt app
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
- [Python API](docs/zh-TW/reference/python.md)

### 我想搞清楚這東西怎麼運作

- [概念文件](docs/zh-TW/concepts/README.md)
- [詞彙表](docs/zh-TW/concepts/glossary.md) — 白話定義
- [Why KohakuTerrarium](docs/zh-TW/concepts/foundations/why-kohakuterrarium.md)
- [什麼是 agent](docs/zh-TW/concepts/foundations/what-is-an-agent.md)

### 我要貢獻框架本身

- [開發首頁](docs/zh-TW/dev/README.md)
- [內部結構](docs/zh-TW/dev/internals.md)
- [測試](docs/zh-TW/dev/testing.md)
- 每個子套件的 README 在 [`src/kohakuterrarium/`](src/kohakuterrarium/README.md)

## 核心心智模型

### 生物 (Creature)

```text
    列出、建立、刪除   +------------------+
                    +-----|   工具系統        |
      +---------+   |     +------------------+
      |  輸入    |   |          ^        |
      +---------+   V          |        v
        |   +---------+   +------------------+   +--------+
        +-->| 觸發器    |-->|    控制器         |-->| 輸出    |
使用者輸入   | 系統     |   |    (主 LLM)      |   +--------+
            +---------+   +------------------+
                              |          ^
                              v          |
                          +------------------+
                          |    子代理         |
                          +------------------+
```

生物是獨立的 agent，有自己的執行期、工具、子代理、提示詞、狀態。

```bash
kt run path/to/creature
kt run @package/path/to/creature
```

### 生態瓶 (Terrarium)

```text
  +---------+       +---------------------------+
  |  使用者  |<----->|        Root 代理           |
  +---------+       |  (生態瓶工具、TUI)          |
                    +---------------------------+
                          |               ^
              送任務  |               |  觀察結果
                          v               |
                    +---------------------------+
                    |     生態瓶層                |
                    |   (純接線，無 LLM)          |
                    +-------+----------+--------+
                    |  swe  |  coder   |  ....  |
                    +-------+----------+--------+
```

生態瓶是純接線層，管理生物的生命週期、它們之間的頻道、以及框架層級的 **輸出接線 (output wiring)** — 把生物回合結束的輸出自動送到指定目標。沒有 LLM、不做決策 — 只是執行期。生物不知道自己在生態瓶裡；它們一樣可以獨立跑。

生態瓶是我們對橫向多代理的 **一種提案架構** — 兩種互補的合作機制 (頻道處理條件性/選用性流量；輸出接線處理確定性的 pipeline 邊)，加上熱插拔與觀察。模式還在演化中；開放問題放在 [ROADMAP](ROADMAP.md)。當單一生物可以自己拆解任務時，用子代理 (縱向) 比較簡單 — 對多數「我要上下文隔離」的直覺而言。

### Root 代理

生態瓶可以定義一個 root 代理，站在團隊外面、透過生態瓶管理工具來操作團隊。使用者對 root 講話；root 對團隊講話。

### 頻道 (Channel)

頻道是通訊底層 — 用於生態瓶，以及單一生物內部的 agent-to-agent 模式。

- **Queue** — 每則訊息被一個消費者收到
- **Broadcast** — 每則訊息被所有訂閱者收到

### 模組

一隻生物有六個概念模組。**其中五個是使用者可擴充的** — 可以在設定或 Python 裡換掉實作。第六個控制器是驅動它們的推理迴圈；你很少換它 (真的要換的話，你其實是在寫下一代框架)。

| 模組 | 做什麼 | 自訂範例 |
|------|--------|----------|
| **Input** | 接收外部事件 | Discord listener、webhook、語音輸入 |
| **Output** | 送出 agent 輸出 | Discord sender、TTS、檔案寫入 |
| **Tool** | 執行動作 | API 呼叫、資料庫存取、RAG 檢索 |
| **Trigger** | 產生自動事件 | 計時器、排程器、頻道 watcher |
| **Sub-agent** | 委派任務執行 | 規劃、程式審查、研究 |

另外還有**外掛**，會修改模組**之間**的連接而不 fork 它們 (prompt 外掛、lifecycle hook)。看[外掛使用指南](docs/zh-TW/guides/plugins.md)。

### 環境與工作階段

- **環境 (Environment)** — 生態瓶共享狀態 (共用頻道)。
- **工作階段 (Session)** — 生物私有狀態 (scratchpad、私有頻道、子代理狀態)。

預設私有、共享需明確 opt-in。

## 實際能力

KohakuTerrarium 已經內建：

- 檔案、shell、網頁、JSON、頻道、觸發器、內省工具，含單次編輯與多點編輯原語。
- 探索、規劃、實作、審查、摘要、研究用的內建子代理。
- 背景工具執行與非阻塞的 agent 流程。
- 工作階段持久化，可恢復操作狀態。
- FTS + vector 記憶搜尋 (model2vec / sentence-transformer / API embedding 提供者)。
- 長時間執行的 agent 用非阻塞自動壓縮。
- MCP (Model Context Protocol) 整合 — stdio 與 HTTP 傳輸。
- 生物、外掛、生態瓶、可重用 agent 套件的套件管理器 (`kt install`、`kt update`)。
- 透過 `Agent`、`AgentSession`、`TerrariumRuntime`、`KohakuManager` 嵌入 Python。
- HTTP 與 WebSocket 服務。
- 網頁 dashboard 與原生桌面 app。
- 自訂模組與外掛系統。

## 程式化使用

Agent 是 async Python 值。嵌進去：

```python
import asyncio
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.runtime import TerrariumRuntime

async def main():
    # 單一 agent
    agent = Agent.from_path("@kt-biome/creatures/swe")
    agent.set_output_handler(lambda text: print(text, end=""), replace_default=True)
    await agent.start()
    await agent.inject_input("說明這個 codebase 在做什麼。")
    await agent.stop()

    # 多代理生態瓶
    runtime = TerrariumRuntime(load_terrarium_config("@kt-biome/terrariums/swe_team"))
    await runtime.start()
    tasks = runtime.environment.shared_channels.get("tasks")
    await tasks.send(ChannelMessage(sender="user", content="修 auth bug。"))
    await runtime.run()
    await runtime.stop()

asyncio.run(main())
```

### 組合代數

因為 agent 是 Python 值，它們可以用運算子組合。`>>` (sequence)、`&` (parallel)、`|` (fallback)、`*N` (retry)、`.iterate` (async 迴圈)：

```python
import asyncio
from kohakuterrarium.compose import agent, factory
from kohakuterrarium.core.config import load_agent_config

def make_agent(name, prompt):
    config = load_agent_config("@kt-biome/creatures/general")
    config.name, config.system_prompt, config.tools, config.subagents = name, prompt, [], []
    return config

async def main():
    # 持久 agent (對話會累積)
    async with await agent(make_agent("writer", "你是一位寫手。")) as writer, \
               await agent(make_agent("reviewer", "你是嚴格的審查者。通過就回 APPROVED。")) as reviewer:

        pipeline = writer >> (lambda text: f"審查這篇：\n{text}") >> reviewer

        async for feedback in pipeline.iterate("寫一首關於 coding 的俳句"):
            print(f"Reviewer: {feedback[:100]}")
            if "APPROVED" in feedback:
                break

    # 平行 ensemble + retry + fallback
    fast = factory(make_agent("fast", "簡短回答。"))
    deep = factory(make_agent("deep", "徹底回答。"))
    safe = (fast & deep) >> (lambda results: max(results, key=len))
    safe_with_retry = (safe * 2) | fast
    print(await safe_with_retry("什麼是遞迴？"))

asyncio.run(main())
```

更多：[程式化使用](docs/zh-TW/guides/programmatic-usage.md)、[組合](docs/zh-TW/guides/composition.md)、[Python API](docs/zh-TW/reference/python.md)、[`examples/code/`](examples/)。

## 執行期介面

### CLI 與 TUI

- **cli** — 豐富的行內終端體驗
- **tui** — 全螢幕 Textual 應用
- **plain** — 簡單 stdout/stdin，給 pipe 與 CI 用

見 [CLI 參考](docs/zh-TW/reference/cli.md)。

### 網頁 dashboard

Vue 的 dashboard + FastAPI 伺服器。

```bash
kt web                       # 一次性、前台執行
kt serve start               # 長期常駐
# 前端開發：npm run dev --prefix src/kohakuterrarium-frontend
```

見 [HTTP API](docs/zh-TW/reference/http.md)、[Serving 指南](docs/zh-TW/guides/serving.md)、[前端架構](docs/zh-TW/dev/frontend.md)。

### 桌面 app

`kt app` 把網頁 UI 開在原生桌面視窗裡 (需要 `pywebview`)。

## 工作階段、記憶、恢復

工作階段預設存在 `~/.kohakuterrarium/sessions/` (除非停用)。

```bash
kt resume            # 互動選擇
kt resume --last     # 接最近的一個
kt resume swe_team   # 用名稱前綴恢復
```

同一個 store 也驅動可搜尋歷史：

```bash
kt embedding <session>                       # 建 FTS + vector 索引
kt search <session> "auth bug fix"           # 混合 / 語意 / FTS 搜尋
```

而且 agent 可以透過 `search_memory` 工具搜尋自己的歷史。

`.kohakutr` 檔案儲存對話、工具呼叫、事件、scratchpad、子代理狀態、頻道訊息、job、可恢復的觸發器、設定 metadata。

見 [工作階段](docs/zh-TW/guides/sessions.md)、[記憶](docs/zh-TW/guides/memory.md)。

## 套件、預設、範例

生物是設計來被打包、安裝、重用、分享的。

```bash
kt install https://github.com/someone/cool-creatures.git
kt install ./my-creatures -e
kt list
kt update --all
```

用套件參照執行已安裝的設定：

```bash
kt run @cool-creatures/creatures/my-agent
kt terrarium run @cool-creatures/terrariums/my-team
```

可用資源：

- [`kt-biome/`](https://github.com/Kohaku-Lab/kt-biome) — 官方展示生物、生態瓶、外掛套件
- `examples/agent-apps/` — 設定驅動的生物範例
- `examples/code/` — Python 使用範例
- `examples/terrariums/` — 多代理範例
- `examples/plugins/` — 外掛範例

見 [examples/README.md](examples/README.md)。

## Codebase 地圖

```text
src/kohakuterrarium/
  core/              # Agent 執行期、控制器、executor、事件、environment
  bootstrap/         # Agent 初始化工廠 (LLM、工具、I/O、觸發器、外掛)
  cli/               # `kt` 指令分派
  terrarium/         # 多代理執行期、拓樸接線、熱插拔、持久化
  builtins/          # 內建工具、子代理、I/O、TUI、使用者指令、CLI UI
  builtin_skills/    # Markdown skill 檔 (按需載入的說明)
  session/           # 工作階段持久化、記憶搜尋、embeddings
  serving/           # 與傳輸無關的服務管理器與事件串流
  api/               # FastAPI HTTP + WebSocket 伺服器
  compose/           # 組合代數原語
  mcp/               # MCP client 管理器
  modules/           # 工具、輸入、輸出、觸發器、子代理、使用者指令的 base protocol
  llm/               # LLM 提供者、設定檔、API 金鑰管理
  parsing/           # 工具呼叫解析與串流處理
  prompt/            # 提示詞聚合、外掛、skill 載入
  testing/           # 測試基礎建設 (ScriptedLLM、TestAgentBuilder、recorder)

src/kohakuterrarium-frontend/   # Vue 前端
kt-biome/                       # (獨立 repo) 官方 OOTB 套件
examples/                       # 範例生物、生態瓶、程式碼、外掛
docs/                           # 教學、使用指南、概念、參考、開發
```

每個子套件都有自己的 README 說明檔案、相依方向、不變式。

## 文件地圖

完整文件在 [`docs/`](docs/zh-TW/README.md)。

### 教學
[第一隻生物](docs/zh-TW/tutorials/first-creature.md) · [第一個生態瓶](docs/zh-TW/tutorials/first-terrarium.md) · [第一次 Python 嵌入](docs/zh-TW/tutorials/first-python-embedding.md) · [第一個自訂工具](docs/zh-TW/tutorials/first-custom-tool.md) · [第一個外掛](docs/zh-TW/tutorials/first-plugin.md)

### 使用指南
[快速開始](docs/zh-TW/guides/getting-started.md) · [撰寫生物](docs/zh-TW/guides/creatures.md) · [生態瓶](docs/zh-TW/guides/terrariums.md) · [工作階段](docs/zh-TW/guides/sessions.md) · [記憶](docs/zh-TW/guides/memory.md) · [設定檔](docs/zh-TW/guides/configuration.md) · [程式化使用](docs/zh-TW/guides/programmatic-usage.md) · [組合](docs/zh-TW/guides/composition.md) · [自訂模組](docs/zh-TW/guides/custom-modules.md) · [外掛](docs/zh-TW/guides/plugins.md) · [MCP](docs/zh-TW/guides/mcp.md) · [套件](docs/zh-TW/guides/packages.md) · [Serving](docs/zh-TW/guides/serving.md) · [範例](docs/zh-TW/guides/examples.md)

### 概念
[詞彙表](docs/zh-TW/concepts/glossary.md) · [Why KohakuTerrarium](docs/zh-TW/concepts/foundations/why-kohakuterrarium.md) · [什麼是 agent](docs/zh-TW/concepts/foundations/what-is-an-agent.md) · [組合一個 agent](docs/zh-TW/concepts/foundations/composing-an-agent.md) · [模組](docs/zh-TW/concepts/modules/README.md) · [Agent 作為 Python 物件](docs/zh-TW/concepts/python-native/agent-as-python-object.md) · [組合代數](docs/zh-TW/concepts/python-native/composition-algebra.md) · [多代理](docs/zh-TW/concepts/multi-agent/README.md) · [模式](docs/zh-TW/concepts/patterns.md) · [邊界](docs/zh-TW/concepts/boundaries.md)

### 參考
[CLI](docs/zh-TW/reference/cli.md) · [HTTP](docs/zh-TW/reference/http.md) · [Python API](docs/zh-TW/reference/python.md) · [設定檔](docs/zh-TW/reference/configuration.md) · [內建模組](docs/zh-TW/reference/builtins.md) · [外掛 hook](docs/zh-TW/reference/plugin-hooks.md)

## Roadmap

近期方向：更可靠的生態瓶流程、更豐富的 UI 輸出 / 互動模組 (CLI / TUI / 網頁)、更多內建生物、外掛、整合、更好的 daemon 背後的工作流 (給長時間執行與遠端使用)。見 [ROADMAP.md](ROADMAP.md)。

## 貢獻

- [貢獻文件](docs/zh-TW/dev/README.md)
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
