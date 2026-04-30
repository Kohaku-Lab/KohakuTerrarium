---
title: 設定
summary: 生物、生態瓶、LLM profile、MCP server、壓縮、外掛、輸出接線的每一個欄位。
tags:
  - reference
  - config
---

# 設定

生物、生態瓶、LLM profile、MCP server、套件 manifest 的所有欄位。檔案格式：YAML (建議)、JSON、TOML。所有檔案都支援載入時的 `${VAR}` / `${VAR:default}` 環境變數插值。

生物跟生態瓶怎麼搭的心智模型請看 [concepts/boundaries](../concepts/boundaries.md)。實際操作的例子請看 [guides/configuration](../guides/configuration.md) 與 [guides/creatures](../guides/creatures.md)。

## 路徑解析

Config 裡指到其他檔案或套件的欄位，解析順序：

1. `@<pkg>/<path-inside-pkg>` → `~/.kohakuterrarium/packages/<pkg>/<path-inside-pkg>` (遇到 `<pkg>.link` 會跟著走，給 editable 安裝用)。
2. `creatures/<name>` 或類似的 project-relative 形式 → 從當前代理資料夾往上走到專案根。
3. 其他情況相對於代理資料夾 (繼承情境下則 fallback 到 base config 的資料夾)。

---

## 生物設定 (`config.yaml`)

由 `kohakuterrarium.core.config.load_agent_config` 載入。檔案查找順序：`config.yaml` → `config.yml` → `config.json` → `config.toml`。

### 頂層欄位

