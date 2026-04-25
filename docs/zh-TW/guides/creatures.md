---
title: 撰寫生物
summary: 提示詞設計、工具與子代理選擇、LLM 設定檔選用，以及把生物發佈為可重用套件。
tags:
  - guides
  - creature
  - authoring
---

# 生物

給想要撰寫、自訂或封裝獨立 agent 的讀者。

**生物**是一個自包含的 agent：擁有自己的控制器、工具、子代理、觸發器、提示詞與 I/O。一隻生物可以獨立執行（`kt run path/to/creature`）、繼承自另一隻生物，或封裝在套件中發佈。它永遠不會知道自己是否身處某個生態瓶中。

概念預習：[什麼是 agent](../concepts/foundations/what-is-an-agent.md)、[組合 agent](../concepts/foundations/composing-an-agent.md)、[模組索引](../concepts/modules/README.md)。

## 結構

一隻生物存在於一個資料夾中：

```
creatures/my-agent/
  config.yaml            # 必填
  prompts/
    system.md            # 由 system_prompt_file 引用
    context.md           # 由 prompt_context_files 引用
  tools/                 # 可選的自訂工具模組 (慣例)
  subagents/             # 可選的自訂子代理設定 (慣例)
  memory/                # 可選的文字 / Markdown 記憶檔案 (慣例)
```

查找順序為：`config.yaml` → `config.yml` → `config.json` → `config.toml`。環境變數插值（`${VAR}` 或 `${VAR:default}`）可在 YAML 任意位置使用。子資料夾名稱只是慣例 — loader 會依每個 `module:` 路徑相對於代理資料夾解析，但並不會自動掃 `tools/` 或 `subagents/`。

### 最小設定

```yaml
name: my-agent
controller:
  llm: claude-opus-4.7
system_prompt_file: prompts/system.md
tools:
  - read
  - write
  - bash
```

每個欄位都對應到 `AgentConfig` dataclass。任務導向索引請見[設定](configuration.md)；完整欄位請見 [reference/configuration](../reference/configuration.md)。

## 繼承

可將既有生物作為基底重用：

```yaml
name: my-swe
base_config: "@kt-biome/creatures/swe"
controller:
  reasoning_effort: high
tools:
  - name: my_tool          # 新工具，會附加進去
    type: custom
    module: ./tools/my_tool.py
```

規則——所有欄位都遵循同一套統一模型：

- **純量**：子層覆蓋父層。
- **字典**（`controller`、`input`、`output`、`memory`、`compact`……）：淺層合併。
- **以識別鍵為準的列表**（`tools`、`subagents`、`plugins`、`mcp_servers`、`triggers`）：依 `name` 做 union。若名稱衝突，**子層勝出**，並原地取代基底項目。沒有 `name` 的項目則直接串接。
- **提示詞檔案**：`system_prompt_file` 會沿著繼承鏈串接；行內 `system_prompt` 最後附加。
- `base_config` 可解析 `@pkg/...`、`creatures/<name>`（往上尋找專案根目錄），或相對路徑。

有兩個指令可用來退出預設繼承：

```yaml
# 1. 完全丟棄某個繼承欄位，然後從頭定義
no_inherit: [tools, plugins]
tools:
  - { name: think, type: builtin }

# 2. 取代整條繼承來的提示詞鏈（是
#    no_inherit: [system_prompt, system_prompt_file] 的語法糖）
prompt_mode: replace
system_prompt_file: prompts/brand_new.md
```

### 何時使用 `prompt_mode: replace`

這對**子代理**與**生態瓶中的生物**特別有用：它們可能繼承同一個基底 persona，但需要完全不同的語氣。

```yaml
# 生物設定中的子代理項目
subagents:
  - name: niche_responder
    base_config: "@kt-biome/subagents/response"
    prompt_mode: replace
    system_prompt_file: prompts/niche_persona.md
```

```yaml
# 生態瓶中的生物，把 OOTB 生物重新用途化為團隊專家
creatures:
  - name: reviewer
    base_config: "@kt-biome/creatures/critic"
    prompt_mode: replace
    system_prompt: |
      You are the team's lead reviewer. Speak only to approve or reject, with one-line reasoning.
```

