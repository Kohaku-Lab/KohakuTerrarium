---
title: 內建模組
summary: 隨附的工具、子代理、trigger、輸入與輸出——參數形式、行為與預設值。
tags:
  - reference
  - builtins
---

# 內建模組

KohakuTerrarium 隨附的所有內建工具、子代理、輸入、輸出、使用者命令、框架命令、LLM provider 與 LLM preset，都整理在這裡。

如果你想了解工具與子代理各自的形狀，請閱讀
[concepts/modules/tool](../concepts/modules/tool.md) 與
[concepts/modules/sub-agent](../concepts/modules/sub-agent.md)。
如果你需要以任務為導向的說明，請參考 [guides/creatures](../guides/creatures.md)
與 [guides/custom-modules](../guides/custom-modules.md)。

## 工具

內建工具類別位於
`src/kohakuterrarium/builtins/tools/`。在 creature 設定中的 `tools:`
底下，使用裸名稱即可註冊。

### Shell 與腳本

**`bash`** — 執行 shell 命令。會在 `bash`、`zsh`、`sh`、`fish`、`pwsh`
之中選擇第一個可用者。遵守 `KT_SHELL_PATH`。會擷取 stdout 與 stderr，並在達到上限時截斷。直接執行。

- 參數：`command`（str）、`working_dir`（str，可選）、
  `timeout`（float，可選）。

**`python`** — 執行 Python 子程序。遵守 `working_dir` 與
`timeout`。直接執行。

- 參數：`code`（str）、`working_dir`、`timeout`。

### 檔案操作

**`read`** — 讀取文字、圖片或 PDF 內容。會記錄每個檔案的讀取狀態。圖片會以 `base64` data URL 回傳。PDF 支援需要
`pymupdf`。直接執行。

- 參數：`path`（str）、`offset`（int，可選）、`limit`（int，可選）。

**`write`** — 建立或覆寫檔案。會建立父目錄。除非先讀取過檔案（或指定 `new`），否則會阻止覆寫。直接執行。

- 參數：`path`、`content`、`new`（bool，可選）。

**`edit`** — 自動偵測 unified diff（`@@`）或搜尋／取代形式。不接受二進位檔案。直接執行。

- 參數：`path`、`old_text`/`new_text` 或 `diff`、`replace_all`（bool）。

**`multi_edit`** — 對單一檔案依序套用一串編輯。以檔案為單位保持原子性。模式有：`strict`（每個編輯都必須成功套用）、`best_effort`（略過失敗項目）、預設（部分套用並附回報）。直接執行。

- 參數：`path`、`edits: list[{old, new}]`、`mode`。

**`glob`** — 依修改時間排序的 glob。遵守 `.gitignore`。會提早終止。直接執行。

- 參數：`pattern`、`root`（可選）、`limit`（可選）。

**`grep`** — 跨檔案進行正規表示式搜尋。支援 `ignore_case`。會略過二進位檔案。直接執行。

- 參數：`pattern`、`path`（可選）、`ignore_case`（bool）、
  `max_matches`。

**`tree`** — 目錄列表，並為 markdown 檔案附上 YAML frontmatter 摘要。直接執行。

- 參數：`path`、`depth`。

### 結構化資料

**`json_read`** — 以 dot-path 讀取 JSON 文件。直接執行。

- 參數：`path`、`query`（dot-path）。

**`json_write`** — 在 dot-path 指派值。必要時會建立巢狀物件。直接執行。

- 參數：`path`、`query`、`value`。

### Web

**`web_fetch`** — 將 URL 擷取為 markdown。依序嘗試 `crawl4ai` →
`trafilatura` → Jina proxy → `httpx + html2text`。上限 100k 字元，逾時 30 秒。直接執行。

- 參數：`url`。

**`web_search`** — 使用 DuckDuckGo 搜尋，回傳 markdown 格式結果。直接執行。

- 參數：`query`、`max_results`（int）、`region`（str）。

### 互動與記憶

**`ask_user`** — 透過 stdin 向使用者提問（僅限 CLI 或 TUI）。
具狀態性。

- 參數：`question`。

**`think`** — 不做任何事；只是把推理保留為工具事件，寫進事件日誌。直接執行。

- 參數：`thought`。

**`scratchpad`** — 以 session 為範圍的 KV 儲存。由同一個 session 中的各 agent 共用。

