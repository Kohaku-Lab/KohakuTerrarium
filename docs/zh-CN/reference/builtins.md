---
title: 内置模块
summary: 随附的工具、子代理、trigger、输入与输出——参数形式、行为与默认值。
tags:
  - reference
  - builtins
---

# 内置模块

KohakuTerrarium 随附的所有内置工具、子代理、输入、输出、用户命令、框架命令、LLM provider 与 LLM preset，都整理在这里。

如果你想了解工具和子代理各自的形态，请阅读 [工具概念](../concepts/modules/tool.md) 和 [子代理概念](../concepts/modules/sub-agent.md)。
如果你需要任务导向的说明，请参见 [Creature 指南](../guides/creatures.md) 和 [自定义模块指南](../guides/custom-modules.md)。

## 工具

内置工具类别位于
`src/kohakuterrarium/builtins/tools/`。在 creature 配置中的 `tools:`
下面，使用裸名称即可注册。

### Shell 与脚本

 **`bash`** — 执行 shell 命令。会在 `bash`、`zsh`、`sh`、`fish`、`pwsh`
中选择第一个可用项。遵守 `KT_SHELL_PATH`。会捕获 stdout 与 stderr，并在达到上限时截断。直接执行。

- 参数：`command`（str）、`working_dir`（str，可选）、
  `timeout`（float，可选）。

 **`python`** — 执行 Python 子进程。遵守 `working_dir` 与
`timeout`。直接执行。

- 参数：`code`（str）、`working_dir`、`timeout`。

### 文件操作

 **`read`** — 读取文本、图片或 PDF 内容。会记录每个文件的读取状态。图片会以 `base64` data URL 返回。PDF 支持需要
`pymupdf`。直接执行。

- 参数：`path`（str）、`offset`（int，可选）、`limit`（int，可选）。

 **`write`** — 创建或覆盖文件。会创建父目录。除非先读取过文件（或指定 `new`），否则会阻止覆盖。直接执行。

- 参数：`path`、`content`、`new`（bool，可选）。

 **`edit`** — 自动检测 unified diff（`@@`）或搜索／替换形式。不接受二进制文件。直接执行。

- 参数：`path`、`old_text`/`new_text` 或 `diff`、`replace_all`（bool）。

 **`multi_edit`** — 对单一文件依序应用一串编辑。以文件为单位保持原子性。模式有：`strict`（每个编辑都必须成功应用）、`best_effort`（跳过失败项目）、默认（部分应用并附报告）。直接执行。

- 参数：`path`、`edits: list[{old, new}]`、`mode`。

 **`glob`** — 依修改时间排序的 glob。遵守 `.gitignore`。会提前终止。直接执行。

- 参数：`pattern`、`root`（可选）、`limit`（可选）。

 **`grep`** — 跨文件进行正则表达式搜索。支持 `ignore_case`。会跳过二进制文件。直接执行。

- 参数：`pattern`、`path`（可选）、`ignore_case`（bool）、
  `max_matches`。

 **`tree`** — 目录列表，并为 Markdown 文件附上 YAML frontmatter 摘要。直接执行。

- 参数：`path`、`depth`。

### 结构化数据

 **`json_read`** — 以 dot-path 读取 JSON 文件。直接执行。

- 参数：`path`、`query`（dot-path）。

 **`json_write`** — 在 dot-path 指派值。必要时会创建嵌套对象。直接执行。

- 参数：`path`、`query`、`value`。

### Web

 **`web_fetch`** — 将 URL 抓取为 Markdown。依序尝试 `crawl4ai` →
`trafilatura` → Jina proxy → `httpx + html2text`。上限 100k 字符，超时 30 秒。直接执行。

- 参数：`url`。

 **`web_search`** — 使用 DuckDuckGo 搜索，返回 Markdown 格式结果。直接执行。

- 参数：`query`、`max_results`（int）、`region`（str）。

### 互动与记忆

 **`ask_user`** — 通过 stdin 向用户提问（仅限 CLI 或 TUI）。
有状态。

- 参数：`question`。

 **`think`** — 不做任何事；只是把推理保留为工具事件，写进事件日志。直接执行。

- 参数：`thought`。

 **`scratchpad`** — 以 session 为范围的 KV 存储。由同一个 session 中的各 agent 共享。

- 参数：`action`（`get` | `set` | `delete` | `list`）、`key`、`value`。

 **`search_memory`** — 对 session 已索引事件进行 FTS／semantic／auto 搜索。可依 agent 过滤。

- 参数：`query`、`mode`（`auto`/`fts`/`semantic`/`hybrid`）、`k`、
  `agent`。

### 通信

 **`send_message`** — 向某个 channel 发送消息。会先解析 creature 本地 channel，再解析环境中的共享 channel。直接执行。

- 参数：`channel`、`content`、`sender`（可选）。

### 内省

 **`info`** — 按需加载任一工具或子代理的文件。会委派到