預設值（`prompt_mode: concat`）適合用在：你想擴充基底提示詞，而不是取代它，尤其當它代表的是某種通用契約時。

### 覆寫與擴充列表項目

以 `name` 發生衝突時，子層項目會勝出：

```yaml
base_config: "@kt-biome/creatures/general"
tools:
  - { name: bash, type: custom, module: ./tools/safe_bash.py, class: SafeBash }
```

子層的 `bash` 會原地取代基底的 `bash`；其他繼承來的工具則會保留。

## 提示詞檔案

請將 system prompt 放在 Markdown 中。裡面只放**人格與指引**——工具列表、呼叫語法與完整工具文件都會自動聚合。

```markdown
<!-- prompts/system.md -->
You are a focused SWE agent. Use tools immediately rather than narrating.
Prefer minimal diffs. Validate before declaring done.
```

模板變數來自 `prompt_context_files`：

```yaml
prompt_context_files:
  style_guide: prompts/style.md
  today:       memory/today.md
```

在 `system.md` 中：

```
## Style guide
{{ style_guide }}

## Today
{{ today }}
```

聚合器會自動附加工具列表、框架提示、環境資訊與 `CLAUDE.md`。請不要自行重複這些內容。

## Skill mode：dynamic 與 static

- `skill_mode: dynamic`（預設）— 工具會以單行描述出現在提示詞中。控制器會在需要時透過 `info` 框架指令載入完整文件。
- `skill_mode: static` — 所有工具文件都會預先內嵌（system prompt 較大，但 round-trip 較少）。

除非你需要固定、可稽核的提示詞，否則建議使用 `dynamic`。

## 工具格式

它控制 LLM 輸出工具呼叫（以及框架指令呼叫）時所用的語法。這會同時影響 parser 與 system prompt 中的 framework-hints 區塊。

以下是 `bash` 呼叫、`command=ls` 的具體例子：

- `bracket`（預設）— 以 `[/name]` 開始、`[name/]` 結束，參數用 `@@key=value` 行表示：
  ```
  [/bash]
  @@command=ls
  [bash/]
  ```
- `xml` — 標準的帶屬性標籤形式：
  ```
  <bash command="ls"></bash>
  ```
- `native` — 提供者原生 function calling（OpenAI / Anthropic tool use）。LLM 不輸出文字區塊，而由 API 以結構化方式攜帶呼叫。
- dict — 自訂分隔符（見 [configuration reference — `tool_format`](../reference/configuration.md)）。

三種格式可以互換——選擇最適合你模型的即可。`native` 在主流提供者上通常最穩定；`bracket` 則幾乎到處都能用，包括本機模型。

## 工具與子代理

```yaml
tools:
  - read                              # shorthand = builtin
  - bash
  - name: my_tool                     # custom / package tool
    type: custom
    module: ./tools/my_tool.py
    class: MyTool
  - name: web_search
    options:
      max_results: 5
  # 把通用 trigger 暴露成 setup tool —— LLM 可以在執行期
  # 呼叫這個工具名稱來安裝它。框架會以 `CallableTriggerTool`
  # 包裝 trigger 類別；簡短描述前面會加上「**Trigger** — 」
  # 讓 LLM 知道這是在安裝長期副作用，而不是立即執行一次行為。
  - { name: add_timer, type: trigger }
  - { name: watch_channel, type: trigger }
  - { name: add_schedule, type: trigger }

subagents:
  - worker
  - plan
  - name: my_specialist
    type: custom
    module: ./subagents/specialist.py
    config: SPECIALIST_CONFIG
    interactive: true                 # 在父代理多輪之間持續存活
    can_modify: true
```

可安裝型 trigger 是逐生物 opt-in 的——沒有任何 `type: trigger` 項目的生物，無法在執行期安裝 trigger。每個通用 `BaseTrigger` 子類別都會宣告自己的 `setup_tool_name`（例如 `add_timer`）、`setup_description` 與 `setup_param_schema`。若要自己撰寫，請見[自訂模組 — Triggers](custom-modules.md)。

完整的工具與子代理目錄請見 [reference/builtins](../reference/builtins.md)；撰寫自訂內容請見[自訂模組](custom-modules.md)。