- 參數：`action`（`get` | `set` | `delete` | `list`）、`key`、`value`。

**`search_memory`** — 對 session 已索引事件進行 FTS／semantic／auto 搜尋。可依 agent 過濾。

- 參數：`query`、`mode`（`auto`/`fts`/`semantic`/`hybrid`）、`k`、
  `agent`。

### 通訊

**`send_message`** — 向某個 channel 發出訊息。會先解析 creature 本地 channel，再解析環境中的共用 channel。直接執行。

- 參數：`channel`、`content`、`sender`（可選）。

### 內省

**`info`** — 按需載入任一工具或子代理的文件。會委派到
`src/kohakuterrarium/builtin_skills/` 底下的 skill manifest，以及各 agent 的覆寫設定。直接執行。

- 參數：`target`（工具或子代理名稱）。

**`stop_task`** — 依 id 取消正在執行的背景任務或 trigger。直接執行。

- 參數：`job_id`（任一工具呼叫返回的 job id；或 `add_timer`/`watch_channel`/`add_schedule` 回傳的 trigger id）。

### 可安裝的 trigger（以 `type: trigger` 形式暴露為工具）

每個通用 trigger 類別都會透過
`modules/trigger/callable.py:CallableTriggerTool` 包裝成各自的工具。creature 可以在 `tools:`
底下列出 trigger 的 `setup_tool_name`，並指定
`type: trigger` 來選擇啟用。工具描述會以前綴
`**Trigger** — ` 開頭，讓 LLM 知道呼叫它會安裝一個長期存在的副作用。這三個工具都會立即回傳已安裝的 trigger id；trigger 本身則在背景中執行。

**`add_timer`**（包裝 `TimerTrigger`）— 安裝週期性計時器。

- 參數：`interval`（秒，必填）、`prompt`（必填）、`immediate`（bool，預設 false）。

**`watch_channel`**（包裝 `ChannelTrigger`）— 監聽具名 channel。

- 參數：`channel_name`（必填）、`prompt`（可選，支援 `{content}`）、`filter_sender`（可選）。
- agent 自己的名稱會自動設為 `ignore_sender`，以避免自我觸發。

**`add_schedule`**（包裝 `SchedulerTrigger`）— 對齊時鐘的排程。

- 參數：`prompt`（必填）；`every_minutes`、`daily_at`（HH:MM）、`hourly_at`（0-59）三者必須且只能擇一。

### Terrarium（僅 root 可用）

**`terrarium_create`** — 啟動新的 terrarium 實例。僅 root 可用。

**`terrarium_send`** — 傳送訊息到 root 所屬 terrarium 中的 channel。

**`creature_start`** — 在執行期間熱插拔啟動 creature。

**`creature_stop`** — 在執行期間停止 creature。

---

## 子代理

隨附的子代理設定位於
`src/kohakuterrarium/builtins/subagents/`。在 creature 設定中的 `subagents:`
底下，以名稱引用即可。

所有內建子代理都會載入 `default_plugins: ["default-runtime"]`，並使用最小執行期預算：turn 軟/硬限制 `40/60`、工具呼叫軟/硬限制 `75/100`，且沒有 walltime 預算。

| 名稱 | 工具 | 用途 |
|---|---|---|
| `worker` | `read`, `write`, `bash`, `glob`, `grep`, `edit`, `multi_edit` | 修 bug、重構、執行驗證。 |
| `coordinator` | `send_message`, `scratchpad` | 拆解 → 分派 → 彙整。 |
| `explore` | `glob`, `grep`, `read`, `tree`, `bash` | 唯讀探索。 |
| `plan` | `explore` 的工具 + `think` | 唯讀規劃。 |
| `research` | `web_search`, `web_fetch`, `read`, `write`, `think`, `scratchpad` | 對外研究。 |
| `critic` | `read`, `glob`, `grep`, `tree`, `bash` | 程式碼審查。 |
| `response` | `read` | 面向使用者的文案產生器。通常設為 `output_to: external`。 |
| `memory_read` | 在 memory 資料夾上使用 `tree`、`read`、`grep` | 從 agent 記憶中回想內容。 |
| `memory_write` | `tree`, `read`, `write` | 將發現持久化到記憶中。 |
| `summarize` | （無工具） | 為交接或重置濃縮對話。 |