| 欄位 | 型別 | 預設 | 必要 | 說明 |
|---|---|---|---|---|
| `name` | str | — | 是 | 生物名稱。沒設 `session_key` 時就拿來當預設 session key。 |
| `version` | str | `"1.0"` | 否 | 資訊用。 |
| `base_config` | str | `null` | 否 | 要繼承的 parent config (`@package/path`、`creatures/<name>`、或相對路徑)。 |
| `controller` | dict | `{}` | 否 | LLM/控制器區塊。見 [Controller](#controller-區塊)。 |
| `system_prompt` | str | `"You are a helpful assistant."` | 否 | 行內 system prompt。 |
| `system_prompt_file` | str | `null` | 否 | Markdown prompt 檔路徑，相對於代理資料夾。會沿繼承鏈串接。 |
| `prompt_context_files` | dict[str,str] | `{}` | 否 | Jinja 變數 → 檔案路徑；prompt 渲染時讀進來插入。 |
| `skill_mode` | str | `"dynamic"` | 否 | `dynamic` (需要時透過 `info` 框架指令載) 或 `static` (完整文件一次塞進去)。 |
| `include_tools_in_prompt` | bool | `true` | 否 | 是否納入自動生成的工具清單。 |
| `include_hints_in_prompt` | bool | `true` | 否 | 是否納入框架提示 (工具呼叫語法、`info` / `read_job` / `jobs` / `wait` 指令範例)。 |
| `max_messages` | int | `0` | 否 | 對話上限。`0` = 無上限。 |
| `ephemeral` | bool | `false` | 否 | 每回合後清空對話 (group-chat 模式)。 |
| `session_key` | str | `null` | 否 | 覆寫預設 session key (原本是 `name`)。 |
| `input` | dict | `{}` | 否 | Input 模組設定。見 [Input](#input)。 |
| `output` | dict | `{}` | 否 | Output 模組設定。見 [Output](#output)。 |
| `tools` | list | `[]` | 否 | 工具條目。見 [工具](#工具)。 |
| `subagents` | list | `[]` | 否 | 子代理條目。見 [子代理](#子代理)。 |
| `triggers` | list | `[]` | 否 | 觸發器條目。見 [觸發器](#觸發器)。 |
| `compact` | dict | `null` | 否 | 壓縮設定。見 [壓縮](#壓縮)。 |
| `startup_trigger` | dict | `null` | 否 | 啟動時觸發一次的觸發器。`{prompt: "..."}`。 |
| `termination` | dict | `null` | 否 | 終止條件。見 [終止](#終止)。 |
| `max_subagent_depth` | int | `3` | 否 | 子代理最大巢狀深度。`0` = 無上限。 |
| `tool_format` | str \| dict | `"bracket"` | 否 | `bracket`、`xml`、`native`，或自訂 dict 格式。`native` 需要設定的 LLM provider 支援結構化的 tool calling。 |
| `mcp_servers` | list | `[]` | 否 | 每隻代理的 MCP server。見 [MCP server](#生物-config-裡的-mcp-server)。 |
| `plugins` | list | `[]` | 否 | Lifecycle 外掛。見 [外掛](#外掛)。 |
| `no_inherit` | list[str] | `[]` | 否 | 改成**取代**而非合併的 key。例如 `[tools, subagents]`。 |
| `memory` | dict | `{}` | 否 | `memory.embedding.{provider,model}`。見 [記憶](#記憶)。 |
| `output_wiring` | list | `[]` | 否 | 每隻生物的回合輸出自動路由。見 [輸出接線](#輸出接線)。 |

### Controller 區塊

為了向後相容，下列欄位也可以放在頂層。

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `llm` | str | `""` | `~/.kohakuterrarium/llm_profiles.yaml` 裡的 profile 參照 (例如 `gpt-5.4`、`claude-opus-4.7`)。可帶上行內的 variation selector，例如 `claude-opus-4.7@reasoning=xhigh`。 |
| `model` | str | `""` | 沒設 `llm` 時用的行內 model id。也接受 `name@group=option` 形式的 selector。 |
| `provider` | str | `""` | 當 `model` 同一個 id 綁到多個 backend 時 (例如 `openai` vs `openrouter`) 用來消除歧義。 |
| `variation_selections` | dict[str,str] | `{}` | 每個 group 的 variation 覆寫 — `{group_name: option_name}`。見 [Variation selector](#variation-selector)。 |
| `variation` | str | `""` | 單一 option 選擇的簡寫；會對 preset 的 group 做解析。 |
| `auth_mode` | str | `""` | 空 (自動)、`codex-oauth` 等。 |
| `api_key_env` | str | `""` | 裝 key 的環境變數。 |
| `base_url` | str | `""` | 覆寫 endpoint URL。 |
| `temperature` | float | `0.7` | Sampling temperature。 |
| `max_tokens` | int \| null | `null` | 對映到解析後 profile 的 `max_output` (每次回應的輸出上限)，不是 `max_context` (整體 window)。 |
| `reasoning_effort` | str | `"medium"` | `none`、`minimal`、`low`、`medium`、`high`、`xhigh`。Codex 直接讀取；其他 provider 請用 `extra_body` (見 [Provider 專屬 `extra_body` 說明](#provider-專屬-extra_body-說明))。 |
| `service_tier` | str | `null` | `priority`、`flex`。 |
| `extra_body` | dict | `{}` | 深層合併到解析後 preset 的 `extra_body` 上 (後者可能已經帶有 variation patch)。 |
| `skill_mode`、`include_tools_in_prompt`、`include_hints_in_prompt`、`max_messages`、`ephemeral`、`tool_format` | | | 對映頂層同名欄位。 |

每回合解析順序 (見 `llm/profiles.py:resolve_controller_llm`)：

1. CLI 旗標 `--llm` 勝過 YAML 裡的 `controller.llm`。
2. 否則用 `controller.llm` (preset 名稱 + 可選的 `@group=option` selector)。
3. 否則用 `controller.model` — 依 model id 對內建與使用者 preset registry 做比對。`controller.provider` 用來消除跨 backend 的碰撞；`name@group=option` 形式的 selector 也會被拆出來。
4. 如果 `llm` 跟 `model` 都沒設，fallback 到 `llm_profiles.yaml` 的 `default_model`。
5. Profile 解析完成後，控制器的 `temperature`、`reasoning_effort`、`service_tier`、`max_tokens` (remap 到 `max_output`) 與 `extra_body` 會疊加上去。`extra_body` 是深層合併，其他覆寫是純量取代。

### Variation selector

Preset 可以暴露 **variation groups** — 兩層 dict `{group_name: {option_name: patch}}`，讓單一 preset 服務多組旋鈕 (reasoning effort、speed、thinking level) 而不用重複建立條目。選擇可以寫在 preset 參照字串裡，或透過 controller 上明確的 dict 欄位。

簡寫形式 (`--llm`、`controller.llm`、或 `controller.model` 都能用)：

```text
claude-opus-4.7@reasoning=xhigh                 # 單一 group = option
claude-opus-4.7@reasoning=xhigh,speed=fast      # 多 group，用逗號分隔
claude-opus-4.7@xhigh                           # 裸 option；自動解析到
                                                # 唯一符合的 group
                                                # (有歧義會失敗)
```

明確形式 (在 config 組裝 selector 時建議用這個)：

```yaml
controller:
  llm: claude-opus-4.7
  variation_selections:
    reasoning: xhigh
  # 或者，單一 option 簡寫：
  variation: xhigh
```

規則：

- 裸簡寫 (`@xhigh`) 如果有多個 group 都吻合該 option，會被拒絕 — 請用 `@group=option` 消除歧義。
- 未知 group 或 option 在解析時會 raise。
- Variation patch 只能寫入下列 root：`temperature`、`reasoning_effort`、`service_tier`、`max_context`、`max_output`、`extra_body`。其他都會被拒絕。
- 同一個 dotted path 上的跨 group 碰撞會 raise — 兩個 selection 不能同時宣告 `extra_body.reasoning.effort`。

每個 preset 的 group 與 option 目錄請看 [builtins.md — Variation groups](builtins.md#variation-groups)。

### Provider 專屬 `extra_body` 說明

`extra_body` 會被深層合併進 JSON request body。每個 provider 讀 reasoning/effort 旋鈕的路徑不一樣 — 請設 provider 實際會認的那個旋鈕：

| Provider | 標準路徑 | 說明 |
|---|---|---|
| Codex (ChatGPT-OAuth) | 頂層 `reasoning_effort`、`service_tier` | `reasoning_effort`：`none\|low\|medium\|high\|xhigh`。Fast 模式：在 `gpt-5.4` 上用 `speed=fast` variation — 它會對映到 `service_tier: priority`。直接寫 `service_tier: fast` 會被 OpenAI API 拒絕。 |
| OpenAI direct (`-api` preset) | `extra_body.reasoning.effort` | 完整級距 `none\|low\|medium\|high\|xhigh`。 |
| OpenRouter (`-or` preset) | `extra_body.reasoning.effort` | 統一級距 `minimal\|low\|medium\|high`；只有少數幾個 model (Opus 4.7、GPT-5.x) 會認 `xhigh`。 |
| Anthropic direct | `extra_body.output_config.effort` | Compat endpoint 會默默丟掉頂層 `reasoning_effort` / `service_tier`。Opus 4.7：`low\|medium\|high\|xhigh\|max`；Opus 4.6 / Sonnet 4.6：`low\|medium\|high\|max`。Haiku 4.5 用較舊的 `thinking.budget_tokens`。 |
| Gemini direct | `extra_body.google.thinking_config.thinking_level` | `LOW\|MEDIUM\|HIGH` (Pro) 或 `MINIMAL\|LOW\|MEDIUM\|HIGH` (Flash / Flash-Lite)。 |

Anthropic-via-OpenRouter (`claude-*-or`) preset 出廠已帶有 `extra_body.cache_control: {type: ephemeral}`；你在 `controller.extra_body` 行內寫的東西會深層合併疊在上面，可以關掉或替換它。

### Input

Dict 欄位：`{type, module?, class?, options?, ...型別專屬 key}`。

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `type` | str | `"cli"` | `cli`、`cli_nonblocking`、`tui`、`none`、`custom`、`package`。音訊/ASR 輸入是 custom/package 模組。 |
| `module` | str | — | 給 `custom` (例如 `./custom/input.py`) 或 `package` (例如 `pkg.mod`) 用。 |
| `class` | str | — | 要 instantiate 的類別。YAML key 是 `class`；loader 會存到 dataclass 屬性 `class_name`。 |
| `options` | dict | `{}` | 模組專屬選項。 |
| `prompt` | str | `"> "` | CLI prompt (只對 plain `cli` input 有效 — Rich CLI 跟 TUI 會忽略)。 |
| `exit_commands` | list[str] | `[]` | 觸發離開的字串。 |

### Output

支援一個預設輸出，加上選用的 `named_outputs` 做側通道 (例如 Discord webhook)。

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `type` | str | `"stdout"` | `stdout`、`stdout_prefixed`、`console_tts`、`dummy_tts`、`tui`、`custom`、`package`。 |
| `module` | str | — | `custom`/`package` 輸出模組用。 |
| `class` | str | — | 要 instantiate 的類別。YAML key 是 `class`；loader 會存到 dataclass 屬性 `class_name`。 |
| `options` | dict | `{}` | 模組專屬選項。 |
| `controller_direct` | bool | `true` | 把控制器文字透過預設輸出送出。 |
| `named_outputs` | dict[str, OutputConfigItem] | `{}` | Named 側輸出。每個 item 結構跟預設相同。 |

### 工具

工具條目的 list。每條可以是 dict 或簡寫字串 (同名的內建工具)。

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `name` | str | — | 工具名 (必填)。如果 `type: trigger`，必須與 trigger 的 `setup_tool_name` 相符。 |
| `type` | str | `"builtin"` | `builtin`、`trigger`、`custom`、`package`。 |
| `module` | str | — | 給 `custom` (例如 `./custom/tools/my_tool.py`) 或 `package` 用。 |
| `class` | str | — | `custom`/`package` 要 instantiate 的類別。YAML key 是 `class`；存到 dataclass 屬性 `class_name`。 |
| `doc` | str | — | 覆寫 skill 文件檔。 |
| `options` | dict | `{}` | 工具專屬選項。 |

工具類型：

- `builtin` — 依 `name` 對內建工具目錄做解析。
- `trigger` — 把一個通用 trigger 類別暴露成 LLM 可呼叫的 setup 工具。`name` 必須與 trigger 的 `setup_tool_name` 相符。出廠 setup 工具：`add_timer` (TimerTrigger)、`watch_channel` (ChannelTrigger)、`add_schedule` (SchedulerTrigger)。
- `custom` / `package` — 從 `module` + `class` 載入類別。

簡寫：

```yaml
tools:
  - bash
  - read
  - write
```

### 子代理

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `name` | str | — | 子代理識別字。 |
| `type` | str | `"builtin"` | `builtin`、`custom`、`package`。 |
| `module` | str | — | 給 `custom`/`package` 用。 |
| `config` | str | — | 模組裡具名的 config 物件 (例如 `MY_AGENT_CONFIG`)。YAML key 是 `config`；存到 dataclass 屬性 `config_name`。 |
| `description` | str | — | 父代理 prompt 裡用到的描述。 |
| `tools` | list[str] | `[]` | 子代理被允許使用的工具。 |
| `can_modify` | bool | `false` | 子代理能不能做會改東西的操作。 |
| `interactive` | bool | `false` | 跨回合持續活著、接收 context update。 |
| `options` | dict | `{}` | 子代理專屬選項。 |

簡寫：裸字串會被視為內建子代理名稱：

```yaml
subagents:
  - explore
  - worker
```

純 YAML 內聯設定：使用不帶 `module`/`config` 的 `type: custom`；條目中未知欄位會轉發給 `SubAgentConfig.from_dict`：

```yaml
subagents:
  - name: dependency_mapper
    type: custom
    system_prompt: "Map dependencies and return a compact summary."
    tools: [glob, grep, read, tree]
    default_plugins: ["default-runtime"]
    turn_budget: [40, 60]
    tool_call_budget: [75, 100]
```

內建子代理已經宣告 `default_plugins: ["default-runtime"]`、`turn_budget: [40, 60]`、`tool_call_budget: [75, 100]`，且沒有 `walltime_budget`。

子代理選項欄位還包括執行期與共享預算控制：

- `default_plugins: ["default-runtime"]` — 載入預算 ticker/alarm/gate 以及自動壓縮。
- `turn_budget: [soft, hard]` — 子代理 LLM turn 的軟/硬限制。
- `tool_call_budget: [soft, hard]` — 子代理工具呼叫的軟/硬限制。
- `walltime_budget: [soft, hard]` — 可選的牆鐘時間限制（秒）。
- `budget_inherit: true`（預設）— 如果父級存在共享舊式 iteration budget，子代理會複用它。
- `budget_allocation: N` — 子代理得到一份新的獨立舊式 `N` turn 預算。
- `budget_inherit: false` 且無 allocation — 子代理不使用父級共享舊式預算。

### 觸發器

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `type` | str | — | `timer`、`context`、`channel`、`custom`、`package`。 |
| `module` | str | — | 給 `custom`/`package` 用。 |
| `class` | str | — | 要 instantiate 的類別。YAML key 是 `class`；存到 dataclass 屬性 `class_name`。 |
| `prompt` | str | — | 觸發器觸發時注入的預設 prompt。 |
| `options` | dict | `{}` | 觸發器專屬選項。 |

各型別常見選項：

- `timer`：`interval` (秒)、`immediate` (bool，預設 `false`)。
- `context`：`debounce_ms` (int，預設 `100`) — 做 debounce 的 context-update 觸發器。
- `channel`：`channel` (名稱)、`filter_sender` (選用)。

如果要用時鐘對齊的 scheduler，請在 `tools` 條目裡用 `type: trigger, name: add_schedule` 把 `SchedulerTrigger` 暴露成 LLM 可呼叫的 setup 工具 (見 [工具](#工具))，不要寫在 `triggers:` list 裡。

### 壓縮

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `enabled` | bool | `true` | 開啟壓縮。 |
| `max_tokens` | int | profile 預設 | 目標 token 上限。 |
| `threshold` | float | `0.8` | 達到 `max_tokens` 多少比例時啟動壓縮。 |
| `target` | float | `0.4` | 壓縮後目標比例。 |
| `keep_recent_turns` | int | `8` | 原樣保留的回合數。 |
| `compact_model` | str | 控制器的 model | 摘要用的 LLM 覆寫。 |

### 輸出接線

框架層級路由條目的 list。每回合結束時，框架會組一個 `creature_output` `TriggerEvent`，直接推進每個目標生物的事件佇列 — 完全繞過頻道。討論見 [生態瓶指南 — 輸出接線](../guides/terrariums.md#輸出接線) 與 [patterns.md — pattern 1b](../concepts/patterns.md)；這一節是 config 參考。

條目欄位：

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `to` | str | — | 目標生物名稱，或魔術字串 `"root"`。 |
| `with_content` | bool | `true` | 設 `false` 時，事件 `content` 為空 (只是 metadata ping)。 |
| `prompt` | str \| null | `null` | 接收端 prompt override 的模板。沒設時依 `with_content` 用預設模板。 |
| `prompt_format` | `simple` \| `jinja` | `"simple"` | `simple` 用 `str.format_map`；`jinja` 用 `prompt.template` 渲染 (支援條件式 / filter)。 |

模板變數 (兩種格式都有)：`source`、`target`、`content`、`turn_index`、`source_event_type`、`with_content`。

簡寫 — 裸字串等同於 `{to: <str>, with_content: true}`：

```yaml
output_wiring:
  - runner                                   # 簡寫
  - { to: root, with_content: false }        # lifecycle ping
  - to: analyzer
    prompt: "[From coder] {content}"         # simple (預設)
  - to: critic
    prompt: "{{ source | upper }}: {{ content }}"
    prompt_format: jinja
```

注意：

- 只有生物跑在生態瓶裡時才有意義。獨立生物設了 `output_wiring` 也不會發出任何東西 (resolver 是生態瓶 runtime 掛上去的；獨立代理拿到的是 no-op resolver，只會 log 一次)。
- 未知 / 停掉的目標會被 log 然後跳過；不會往源頭生物的 turn finalisation 丟例外。
- 源頭的 `_finalize_processing` 會立刻跑完 — 每個目標的 `_process_event` 各自在自己的 `asyncio.Task` 裡跑，不會因為某個接收者慢就把源頭卡住。

### 終止

任何非零門檻都會生效。輸出含有關鍵字時，關鍵字比對會停下代理。

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `max_turns` | int | `0` | |
| `max_tokens` | int | `0` | |
| `max_duration` | float | `0` | 秒。 |
| `idle_timeout` | float | `0` | 無事件秒數。 |
| `keywords` | list[str] | `[]` | 區分大小寫的 substring 比對。 |

### 生物 config 裡的 MCP server

每隻代理自己的 MCP server。代理啟動時連線。在 `~/.kohakuterrarium/mcp_servers.yaml` 有一份全域目錄 (由 `kt config mcp` 管理) 用的是同一套 schema；代理在各自 config 裡宣告要用哪些。

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `name` | str | — | Server 識別字。 |
| `transport` | `stdio` \| `http` | — | Transport。`http` 走 Server-Sent Events (SSE)。 |
| `command` | str | — | stdio 執行檔。 |
| `args` | list[str] | `[]` | stdio 參數。 |
| `env` | dict[str,str] | `{}` | stdio 環境變數。 |
| `url` | str | — | HTTP/SSE endpoint。 |

### 外掛

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `name` | str | — | 外掛識別字。 |
| `type` | str | `"builtin"` | `builtin`、`custom`、`package`。 |
| `module` | str | — | 給 `custom` (例如 `./custom/plugins/my.py`) 或 `package` 用。 |
| `class` 或 `class_name` | str | — | 要 instantiate 的類別。 |
| `description` | str | — | 自由格式 metadata。 |
| `options` | dict | `{}` | 外掛專屬選項。 |

簡寫：裸字串會被當成套件解析的外掛名。

### 記憶

```yaml
memory:
  embedding:
    provider: model2vec       # 或 sentence-transformer、api
    model: "@best"            # preset 別名或 HuggingFace 路徑
```

Provider 選項：

- `model2vec` (預設，不用 torch)。
- `sentence-transformer` (torch，品質較高)。

Preset 別名：`@tiny`、`@base`、`@retrieval`、`@best`、`@multilingual`、`@multilingual-best`、`@science`、`@nomic`、`@gemma`。

### 繼承規則

`base_config` 走前面路徑解析規則。合併用一套規則套在所有欄位上：

- **純量** — 子層覆寫。
- **Dict** (`controller`、`input`、`output`、`memory`、`compact`…) — 淺層合併；子層 key 在頂層覆寫父層。
- **以 identity 為 key 的 list** (`tools`、`subagents`、`plugins`、`mcp_servers`、`triggers`) — 依 `name` 聯集。撞名時**子層勝出**並原地取代 base 條目 (保留 base 順序)。沒 `name` 的項目會串接。
- **其他 list** — 子層取代父層。
- **Prompt 檔** — `system_prompt_file` 沿繼承鏈串接；行內 `system_prompt` 最後附上。

兩個可以退出預設行為的指令：

| 指令 | 效果 |
|-----------|--------|
| `no_inherit: [field, …]` | 列出的欄位拋棄繼承值。對純量、dict、identity list、prompt 鏈都適用。 |
| `prompt_mode: concat \| replace` | `concat` (預設) 保留繼承 prompt 檔鏈 + 行內；`replace` 清空繼承 prompt — 等同 `no_inherit: [system_prompt, system_prompt_file]`。 |

**範例。**

覆寫某個繼承來的工具但不取代整份清單：

```yaml
base_config: "@kt-biome/creatures/swe"
tools:
  - { name: bash, type: custom, module: ./tools/safe_bash.py, class: SafeBash }
```

清空重來：完全拋棄繼承的工具。

```yaml
base_config: "@kt-biome/creatures/general"
no_inherit: [tools]
tools:
  - { name: think, type: builtin }
```

為特殊人格完全取代 prompt：

```yaml
base_config: "@kt-biome/creatures/general"
prompt_mode: replace
system_prompt_file: prompts/niche.md
```

### 檔案慣例

```
creatures/<name>/
  config.yaml           # 必要
  prompts/system.md     # 有參照就要存在
  tools/                # 自訂工具模組 (慣例)
  memory/               # context 檔 (慣例)
  subagents/            # 自訂子代理 config (慣例)
```

這些子資料夾名稱只是慣例。Loader 透過 `ModuleLoader` 依每個 `module:` 路徑相對於代理資料夾解析 — 並不會自動掃 `tools/` 或 `subagents/`，所以每個自訂模組都必須在 `config.yaml` 裡宣告。

---

## 生態瓶設定 (`terrarium.yaml`)

由 `kohakuterrarium.terrarium.config.load_terrarium_config` 載入。

```yaml
terrarium:
  name: str
  root:                  # 選用 — 生態瓶外的 root 代理
    base_config: str     # 或任何 AgentConfig 欄位直接行內寫
    ...
  creatures:
    - name: str
      base_config: str   # 舊別名：`config:`
      channels:
        listen: [str]
        can_send: [str]
      output_log: bool         # 預設 false
      output_log_size: int     # 預設 100
      ...                      # 任何 AgentConfig 覆寫
  channels:
    <name>:
      type: queue | broadcast  # 預設 queue
      description: str
    # 或簡寫 — 字串就是 description：
    # <name>: "description"
```

生態瓶欄位摘要：

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `name` | str | — | 生態瓶名稱。 |
| `root` | object | `null` | 選用的 root 代理設定。一定會拿到生態瓶管理工具。 |
| `creatures` | list | `[]` | 跑在生態瓶裡的生物。 |
| `channels` | dict | `{}` | 共享頻道宣告。 |

生物條目欄位 (也接受任何 AgentConfig 欄位直接行內寫，例如 `system_prompt_file`、`controller`、`output_wiring` 等等)：

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `name` | str | — | 生物名稱。 |
| `base_config` (或 `config`) | str | — | Config 路徑 (代理 config)。 |
| `channels.listen` | list[str] | `[]` | 生物消費的頻道。 |
| `channels.can_send` | list[str] | `[]` | 生物能發佈的頻道。 |
| `output_log` | bool | `false` | 每隻生物抓 stdout。 |
| `output_log_size` | int | `100` | 每隻生物 log buffer 最大行數。 |
| `output_wiring` | list | `[]` | 框架層級把這隻生物回合結束的輸出自動送給其他生物。條目形狀見 [輸出接線](#輸出接線)。 |

頻道條目欄位：

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `type` | `queue` \| `broadcast` | `queue` | 傳遞語意。 |
| `description` | str | `""` | 會寫在頻道拓樸 prompt 裡。 |

自動建立的頻道：

- 每隻生物一條以它名字命名的 `queue` (DM 用)。
- 設了 `root` 時多一條 `report_to_root` queue。

Root 代理：

- 拿到附有 `terrarium_*` 與 `creature_*` 工具的 `TerrariumToolManager`。
- 自動 listen 每一條生物頻道、收 `report_to_root`。
- 繼承 / 合併規則跟生物相同。

---

## LLM profile (`~/.kohakuterrarium/llm_profiles.yaml`)

```yaml
version: 3
default_model: <preset name>

backends:
  <provider-name>:
    backend_type: openai | anthropic | codex  # transport 實作
    base_url: str
    api_key_env: str

presets:
  <preset-name>:
    provider: <backend-name>   # backends 參照或內建
    model: str                 # model id
    max_context: int           # 預設 256000
    max_output: int            # 預設 65536
    temperature: float         # 選用
    reasoning_effort: str      # none | minimal | low | medium | high | xhigh
    service_tier: str          # priority | flex
    extra_body: dict
    variation_groups:          # 選用 — 見 Variation selector
      <group>:
        <option>:
          <dotted.path>: value
```

標準的 `backend_type` 值是：

- `openai` — OpenAI-compatible `/chat/completions` endpoint。
- `anthropic` — 透過官方 `anthropic` Python package 存取 Anthropic-compatible Messages API endpoint (Claude、MiniMax 的 `/anthropic/v1/messages`，以及相容 proxy)。
- `codex` — ChatGPT 訂閱 Codex OAuth。

舊值 `codex-oauth` 為了向後相容仍會被接受，讀取時會正規化為 `codex`。

內建 provider 名稱 (`codex`、`openai`、`openrouter`、`anthropic`、`gemini`、`mimo`) 不能刪除；它們的 base URL 與 `api_key_env` 由內建預設值寫死。每隻代理仍然可以用 `controller.base_url` / `controller.api_key_env` 覆寫。

### 新增自訂 LLM backend provider

多數 provider 只需要一個 backend entry 加上一個 preset：

```yaml
backends:
  minimax-anthropic:
    backend_type: anthropic
    base_url: https://api.minimax.io/anthropic
    api_key_env: MINIMAX_API_KEY

presets:
  minimax-anthropic:
    minimax-m2.7:
      model: MiniMax-M2.7
      max_context: 200000
      max_output: 2048
```

provider 暴露 OpenAI-compatible `/chat/completions` API 時使用 `backend_type: openai`；暴露 Anthropic-compatible `/v1/messages` API 時使用 `backend_type: anthropic`。API key 會先從 `~/.kohakuterrarium/api_keys.yaml` (`kt login <provider-name>` / `kt config key set <provider-name>`) 讀取，再 fallback 到 `api_key_env`。
Anthropic backend preset 可以透過 `extra_body` 傳遞 SDK request field；provider beta header 可設在 preset 的 `extra_body.extra_headers`。

若要在程式碼裡加入新的 transport 實作，請在 `src/kohakuterrarium/llm/` 下建立 `BaseLLMProvider` 子類，用 KohakuTerrarium 內部 OpenAI-shaped message dict 實作 `_stream_chat()` 與 `_complete_chat()`，把 backend type 加進 `validate_backend_type()`，並擴充 `bootstrap/llm.py` 讓解析後的 `LLMProfile.backend_type` 能實例化它。provider 專屬的 request/response 轉換應停留在這個邊界，不要為了單一 provider 改 controller 或 conversation storage。

所有附帶的 preset 請看 [builtins.md — LLM presets](builtins.md#llm-presets)；每個 preset 的 group 與 option 目錄見 [builtins.md — Variation groups](builtins.md#variation-groups)；在 controller config 裡怎麼選特定 variation 見 [Variation selector](#variation-selector)。

---

## MCP server 目錄 (`~/.kohakuterrarium/mcp_servers.yaml`)

全域 MCP registry，用來替代每隻代理的 `mcp_servers:`。

```yaml
- name: sqlite
  transport: stdio
  command: mcp-server-sqlite
  args: ["/path/to/db"]
  env: {}
- name: web_api
  transport: http
  url: https://mcp.example.com/sse
  env: { API_KEY: ${MCP_API_KEY} }
```

欄位：

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `name` | str | — | 唯一識別字。 |
| `transport` | `stdio` \| `http` | — | Transport。`http` 走 Server-Sent Events (SSE)。 |
| `command` | str | — | stdio 執行檔。 |
| `args` | list[str] | `[]` | stdio 參數。 |
| `env` | dict[str,str] | `{}` | stdio 環境變數。 |
| `url` | str | — | `http` transport 的 endpoint。 |

---

## 套件 manifest (`kohaku.yaml`)

```yaml
name: my-package
version: "1.0.0"
description: "..."
creatures:
  - name: researcher
terrariums:
  - name: research_team
tools:
  - name: my_tool
    module: my_package.tools
    class: MyTool
plugins:
  - name: my_plugin
    module: my_package.plugins
    class: MyPlugin
llm_presets:
  - name: my_preset
python_dependencies:
  - requests>=2.28.0
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| `name` | str | 套件名稱；會裝在 `~/.kohakuterrarium/packages/<name>/`。 |
| `version` | str | Semver。 |
| `description` | str | 自由格式。 |
| `creatures` | list | `[{name}]` — `creatures/<name>/` 下的生物 config。 |
| `terrariums` | list | `[{name}]` — `terrariums/<name>/` 下的生態瓶 config。 |
| `tools` | list | `[{name, module, class}]` — 提供的工具類別。 |
| `plugins` | list | `[{name, module, class}]` — 提供的外掛。 |
| `llm_presets` | list | `[{name}]` — 提供的 LLM preset (實際值在套件裡)。 |
| `python_dependencies` | list[str] | Pip requirement 字串。 |

安裝模式：

- `kt install <git_url>` — clone。
- `kt install <path>` — 複製。
- `kt install <path> -e` — 寫一個指到來源的 `<name>.link`。

---

## API key 儲存 (`~/.kohakuterrarium/api_keys.yaml`)

由 `kt login` 與 `kt config key set` 管理。格式：

```yaml
openai: sk-...
openrouter: sk-or-...
anthropic: sk-ant-...
```

解析順序：儲存的檔案 → 環境變數 (`api_key_env`) → 空。

---

## 延伸閱讀

- 概念：[邊界](../concepts/boundaries.md)、[組合一個 agent](../concepts/foundations/composing-an-agent.md)、[多代理概覽](../concepts/multi-agent/README.md)。
- 指南：[設定](../guides/configuration.md)、[撰寫生物](../guides/creatures.md)、[生態瓶](../guides/terrariums.md)。
- 參考：[CLI](cli.md)、[內建模組](builtins.md)、[Python API](python.md)。
