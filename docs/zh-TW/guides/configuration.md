---
title: 撰寫設定
summary: 生物設定的結構、繼承、提示詞鏈，以及日常撰寫會用到的重要欄位。
tags:
  - guides
  - config
  - creature
---

# 設定

給想要微調一隻現成的生物、或接一隻新的生物，而不想把參考文件每個欄位都讀過的人。

生物設定用 YAML (也支援 JSON/TOML)。每個頂層 key 對映到 `AgentConfig` 的一個欄位；`controller`、`input`、`output` 這類子區塊是自己的 dataclass、有自己的欄位。這份指南以任務為導向 — 完整的欄位清單請看 [reference/configuration](../reference/configuration.md)。

觀念預備：[撰寫生物](creatures.md)、[組合一個 agent](../concepts/foundations/composing-an-agent.md)。

任何地方都能用環境變數插值：`${VAR}` 或 `${VAR:default}`。

## 怎麼換 model？

從 `~/.kohakuterrarium/llm_profiles.yaml` 挑一個 preset (或用 `kt config llm add` 新增)：

```yaml
controller:
  llm: claude-opus-4.7
  reasoning_effort: high
```

你也可以釘住 preset 的某個 **variation** — 內建 preset 會暴露 `reasoning`、`speed`、`thinking` 這類 group (見 [reference/builtins — Variation groups](../reference/builtins.md#variation-groups))：

```yaml
controller:
  llm: claude-opus-4.7@reasoning=xhigh
  # 或，明確形式
  variation_selections:
    reasoning: xhigh
```

每個 provider 的 effort 旋鈕路徑不同。Codex 設 `reasoning_effort`；OpenAI 直連與 OpenRouter 設 `extra_body.reasoning.effort`；Anthropic 直連設 `extra_body.output_config.effort`；Gemini 直連設 `extra_body.google.thinking_config.thinking_level`。用 variation 會自動幫你接好；如果要手動設，見 [reference/configuration — Provider 專屬 `extra_body` 說明](../reference/configuration.md#provider-專屬-extra_body-說明)。

或是在命令列只為這次執行覆寫：

```bash
kt run path/to/creature --llm gpt-5.4
```

如果想全部寫死在 config 裡、不要 profile 檔，就用 `model` + `api_key_env` + `base_url`：

```yaml
controller:
  model: gpt-4o
  api_key_env: OPENAI_API_KEY
  base_url: https://api.openai.com/v1
  temperature: 0.3
```

## 怎麼繼承 OOTB 生物？

```yaml
name: my-swe
base_config: "@kt-biome/creatures/swe"
controller:
  reasoning_effort: xhigh
tools:
  - name: my_tool
    type: custom
    module: ./tools/my_tool.py
```

純量會覆蓋；`controller`/`input`/`output` 是淺層合併；列表會延伸、並依 `name` 去重。如果要取代整個列表而不是延伸：

```yaml
no_inherit: [tools, subagents]
```

## 怎麼加工具？

內建工具的簡寫：

```yaml
tools:
  - bash
  - read
  - web_search
```

帶選項的：

```yaml
tools:
  - name: web_search
    options:
      max_results: 10
      region: us-en
```

本地 custom 模組：

```yaml
tools:
  - name: my_tool
    type: custom
    module: ./tools/my_tool.py
    class: MyTool
```

來自已安裝套件的 `kohaku.yaml`：

```yaml
tools:
  - name: kql
    type: package
```

協定請看 [自訂模組](custom-modules.md)。

## 怎麼加子代理？

```yaml
subagents:
  - plan
  - worker
  - name: my_critic
    type: custom
    module: ./subagents/critic.py
    config: CRITIC_CONFIG
    interactive: true       # 跨父回合持續活著
    can_modify: true
```

內建：`worker`、`coordinator`、`explore`、`plan`、`research`、`critic`、`response`、`memory_read`、`memory_write`、`summarize`。

## 怎麼加觸發器？

```yaml
triggers:
  - type: timer
    options: { interval: 300 }
    prompt: "Check for pending tasks."
  - type: channel
    options: { channel: alerts }
  - type: context
    options: { debounce_ms: 200 }
    prompt: "Context changed — re-plan if needed."
```

內建：`timer`、`context`、`channel`、`custom`、`package`。觸發器觸發時 `prompt` 會塞進 `TriggerEvent.prompt_override`。需要時鐘對齊的 scheduler 時，請把 `SchedulerTrigger` 暴露成 setup 工具 — 見 [怎麼加工具？](#怎麼加工具) 以及 [reference/builtins](../reference/builtins.md#可安裝的-trigger以-type-trigger-形式暴露為工具) 裡的 `add_schedule`。

## 怎麼設定壓縮？

```yaml
compact:
  enabled: true
  threshold: 0.8
  target: 0.5
  keep_recent_turns: 5
  compact_model: gpt-4o-mini
```

壓縮在做什麼請看 [工作階段](sessions.md)。

## 怎麼加自訂 input？

```yaml
input:
  type: custom
  module: ./inputs/discord.py
  class: DiscordInput
  options:
    token: "${DISCORD_TOKEN}"
    channel_id: 123456
```

內建型別：`cli`、`cli_nonblocking`、`tui`、`none`。音訊/ASR 輸入應設定為 custom 或 package 模組；協定請看 conversational 範例與[自訂模組](custom-modules.md)。

## 怎麼加 named output sink？

當工具或子代理想把東西導到特定頻道 (TTS、Discord、檔案) 時很實用：

```yaml
output:
  type: stdout
  named_outputs:
    tts:
      type: console_tts        # 逐字印出來，適合快速 demo
      options: { char_delay: 0.02 }
    discord:
      type: custom
      module: ./outputs/discord.py
      class: DiscordOutput
      options: { webhook_url: "${DISCORD_WEBHOOK}" }
```

內建輸出型別：`stdout`、`stdout_prefixed`、`console_tts`、`dummy_tts`、`tui`。沒有純 `tts` 型別 — `console_tts` 與 `dummy_tts` 是出廠的 TTS-shaped 輸出；更完整的 TTS 後端以 custom/package 輸出的形式出貨。

## 怎麼用外掛擋工具？

一個會擋掉危險指令的 lifecycle 外掛：

```yaml
plugins:
  - name: tool_guard
    type: custom
    module: ./plugins/tool_guard.py
    class: ToolGuard
    options:
      deny_patterns: ["rm -rf", "dd if="]
```

外掛類別怎麼寫請看 [外掛](plugins.md)，參考實作在 [examples/plugins/tool_guard.py](../../examples/plugins/tool_guard.py)。

## 怎麼註冊 MCP server？

每隻生物：

```yaml
mcp_servers:
  - name: sqlite
    transport: stdio
    command: mcp-server-sqlite
    args: ["/var/db/my.db"]
  - name: docs_api
    transport: http
    url: https://mcp.example.com/sse
    env: { API_KEY: "${DOCS_API_KEY}" }
```

全域 (`~/.kohakuterrarium/mcp_servers.yaml`) 用同一份 schema。請看 [MCP](mcp.md)。

## 怎麼換工具呼叫格式？

```yaml
tool_format: bracket        # 預設：[/name]@@arg=value\n[name/]
# 或
tool_format: xml            # <name arg="value"></name>
# 或
tool_format: native         # provider 原生的 function calling
```

每種格式的具體樣子看 [生物指南 — 工具格式](creatures.md)；要做完全自訂的分隔符看 [reference/configuration.md — `tool_format`](../reference/configuration.md)。

## 怎麼選 dynamic 或 static skill mode？

```yaml
skill_mode: dynamic   # 預設 — `info` 框架指令會在需要時才載完整文件
# 或
skill_mode: static    # 完整工具文件直接塞進 system prompt
```

## 怎麼讓生物沒有使用者輸入也能持續運作？

```yaml
input:
  type: none
triggers:
  - type: timer
    options: { interval: 60 }
    prompt: "Check for anomalies."
```

`none` input 加任何一種觸發器就是標準的 monitor agent 模式。

## 怎麼設執行上限？

```yaml
termination:
  max_turns: 15
  max_duration: 600
  idle_timeout: 120
  keywords: ["DONE", "ABORT"]
```

任一條件符合就會停下代理。

## 怎麼接一條確定性的 pipeline 邊？

生物跑在生態瓶裡時，`output_wiring` 會把每一次回合結束變成一個 `creature_output` 事件，直接落到另一隻生物的佇列裡 — 完全繞過頻道：

```yaml
output_wiring:
  - runner                                   # 簡寫：把輸出送到 `runner`
  - to: analyzer
    prompt: "[From coder] {content}"         # 模板；{content} 等會被填上
  - { to: root, with_content: false }        # 只是 metadata ping
```

生物不在生態瓶裡時，`output_wiring` 是 no-op。完整條目形狀見 [reference/configuration — 輸出接線](../reference/configuration.md#輸出接線)，生態瓶側的視角見 [生態瓶指南 — 輸出接線](terrariums.md#輸出接線)。

## 怎麼讓多隻生物共用狀態 (不透過生態瓶)？

給它們一樣的 `session_key`：

```yaml
name: writer
session_key: shared-workspace
---
name: reviewer
session_key: shared-workspace
```

這兩隻生物現在會共用 `Scratchpad` 與 `ChannelRegistry`。當多隻生物跑在同一個程序、又不想搭生態瓶時很方便。

## 怎麼設定記憶 / embedding？

```yaml
memory:
  embedding:
    provider: model2vec
    model: "@retrieval"
```

詳情看 [記憶](memory.md)。

## 怎麼把生物釘在特定工作目錄？

```bash
kt run path/to/creature --pwd /path/to/project
```

`pwd` 會傳進每個工具的 `ToolContext`。

## 疑難排解

- **環境變數沒展開。** 用 `${VAR}` (有大括號)。`$VAR` 會被當字面字串。
- **子 config「搞丟」了父層的某個工具。** 因為你寫了 `no_inherit: [tools]`。拿掉就會改成延伸。
- **Config 載入成功但工具不見。** 簡寫名稱會去查內建工具目錄 — 拼錯會靜靜 fall through。跑 `kt info path/to/creature` 檢查。
- **兩個設定互相打架。** CLI 覆寫 (`--llm`) > config > `llm_profiles.yaml` 的 `default_model`。

## 延伸閱讀

- [Reference / configuration](../reference/configuration.md) — 每個欄位、型別、預設值。
- [撰寫生物](creatures.md) — 資料夾結構與解剖。
- [外掛](plugins.md)、[自訂模組](custom-modules.md)、[MCP](mcp.md)、[記憶](memory.md) — 特定介面怎麼接。