---

## 輸入

隨附的輸入模組位於 `src/kohakuterrarium/builtins/inputs/`。

**`cli`** — Stdin 提示。選項：`prompt`、`exit_commands`。

**`cli_nonblocking`** — 介面與 `cli` 相同，但在每個按鍵之間會把控制權交回 event loop (輸入過程中 trigger 會觸發時特別有用)。

**`none`** — 不接收輸入。供僅使用 trigger 的 agent 使用。

音訊/ASR 實作不是內建輸入。conversational 範例在 `examples/agent-apps/conversational/custom/` 底下提供 opt-in 的 `ASRModule`/Whisper 自訂輸入檔；請透過 `type: custom` 載入。

另外兩種輸入型別會動態解析：

- `tui` — 在 TUI 模式下由 Textual app 掛載。
- `custom` / `package` — 透過 `module` + `class` 欄位載入。

---

## 輸出

隨附的輸出模組位於 `src/kohakuterrarium/builtins/outputs/`。

**`stdout`** — 輸出到 stdout。選項：`prefix`、`suffix`、`stream_suffix`、`flush_on_stream`。

**`stdout_prefixed`** — 加上每行 prefix 的 `stdout`，用來標記側輸出。

**`console_tts`** — 只在 console 顯示的 TTS shim，會用可設定的 `char_delay` 把合成後的文字逐字印出來。給 demo 與測試用 — 沒有音訊後端。

**`dummy_tts`** — 安靜的 TTS，會觸發平常那套 TTS 生命週期事件但沒有輸出。測試用。

其他路由型別：

- `tui` — 渲染到 Textual TUI 的 widget 樹。
- `custom` / `package` — 透過 module + class 載入。

沒有純 `tts` 這個 registry key。真正的 TTS 後端 (Fish、Edge、OpenAI 等) 以 custom/package 輸出的形式出貨，繼承 `TTSModule`。

---

## 使用者命令

可在輸入模組內使用的 slash 命令。位於
`src/kohakuterrarium/builtins/user_commands/`。

| 命令 | 別名 | 用途 |
|---|---|---|
| `/help` | `/h`, `/?` | 列出命令。 |
| `/status` | `/info` | 模型、訊息數、工具、jobs、compact 狀態。 |
| `/clear` | | 清除對話（session log 仍會保留歷史）。 |
| `/model [name]` | `/llm` | 顯示目前模型或切換 profile。 |
| `/compact` | | 手動壓縮上下文。 |
| `/regen` | `/regenerate`, `/retry` | 將上一輪 assistant 回應作為 sibling branch 重新執行。 |
| `/edit <message_index> <new content>` | — | 編輯過去的 user message，並從該點作為新 branch 重跑。 |
| `/branch [<turn> <branch_id>\|latest]` | `/br` | 列出或切換 regen/edit alternatives 的 live branch。 |
| `/fork [event_id] [--name name]` | — | 將目前 session 複製成新的 `.kohakutr` 檔案，用於探索替代路線。 |
| `/plugin [list\|enable\|disable\|toggle] [name]` | `/plugins` | 檢視或切換 plugin。 |
| `/exit` | `/quit`, `/q` | 優雅離開。在 web 上可能需要 force 旗標。 |

---

## 框架命令

LLM 可輸出的內嵌指令，可取代工具呼叫。它們會直接與框架溝通（不經過工具往返）。定義於
`src/kohakuterrarium/commands/`。

框架命令使用與工具呼叫**同一語法家族**——它們遵循 creature 設定的 `tool_format`（bracket / XML / native）。預設是帶有裸識別子 placeholder 的 bracket 形式：

- `[/info]tool_or_subagent[info/]` — 按需載入某個工具或子代理的完整文件。
- `[/read_job]job_id[read_job/]` — 讀取背景 job 的輸出。內文支援 `--lines N` 與 `--offset M`。
- `[/jobs][jobs/]` — 列出仍在執行中的 jobs 與其 ID。
- `[/wait]job_id[wait/]` — 阻塞目前這一輪，直到背景 job 完成。

命令名稱與工具名稱共用命名空間；為了避免與讀檔工具 `read` 衝突，讀取 job 輸出的命令命名為 `read_job`。定義於 `src/kohakuterrarium/commands/`。