`src/kohakuterrarium/builtin_skills/` 下面的 skill manifest，以及各 agent 的覆盖配置。直接执行。

- 参数：`target`（工具或子代理名称）。

 **`stop_task`** — 依 id 取消正在执行的后台任务或 trigger。直接执行。

- 参数：`job_id`（任一工具调用返回的 job id；或 `add_timer`/`watch_channel`/`add_schedule` 返回的 trigger ID）。

### 可安装的 trigger（以 `type: trigger` 形式暴露为工具）

每个通用 trigger 类别都会通过
`modules/trigger/callable.py:CallableTriggerTool` 包装成各自的工具。creature 可以在 `tools:`
下面列出 trigger 的 `setup_tool_name`，并指定
`type: trigger` 来选择启用。工具描述会以前缀
` **Trigger** — ` 开头，让 LLM 知道调用它会安装一个长期存在的副作用。这三个工具都会立即返回已安装的 trigger ID；trigger 本身则在后台执行。

 **`add_timer`**（包装 `TimerTrigger`）— 安装周期性计时器。

- 参数：`interval`（秒，必填）、`prompt`（必填）、`immediate`（bool，默认 false）。

 **`watch_channel`**（包装 `ChannelTrigger`）— 监听具名 channel。

- 参数：`channel_name`（必填）、`prompt`（可选，支持 `{content}`）、`filter_sender`（可选）。
- agent 自己的名称会自动设置为 `ignore_sender`，以避免自我触发。

 **`add_schedule`**（包装 `SchedulerTrigger`）— 与时钟对齐的调度。

- 参数：`prompt`（必填）；`every_minutes`、`daily_at`（HH:MM）、`hourly_at`（0-59）三者必须且只能择一。

### Terrarium（仅 root 可用）

 **`terrarium_create`** — 启动新的 terrarium 实例。仅 root 可用。

 **`terrarium_send`** — 发送消息到 root 所属 terrarium 中的 channel。

 **`creature_start`** — 在运行期间热插拔启动 creature。

 **`creature_stop`** — 在运行期间停止 creature。

---

## 子代理

随附的子代理配置位于
`src/kohakuterrarium/builtins/subagents/`。在 creature 配置中的 `subagents:`
下面，以名称引用即可。

| 名称 | 工具 | 用途 |
|---|---|---|
| `worker` | `read`, `write`, `bash`, `glob`, `grep`, `edit`, `multi_edit` | 修 bug、重构、执行验证。 |
| `coordinator` | `send_message`, `scratchpad` | 拆解 → 分派 → 汇整。 |
| `explore` | `glob`, `grep`, `read`, `tree`, `bash` | 只读探索。 |
| `plan` | `explore` 的工具 + `think` | 只读规划。 |
| `research` | `web_search`, `web_fetch`, `read`, `write`, `think`, `scratchpad` | 对外研究。 |
| `critic` | `read`, `glob`, `grep`, `tree`, `bash` | 代码审查。 |
| `response` | `read` | 面向用户的文案产生器。通常设置为 `output_to: external`。 |
| `memory_read` | 在 memory 目录上使用 `tree`、`read`、`grep` | 从 agent 记忆中回想内容。 |
| `memory_write` | `tree`, `read`, `write` | 将发现持久化到记忆中。 |
| `summarize` | （无工具） | 为交接或重置压缩对话。 |

---

## 输入

随附的输入模块位于 `src/kohakuterrarium/builtins/inputs/`。

 **`cli`** — Stdin 提示。选项：`prompt`、`exit_commands`。

 **`none`** — 不接收输入。供仅使用 trigger 的 agent 使用。

 **`whisper`** — 麦克风 + Silero VAD + `openai-whisper`。选项包含
`model`、`language`、VAD 阈值。需要 FFmpeg。

 **`asr`** — 自定义语音识别的抽象基底。

另外两种输入型别会动态解析：

- `tui` — 在 TUI 模式下由 Textual app 挂载。
- `custom` / `package` — 通过 `module` + `class_name` 字段加载。

---

## 输出

随附的输出模块位于 `src/kohakuterrarium/builtins/outputs/`。

 **`stdout`** — 输出到 stdout。选项：
`prefix`、`suffix`、`stream_suffix`、`flush_on_stream`。

 **`tts`** — Fish / Edge / OpenAI TTS（自动检测）。支持流式与硬中断。

其他路由型别：

- `tui` — 渲染到 Textual TUI 的 widget 树。
- `custom` / `package` — 通过 module + class 加载。

---

## 用户命令

可在输入模块内使用的 slash 命令。位于
`src/kohakuterrarium/builtins/user_commands/`。

| 命令 | 别名 | 用途 |
|---|---|---|
| `/help` | `/h`, `/?` | 列出命令。 |
| `/status` | `/info` | 模型、消息数、工具、jobs、compact 状态。 |
| `/clear` | | 清除对话（session log 仍会保留历史）。 |
| `/model [name]` | `/llm` | 显示目前模型或切换 profile。 |
| `/compact` | | 手动压缩上下文。 |
| `/regen` | `/regenerate` | 重新执行上一轮 assistant 回应。 |
| `/plugin [list\|enable\|disable\|toggle] [name]` | `/plugins` | 查看或切换 plugin。 |
| `/exit` | `/quit`, `/q` | 优雅退出。在 web 上可能需要 force 旗标。 |

