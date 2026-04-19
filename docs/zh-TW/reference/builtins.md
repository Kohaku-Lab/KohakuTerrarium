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

**`none`** — 不接收輸入。供僅使用 trigger 的 agent 使用。

**`whisper`** — 麥克風 + Silero VAD + `openai-whisper`。選項包含
`model`、`language`、VAD 閾值。需要 FFmpeg。

**`asr`** — 自訂語音辨識的抽象基底。

另外兩種輸入型別會動態解析：

- `tui` — 在 TUI 模式下由 Textual app 掛載。
- `custom` / `package` — 透過 `module` + `class_name` 欄位載入。

---

## 輸出

隨附的輸出模組位於 `src/kohakuterrarium/builtins/outputs/`。

**`stdout`** — 輸出到 stdout。選項：
`prefix`、`suffix`、`stream_suffix`、`flush_on_stream`。

**`tts`** — Fish / Edge / OpenAI TTS（自動偵測）。支援串流與硬中斷。

其他路由型別：

- `tui` — 渲染到 Textual TUI 的 widget 樹。
- `custom` / `package` — 透過 module + class 載入。

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
| `/regen` | `/regenerate` | 重新執行上一輪 assistant 回應。 |
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

內建 provider 類型（後端）：

| Provider | Transport | 說明 |
|---|---|---|
| `codex` | 透過 Codex OAuth 使用 OpenAI chat API | ChatGPT 訂閱驗證；`kt login codex`。 |
| `openai` | OpenAI chat API | API key 驗證。 |
| `openrouter` | 相容 OpenAI | API key 驗證；可路由到多種模型。 |
| `anthropic` | 原生 Anthropic messages API | 專用 client。 |
| `gemini` | Google 上的 OpenAI 相容端點 | API key 驗證。 |
| `mimo` | 小米 MiMo 原生 | `kt login mimo`。 |

設定檔中還會引用其他社群 provider：
`together`、`mistral`、`deepseek`、`vllm`、`generic`。正規清單請參考
`kohakuterrarium.llm.presets`。

## LLM presets

隨附於 `src/kohakuterrarium/llm/presets.py`。可作為 `llm:` 或
`--llm` 的值。括號中列出別名。

### 透過 Codex OAuth 使用 OpenAI

- `gpt-5.4`（別名：`gpt5`、`gpt54`）
- `gpt-5.3-codex`（`gpt53`）
- `gpt-5.1`
- `gpt-4o`（`gpt4o`）
- `gpt-4o-mini`

### OpenAI 直連

- `gpt-5.4-direct`
- `gpt-5.4-mini-direct`
- `gpt-5.4-nano-direct`
- `gpt-5.3-codex-direct`
- `gpt-5.1-direct`
- `gpt-4o-direct`
- `gpt-4o-mini-direct`

### 透過 OpenRouter 使用 OpenAI

- `or-gpt-5.4`
- `or-gpt-5.4-mini`
- `or-gpt-5.4-nano`
- `or-gpt-5.3-codex`
- `or-gpt-5.1`
- `or-gpt-4o`
- `or-gpt-4o-mini`

### 透過 OpenRouter 使用 Anthropic Claude

- `claude-opus-4.6`（別名：`claude-opus`、`opus`）
- `claude-sonnet-4.6`（別名：`claude`、`claude-sonnet`、`sonnet`）
- `claude-sonnet-4.5`
- `claude-haiku-4.5`（別名：`claude-haiku`、`haiku`）
- `claude-sonnet-4`（舊版）
- `claude-opus-4`（舊版）

### Anthropic Claude 直連

- `claude-opus-4.6-direct`
- `claude-sonnet-4.6-direct`
- `claude-haiku-4.5-direct`

### Google Gemini

透過 OpenRouter：

- `gemini-3.1-pro`（別名：`gemini`、`gemini-pro`）
- `gemini-3-flash`（`gemini-flash`）
- `gemini-3.1-flash-lite`（`gemini-lite`）
- `nano-banana`

直連（OpenAI 相容端點）：

- `gemini-3.1-pro-direct`
- `gemini-3-flash-direct`
- `gemini-3.1-flash-lite-direct`

### Google Gemma（OpenRouter）

- `gemma-4-31b`（別名：`gemma`、`gemma-4`）
- `gemma-4-26b`

### Qwen（OpenRouter）

- `qwen3.5-plus`（`qwen`）
- `qwen3.5-flash`
- `qwen3.5-397b`
- `qwen3.5-27b`
- `qwen3-coder`（`qwen-coder`）
- `qwen3-coder-plus`

### Moonshot Kimi（OpenRouter）

- `kimi-k2.5`（`kimi`）
- `kimi-k2-thinking`

### MiniMax（OpenRouter）

- `minimax-m2.7`（`minimax`）
- `minimax-m2.5`

### 小米 MiMo

透過 OpenRouter：

- `mimo-v2-pro`（`mimo`）
- `mimo-v2-flash`

直連：

- `mimo-v2-pro-direct`
- `mimo-v2-flash-direct`

### GLM（Z.ai，透過 OpenRouter）

- `glm-5`（`glm`，透過預設別名）
- `glm-5-turbo`（`glm`）

### xAI Grok（OpenRouter）

- `grok-4`（`grok`）
- `grok-4.20`
- `grok-4.20-multi`
- `grok-4-fast`（`grok-fast`）