---

## LLM providers

內建 provider 類型 (backend)：

| Provider | Backend type | Transport | 說明 |
|---|---|---|---|
| `codex` | `codex` | Codex OAuth (ChatGPT 訂閱) | `kt login codex`；透過 `CodexOAuthProvider` 路由。 |
| `openai` | `openai` | OpenAI `/chat/completions` | API-key 驗證 (`OPENAI_API_KEY`)。 |
| `openrouter` | `openai` | 對 OpenRouter 的 OpenAI-compat | API-key 驗證 (`OPENROUTER_API_KEY`)；統一的 `reasoning` 參數。 |
| `anthropic` | `anthropic` | Anthropic-compatible Messages API | API-key 驗證 (`ANTHROPIC_API_KEY`)。使用官方 `anthropic` SDK。Claude 專屬旋鈕走 `extra_body` (`thinking.*`、`output_config.*`)。 |
| `gemini` | `openai` | Google 的 OpenAI-compat endpoint | API-key 驗證 (`GEMINI_API_KEY`)。 |
| `mimo` | `openai` | 小米 MiMo | `kt login mimo`。 |

標準的 backend type 是 `openai`、`anthropic` 與 `codex`。舊的 `codex-oauth` backend type 值在讀取時會默默遷移 (見 [configuration 參考](configuration.md#llm-profiles-kohakuterrariumllm_profilesyaml))。

其他供應商若暴露 OpenAI-compatible `/chat/completions` API，請設 `backend_type: openai`；若暴露 Anthropic-compatible `/v1/messages` API，請設 `backend_type: anthropic`。

## LLM presets

隨附於 `src/kohakuterrarium/llm/presets.py`。可作為 `llm:` 或 `--llm` 的值。

命名慣例 (2026-04 重構後)：

- 直連 / 原生 API 變體是主要名稱 (`claude-opus-4.7`、`gemini-3.1-pro`、`mimo-v2-pro`)。
- 走 OpenRouter 的變體用 `-or` 後綴 (`claude-opus-4.7-or`)。
- OpenAI 是例外：`gpt-5.4` 綁在 **Codex OAuth** provider 上；OpenAI 直連變體用 `-api`，OpenRouter 用 `-or`。
- 舊名稱 (`claude-opus-4.6-direct`、`or-gpt-5.4`、`gemini-3.1-pro-direct`、`mimo-v2-pro-direct`…) 以別名保留，既有 config 仍然可用。

### OpenAI 透過 Codex OAuth

- `gpt-5.4` (別名：`gpt5`、`gpt54`)
- `gpt-5.3-codex` (`gpt53`)
- `gpt-5.1`
- `gpt-4o-codex` (別名：`gpt4o`、`gpt-4o`)
- `gpt-4o-mini-codex` (別名：`gpt-4o-mini`)

### OpenAI 直連 API (`-api` 後綴)

- `gpt-5.4-api` (舊別名：`gpt-5.4-direct`)
- `gpt-5.4-mini-api` (`gpt-5.4-mini-direct`)
- `gpt-5.4-nano-api` (`gpt-5.4-nano-direct`)
- `gpt-5.3-codex-api` (`gpt-5.3-codex-direct`)
- `gpt-5.1-api` (`gpt-5.1-direct`)
- `gpt-4o-api` (`gpt-4o-direct`)
- `gpt-4o-mini-api` (`gpt-4o-mini-direct`)

### OpenAI 透過 OpenRouter (`-or` 後綴)

- `gpt-5.4-or` (舊別名：`or-gpt-5.4`)
- `gpt-5.4-mini-or` (`or-gpt-5.4-mini`)
- `gpt-5.4-nano-or` (`or-gpt-5.4-nano`)
- `gpt-5.3-codex-or` (`or-gpt-5.3-codex`)
- `gpt-5.1-or` (`or-gpt-5.1`)
- `gpt-4o-or` (`or-gpt-4o`)
- `gpt-4o-mini-or` (`or-gpt-4o-mini`)

### Anthropic Claude 直連 (無後綴 — 主要)

走原生 Anthropic-compatible Messages API。Effort 走 `extra_body.output_config.effort`。

- `claude-opus-4.7` (別名：`claude-opus`、`opus`)
- `claude-opus-4.6` (舊別名：`claude-opus-4.6-direct`)
- `claude-sonnet-4.6` (別名：`claude`、`claude-sonnet`、`sonnet`；舊：`claude-sonnet-4.6-direct`)
- `claude-haiku-4.5` (別名：`claude-haiku`、`haiku`；舊：`claude-haiku-4.5-direct`)

### Anthropic Claude 透過 OpenRouter (`-or` 後綴)

- `claude-opus-4.7-or`
- `claude-opus-4.6-or`
- `claude-sonnet-4.6-or`
- `claude-sonnet-4.5-or`
- `claude-haiku-4.5-or`
- `claude-sonnet-4-or` (舊別名：`claude-sonnet-4`)
- `claude-opus-4-or` (舊別名：`claude-opus-4`)

### Google Gemini 直連 (OpenAI-compat)

- `gemini-3.1-pro` (別名：`gemini`、`gemini-pro`；舊：`gemini-3.1-pro-direct`)
- `gemini-3-flash` (`gemini-flash`；舊：`gemini-3-flash-direct`)
- `gemini-3.1-flash-lite` (`gemini-lite`；舊：`gemini-3.1-flash-lite-direct`)

### Google Gemini 透過 OpenRouter (`-or` 後綴)

- `gemini-3.1-pro-or`
- `gemini-3-flash-or`
- `gemini-3.1-flash-lite-or`
- `nano-banana` (生圖模型，OpenRouter)

### Google Gemma (OpenRouter)

- `gemma-4-31b` (別名：`gemma`、`gemma-4`)
- `gemma-4-26b`

### Qwen (OpenRouter)

- `qwen3.5-plus` (`qwen`)
- `qwen3.5-flash`
- `qwen3.5-397b`
- `qwen3.5-27b`
- `qwen3-coder` (`qwen-coder`)
- `qwen3-coder-plus`

### Moonshot Kimi (OpenRouter)

- `kimi-k2.5` (`kimi`)
- `kimi-k2-thinking`

### MiniMax (OpenRouter)

- `minimax-m2.7` (`minimax`)
- `minimax-m2.5`

### 小米 MiMo 直連 (無後綴 — 主要)

- `mimo-v2-pro` (`mimo`；舊別名：`mimo-v2-pro-direct`)
- `mimo-v2-flash` (舊別名：`mimo-v2-flash-direct`)

### 小米 MiMo 透過 OpenRouter (`-or` 後綴)

- `mimo-v2-pro-or`
- `mimo-v2-flash-or`

### GLM (Z.ai，OpenRouter)

- `glm-5`
- `glm-5-turbo` (別名：`glm`)

### xAI Grok (OpenRouter)

- `grok-4` (`grok`)
- `grok-4.20`
- `grok-4.20-multi`
- `grok-4-fast` (`grok-fast`)
- `grok-4.1-fast`
- `grok-code-fast` (`grok-code`)
- `grok-3`
- `grok-3-mini`

### Mistral (OpenRouter)

- `mistral-large-3` (別名：`mistral`、`mistral-large`)
- `mistral-medium-3.1` (`mistral-medium`)
- `mistral-medium-3`
- `mistral-small-4` (`mistral-small`)
- `mistral-small-3.2`
- `magistral-medium` (`magistral`)
- `magistral-small`
- `codestral`
- `devstral-2` (`devstral`)
- `devstral-medium`
- `devstral-small`
- `pixtral-large`
- `ministral-3-14b` (`ministral`)
- `ministral-3-8b`

每個 preset 的 token window (`max_context` / `max_output`) 會因 preset 而異 — 確切值請看 `src/kohakuterrarium/llm/presets.py`，或執行 `kt config llm show <name>`。`controller.max_tokens` 會覆寫 `max_output`；要調壓縮時的 window，請設 `compact.max_tokens`。

內建 preset 合併時也會把安裝套件貢獻的 `llm_presets` 一併納入；見 [configuration.md — Package manifest](configuration.md#package-manifest-kohakuyaml)。

## Variation groups

Variation group 讓單一 preset 暴露多組旋鈕而不用重複建立條目。在 `llm:` / `--llm` 裡用 `preset@group=option` 簡寫，或用 `controller.variation_selections` 指定；見 [configuration 參考 — Variation selector](configuration.md#variation-selector)。

這裡沒列到的 preset 就沒有 variation group — 預設值是固定的。

### OpenAI — Codex OAuth

| Preset | Group | Options |
|---|---|---|
| `gpt-5.4` | `reasoning` | `none`、`low`、`medium`、`high`、`xhigh` |
| `gpt-5.4` | `speed` | `normal`、`fast` (對映 `service_tier: priority`) |
| `gpt-5.3-codex` | `reasoning` | `none`、`low`、`medium`、`high`、`xhigh` |
| `gpt-5.1` | `reasoning` | `none`、`low`、`medium`、`high`、`xhigh` |

### OpenAI — 直連 API (`-api` 後綴)

Patch 到 `extra_body.reasoning.effort`。

| Preset | Group | Options |
|---|---|---|
| `gpt-5.4-api`、`gpt-5.4-mini-api`、`gpt-5.4-nano-api`、`gpt-5.3-codex-api`、`gpt-5.1-api` | `reasoning` | `none`、`low`、`medium`、`high`、`xhigh` |

### OpenAI — OpenRouter (`-or` 後綴)

透過 OpenRouter 統一參數 patch 到 `extra_body.reasoning.effort`。

| Preset | Group | Options |
|---|---|---|
| `gpt-5.4-or`、`gpt-5.4-mini-or`、`gpt-5.4-nano-or`、`gpt-5.3-codex-or`、`gpt-5.1-or` | `reasoning` | `minimal`、`low`、`medium`、`high`、`xhigh` |

### Anthropic — 直連

透過 compat 層 patch 到 `extra_body.output_config.effort`。

| Preset | Group | Options |
|---|---|---|
| `claude-opus-4.7` | `reasoning` | `low`、`medium`、`high`、`xhigh`、`max` |
| `claude-opus-4.6`、`claude-sonnet-4.6` | `reasoning` | `low`、`medium`、`high`、`max` |

Haiku 4.5 走較舊的 extended-thinking (`budget_tokens`)，沒有 variation group。

### Anthropic — OpenRouter (`-or` 後綴)

Patch 到 `extra_body.reasoning.effort`。只有 Opus 4.7 認 `xhigh`。

| Preset | Group | Options |
|---|---|---|
| `claude-opus-4.7-or` | `reasoning` | `minimal`、`low`、`medium`、`high`、`xhigh` |
| `claude-opus-4.6-or`、`claude-sonnet-4.6-or`、`claude-sonnet-4.5-or`、`claude-opus-4-or`、`claude-sonnet-4-or` | `reasoning` | `minimal`、`low`、`medium`、`high` |
| `claude-haiku-4.5-or` | `reasoning` | `off`、`low`、`medium`、`high` |

### Google Gemini — 直連

Patch 到 `extra_body.google.thinking_config.thinking_level`。

| Preset | Group | Options |
|---|---|---|
| `gemini-3.1-pro` | `thinking` | `low`、`medium`、`high` |
| `gemini-3-flash`、`gemini-3.1-flash-lite` | `thinking` | `minimal`、`low`、`medium`、`high` |

### Google Gemini — OpenRouter

| Preset | Group | Options |
|---|---|---|
| `gemini-3.1-pro-or`、`gemini-3-flash-or`、`gemini-3.1-flash-lite-or` | `reasoning` | `minimal`、`low`、`medium`、`high` |

### Gemma / Qwen / Kimi / MiMo / GLM — OpenRouter

共用同一個 OpenRouter 統一 reasoning group (除非另行註明)。

| Preset | Group | Options |
|---|---|---|
| `gemma-4-31b`、`gemma-4-26b` | `reasoning` | `minimal`、`low`、`medium`、`high` |
| `qwen3.5-plus`、`qwen3.5-flash`、`qwen3.5-397b`、`qwen3.5-27b`、`qwen3-coder`、`qwen3-coder-plus` | `reasoning` | `minimal`、`low`、`medium`、`high` |
| `kimi-k2.5` | `reasoning` | `minimal`、`low`、`medium`、`high` |
| `mimo-v2-pro`、`mimo-v2-flash`、`mimo-v2-pro-or`、`mimo-v2-flash-or` | `reasoning` | `minimal`、`low`、`medium`、`high` |
| `glm-5`、`glm-5-turbo` | `reasoning` | `minimal`、`low`、`medium`、`high` |

`kimi-k2-thinking` 永遠開 thinking — 沒有 variation group。

### Mistral — OpenRouter

| Preset | Group | Options |
|---|---|---|
| `mistral-small-4` | `reasoning` | `none`、`high` |

其他 Mistral preset (`mistral-large-3`、`mistral-medium-*`、`mistral-small-3.2`、`codestral`、`devstral-*`、`pixtral-large`、`ministral-*`) 不是 reasoning 模型。`magistral-medium` 與 `magistral-small` 是永遠開 reasoning — 沒有 variation group。

### Grok / MiniMax — OpenRouter

Grok 4.x (`grok-4`、`grok-4.20`、`grok-4.20-multi`、`grok-4-fast`、`grok-4.1-fast`、`grok-code-fast`) 的 reasoning 是強制開啟且不可設定。`grok-3` / `grok-3-mini` 是舊的非 reasoning 模型。`minimax-m2.7` / `minimax-m2.5` 強制開 reasoning。都沒有 variation group。

---

## Prompt plugin

隨附的 prompt plugin (由 `prompt/aggregator.py` 載入)。依 priority 排序 (數字越小越早執行)。

| Priority | 名稱 | 產出 |
|---|---|---|
| 50 | `ToolListPlugin` | 工具名稱 + 一行描述。 |
| 45 | `FrameworkHintsPlugin` | 框架命令範例 (`info`、`read_job`、`jobs`、`wait`) 與 tool-call 格式範例。 |
| 40 | `EnvInfoPlugin` | `cwd`、平台、日期/時間。 |
| 30 | `ProjectInstructionsPlugin` | 載入 `CLAUDE.md` 與 `.claude/rules.md`。 |

自訂 prompt plugin 繼承 `BasePlugin`，在 creature config 的 `plugins` 欄位註冊。生命週期與 callback hook 見 [plugin-hooks.md](plugin-hooks.md)。

---

## Compose 代數

運算子優先順序：`* > | > & > >>`。

| 運算子 | 意義 |
|---|---|
| `a >> b` | 串行 (會自動攤平)。`>> {key: fn}` 形成 Router。 |
| `a & b` | 乘積 (`asyncio.gather`；廣播 input)。 |
| `a \| b` | Fallback (攔例外、試下一個)。 |
| `a * N` | 重試 (額外 N 次)。 |

Factory：`Pure`、`Sequence`、`Product`、`Fallback`、`Retry`、`Router`、`Iterator`。包裝 helper：`agent(config_path)` 給常駐代理，`factory(config)` 給每次呼叫都 ephemeral 的代理。`effects.Effects()` 提供副作用 log handle。

Runnable 方法：`.map(f)` (輸出後處理)、`.contramap(f)` (輸入前處理)、`.fails_when(pred)` (依 predicate raise)。

---

## MCP surface

內建 MCP meta-tool (在 `mcp_servers` 設定時會暴露)：

- `mcp_list` — 列已連線的 server 及它們的 tool。
- `mcp_call` — 在指定 server 上呼叫 tool。
- `mcp_connect` — 連上 config 裡宣告的 server。
- `mcp_disconnect` — 拆掉連線。

Server tool 會在系統提示裡以 `## Available MCP Tools` 出現。Transport：`stdio` (subprocess) 與 `http`/SSE。

Python 介面：`kohakuterrarium.mcp` 的 `MCPServerConfig`、`MCPClientManager`。

---

## Extension

套件的 `kohaku.yaml` 可以貢獻 `creatures`、`terrariums`、`tools`、`plugins`、`llm_presets`、`python_dependencies`。`kt extension list` 會做 inventory。Python 模組依 `module:class` 參照解析；config 透過 `@pkg/path` 解析。見 [configuration.md — Package manifest](configuration.md#package-manifest-kohakuyaml)。

---

## 延伸閱讀

- 概念：[tool](../concepts/modules/tool.md)、[sub-agent](../concepts/modules/sub-agent.md)、[channel](../concepts/modules/channel.md)、[patterns](../concepts/patterns.md)。
- 指南：[creatures](../guides/creatures.md)、[custom modules](../guides/custom-modules.md)、[plugins](../guides/plugins.md)。
- 參考：[configuration](configuration.md)、[plugin-hooks](plugin-hooks.md)、[python](python.md)、[cli](cli.md)。