## 觸發器

```yaml
triggers:
  - type: timer
    options: { interval: 600 }
    prompt: "Health check: anything pending?"
  - type: channel
    options: { channel: alerts }
  - type: context
    options: { debounce_ms: 200 }
    prompt: "Context shifted — reconsider plan."
  - type: custom
    module: ./triggers/webhook.py
    class: WebhookTrigger
```

內建型別：`timer`、`context`、`channel`、`custom`、`package`。需要時鐘對齊的 scheduler 時，請改用 `add_schedule` setup 工具 (見 [工具與子代理](#工具與子代理))。模組文件：[concepts/modules/trigger](../concepts/modules/trigger.md)。

## 啟動觸發器

會在生物啟動時觸發一次：

```yaml
startup_trigger:
  prompt: "Review the project status and plan today's work."
```

## 終止條件

```yaml
termination:
  max_turns: 20
  max_duration: 300          # 秒
  idle_timeout: 60           # 無事件多久後視為超時（秒）
  keywords: ["DONE", "SHUTDOWN"]
```

只要任一條件達成，agent 就會停止。`keywords` 會對控制器輸出做子字串比對。

## Session key

多隻生物可透過設定 `session_key` 共享同一個 `Session`（scratchpad + channels）：

```yaml
session_key: shared_workspace
```

預設值是生物的 `name`。在生態瓶中，每隻生物都有自己的私有 `Session` 與共享 `Environment`；見 [concepts/modules/session-and-environment](../concepts/modules/session-and-environment.md)。

## 框架指令

控制器可以輸出內嵌指令直接與框架溝通（不需工具 round-trip）。這些指令會記錄在提示詞中的 framework-hints 區塊。

框架指令與工具呼叫共用同一語法家族——也就是你設定的 `tool_format`（bracket、XML、native）是什麼，它就用什麼。以下為預設 bracket 例子，placeholder 以裸識別字表示：

- `[/info]tool_or_subagent[info/]` — 按需載入完整文件。
- `[/read_job]job_id[read_job/]` — 讀取背景作業輸出（在 body 中接受 `--lines N` 與 `--offset M`）。
- `[/jobs][jobs/]` — 列出執行中的作業與其 ID。
- `[/wait]job_id[wait/]` — 阻塞目前這一輪，直到背景作業完成。

指令名稱與工具名稱共享同一個命名空間；讀取作業輸出的指令之所以叫 `read_job`，就是為了避免與 `read` 檔案讀取工具衝突。

這些機制讓 agent 能讀取串流工具輸出、查詢自己沒記住的文件，以及與自己的背景工作同步。

## 使用者指令

由**使用者**在 CLI/TUI 提示字元輸入的斜線指令。內建如下：

| 指令 | 別名 | 效果 |
|---|---|---|
| `/help` | `/h`, `/?` | 列出指令 |
| `/status` | `/info` | 模型、訊息、工具、作業、壓縮狀態 |
| `/clear` | | 清除對話 |
| `/model [name]` | `/llm` | 列出或切換 LLM 設定檔 |
| `/compact` | | 手動壓縮 |
| `/regen` | `/regenerate` | 重新執行上一輪 assistant 回應 |
| `/plugin [list\|enable\|disable\|toggle] [name]` | `/plugins` | 管理生命週期外掛 |
| `/exit` | `/quit`, `/q` | 優雅退出 |

自訂使用者指令可放在 `builtins/user_commands/`，也可封裝在套件中發佈。撰寫方式請見[自訂模組](custom-modules.md)。

## 輸入與輸出

```yaml
input:
  type: cli                  # 或：cli_nonblocking、tui、none、custom、package
  prompt: "> "
  exit_commands: ["exit", "quit"]

output:
  type: stdout               # 或：stdout_prefixed、console_tts、dummy_tts、tui、custom、package
  named_outputs:
    discord:
      type: custom
      module: ./outputs/discord.py
      class: DiscordOutput
      options: { webhook_url: "${DISCORD_WEBHOOK}" }
```

`named_outputs` 讓工具或子代理能路由到特定輸出端（例如 Discord webhook、TTS、檔案）。詳見 [concepts/modules/output](../concepts/modules/output.md)。