---

## 框架命令

LLM 可输出的内嵌指令，可替换工具调用。它们会直接与框架沟通（不经过工具往返）。定义于
`src/kohakuterrarium/commands/`。

框架命令使用与工具调用 **同一语法家族** ——它们遵循 creature 配置的 `tool_format`（bracket / XML / native）。默认是带有裸识别子 placeholder 的 bracket 形式：

- `[/info]tool_or_subagent[info/]` — 按需加载某个工具或子代理的完整文件。
- `[/read_job]job_id[read_job/]` — 读取背景 job 的输出。内文支持 `--lines N` 与 `--offset M`。
- `[/jobs][jobs/]` — 列出仍在执行中的 jobs 与其 ID。
- `[/wait]job_id[wait/]` — 阻塞目前这一轮，直到背景 job 完成。

命令名称与工具名称共享命名空间；为了避免与读档工具 `read` 冲突，读取 job 输出的命令命名为 `read_job`。定义于 `src/kohakuterrarium/commands/`。

---

## LLM providers

内置 provider 类型（后端）：

| Provider | Transport | 说明 |
|---|---|---|
| `codex` | 通过 Codex OAuth 使用 OpenAI chat API | ChatGPT 订阅验证；`kt login codex`。 |
| `openai` | OpenAI chat API | API key 验证。 |
| `openrouter` | 兼容 OpenAI | API key 验证；可路由到多种模型。 |
| `anthropic` | 原生 Anthropic messages API | 专用 client。 |
| `gemini` | Google 上的 OpenAI 兼容端点 | API key 验证。 |
| `mimo` | 小米 MiMo 原生 | `kt login mimo`。 |

配置中还会引用其他社群 provider：
`together`、`mistral`、`deepseek`、`vllm`、`generic`。正规清单请参考
`kohakuterrarium.llm.presets`。

## LLM presets

随附于 `src/kohakuterrarium/llm/presets.py`。可作为 `llm:` 或
`--llm` 的值。括号中列出别名。

### 通过 Codex OAuth 使用 OpenAI

- `gpt-5.4`（别名：`gpt5`、`gpt54`）
- `gpt-5.3-codex`（`gpt53`）
- `gpt-5.1`
- `gpt-4o`（`gpt4o`）
- `gpt-4o-mini`

### OpenAI 直连

- `gpt-5.4-direct`
- `gpt-5.4-mini-direct`
- `gpt-5.4-nano-direct`
- `gpt-5.3-codex-direct`
- `gpt-5.1-direct`
- `gpt-4o-direct`
- `gpt-4o-mini-direct`

### 通过 OpenRouter 使用 OpenAI

- `or-gpt-5.4`
- `or-gpt-5.4-mini`
- `or-gpt-5.4-nano`
- `or-gpt-5.3-codex`
- `or-gpt-5.1`
- `or-gpt-4o`
- `or-gpt-4o-mini`

### 通过 OpenRouter 使用 Anthropic Claude

- `claude-opus-4.6`（别名：`claude-opus`、`opus`）
- `claude-sonnet-4.6`（别名：`claude`、`claude-sonnet`、`sonnet`）
- `claude-sonnet-4.5`
- `claude-haiku-4.5`（别名：`claude-haiku`、`haiku`）
- `claude-sonnet-4`（旧版）
- `claude-opus-4`（旧版）

### Anthropic Claude 直连

- `claude-opus-4.6-direct`
- `claude-sonnet-4.6-direct`
- `claude-haiku-4.5-direct`

### Google Gemini

通过 OpenRouter：

- `gemini-3.1-pro`（别名：`gemini`、`gemini-pro`）
- `gemini-3-flash`（`gemini-flash`）
- `gemini-3.1-flash-lite`（`gemini-lite`）
- `nano-banana`

直连（OpenAI 兼容端点）：

- `gemini-3.1-pro-direct`
- `gemini-3-flash-direct`
- `gemini-3.1-flash-lite-direct`

### Google Gemma（OpenRouter）

- `gemma-4-31b`（别名：`gemma`、`gemma-4`）
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

通过 OpenRouter：

- `mimo-v2-pro`（`mimo`）
- `mimo-v2-flash`

直连：

- `mimo-v2-pro-direct`
- `mimo-v2-flash-direct`

### GLM（Z.ai，通过 OpenRouter）

- `glm-5`（`glm`，通过默认别名）
- `glm-5-turbo`（`glm`）

### xAI Grok（OpenRouter）

- `grok-4`（`grok`）
- `grok-4.20`
- `grok-4.20-multi`
- `grok-4-fast`（`grok-fast`）
