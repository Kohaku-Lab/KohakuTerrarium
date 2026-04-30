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

所有内置子代理都会加载 `default_plugins: ["default-runtime"]`，并使用最小运行时预算：turn 软/硬限制 `40/60`、工具调用软/硬限制 `75/100`，且没有 walltime 预算。

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

 **`cli_nonblocking`** — 与 `cli` 相同的接口，但会在按键之间把控制权交回事件回圈（当输入期间有 trigger 触发时很有用）。

 **`none`** — 不接收输入。供仅使用 trigger 的 agent 使用。

音频/ASR 实现不是内置输入。conversational 示例在 `examples/agent-apps/conversational/custom/` 下提供 opt-in 的 `ASRModule`/Whisper 自定义输入文件；请通过 `type: custom` 加载。

另外两种输入型别会动态解析：

- `tui` — 在 TUI 模式下由 Textual app 挂载。
- `custom` / `package` — 通过 `module` + `class` 字段加载。

---

## 输出

随附的输出模块位于 `src/kohakuterrarium/builtins/outputs/`。

 **`stdout`** — 输出到 stdout。选项：
`prefix`、`suffix`、`stream_suffix`、`flush_on_stream`。

 **`stdout_prefixed`** — 加上每行前缀的 `stdout`，适合帮侧输出打标签。

 **`console_tts`** — 只在 console 上的 TTS shim，会依可配置的 `char_delay` 一个字一个字地印出合成文本。给 demo 与测试用 — 没有音频后端。

 **`dummy_tts`** — 沉默的 TTS，会触发一般的 TTS 生命周期事件但不产生任何输出。用于测试。

其他路由型别：

- `tui` — 渲染到 Textual TUI 的 widget 树。
- `custom` / `package` — 通过 `module` + `class` 加载。

没有 plain 的 `tts` 注册 key。真正的 TTS 后端 (Fish、Edge、OpenAI 等) 都以 `custom`/`package` 输出形式出货，继承 `TTSModule`。

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
| `/regen` | `/regenerate`, `/retry` | 将上一轮 assistant 回应作为 sibling branch 重新执行。 |
| `/edit <message_index> <new content>` | — | 编辑过去的 user message，并从该点作为新 branch 重跑。 |
| `/branch [<turn> <branch_id>\|latest]` | `/br` | 列出或切换 regen/edit alternatives 的 live branch。 |
| `/fork [event_id] [--name name]` | — | 将当前 session 复制成新的 `.kohakutr` 文件，用于探索替代路线。 |
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

| Provider | Backend type | Transport | 说明 |
|---|---|---|---|
| `codex` | `codex` | Codex OAuth (ChatGPT 订阅) | `kt login codex`；通过 `CodexOAuthProvider` 路由。 |
| `openai` | `openai` | OpenAI `/chat/completions` | API-key 验证 (`OPENAI_API_KEY`)。 |
| `openrouter` | `openai` | 对 OpenRouter 使用 OpenAI-compat | API-key 验证 (`OPENROUTER_API_KEY`)；统一的 `reasoning` 参数。 |
| `anthropic` | `anthropic` | Anthropic-compatible Messages API | API-key 验证 (`ANTHROPIC_API_KEY`)。使用官方 `anthropic` SDK。Claude 专属旋钮请走 `extra_body` (`thinking.*`、`output_config.*`)。 |
| `gemini` | `openai` | Google 的 OpenAI-compat endpoint | API-key 验证 (`GEMINI_API_KEY`)。 |
| `mimo` | `openai` | 小米 MiMo | `kt login mimo`。 |

规范 backend type 值是 `openai`、`anthropic` 与 `codex`。旧的 `codex-oauth` backend type 值会在读取时默默迁移 (见 [配置参考](configuration.md#llm-profiles-kohakuterrariumllm_profilesyaml))。

使用者定义的 provider 若暴露 OpenAI-compatible `/chat/completions` API，请用 `backend_type: openai`；若暴露 Anthropic-compatible `/v1/messages` API，请用 `backend_type: anthropic`。

## LLM presets

随附于 `src/kohakuterrarium/llm/presets.py`。可作为 `llm:` 或
`--llm` 的值。

命名惯例 (2026-04 重构后)：

- Direct / native-API 变体为主要名称 (`claude-opus-4.7`、`gemini-3.1-pro`、`mimo-v2-pro`)。
- 走 OpenRouter 的变体用 `-or` 后缀 (`claude-opus-4.7-or`)。
- OpenAI 是例外：`gpt-5.4` 仍绑到 **Codex OAuth** provider；OpenAI API 直连变体用 `-api`，OpenRouter 变体用 `-or`。
- 旧名称 (`claude-opus-4.6-direct`、`or-gpt-5.4`、`gemini-3.1-pro-direct`、`mimo-v2-pro-direct`…) 作为别名保留，既有 config 继续可用。

### OpenAI via Codex OAuth

- `gpt-5.4` (别名：`gpt5`、`gpt54`)
- `gpt-5.3-codex` (`gpt53`)
- `gpt-5.1`
- `gpt-4o-codex` (别名：`gpt4o`、`gpt-4o`)
- `gpt-4o-mini-codex` (别名：`gpt-4o-mini`)

### OpenAI Direct API (`-api` 后缀)

- `gpt-5.4-api` (旧别名：`gpt-5.4-direct`)
- `gpt-5.4-mini-api` (`gpt-5.4-mini-direct`)
- `gpt-5.4-nano-api` (`gpt-5.4-nano-direct`)
- `gpt-5.3-codex-api` (`gpt-5.3-codex-direct`)
- `gpt-5.1-api` (`gpt-5.1-direct`)
- `gpt-4o-api` (`gpt-4o-direct`)
- `gpt-4o-mini-api` (`gpt-4o-mini-direct`)

### OpenAI via OpenRouter (`-or` 后缀)

- `gpt-5.4-or` (旧别名：`or-gpt-5.4`)
- `gpt-5.4-mini-or` (`or-gpt-5.4-mini`)
- `gpt-5.4-nano-or` (`or-gpt-5.4-nano`)
- `gpt-5.3-codex-or` (`or-gpt-5.3-codex`)
- `gpt-5.1-or` (`or-gpt-5.1`)
- `gpt-4o-or` (`or-gpt-4o`)
- `gpt-4o-mini-or` (`or-gpt-4o-mini`)

### Anthropic Claude Direct (无后缀 — 主要)

走原生 Anthropic-compatible Messages API。Effort 通过 `extra_body.output_config.effort`。

- `claude-opus-4.7` (别名：`claude-opus`、`opus`)
- `claude-opus-4.6` (旧别名：`claude-opus-4.6-direct`)
- `claude-sonnet-4.6` (别名：`claude`、`claude-sonnet`、`sonnet`；旧：`claude-sonnet-4.6-direct`)
- `claude-haiku-4.5` (别名：`claude-haiku`、`haiku`；旧：`claude-haiku-4.5-direct`)

### Anthropic Claude via OpenRouter (`-or` 后缀)

- `claude-opus-4.7-or`
- `claude-opus-4.6-or`
- `claude-sonnet-4.6-or`
- `claude-sonnet-4.5-or`
- `claude-haiku-4.5-or`
- `claude-sonnet-4-or` (旧别名：`claude-sonnet-4`)
- `claude-opus-4-or` (旧别名：`claude-opus-4`)

### Google Gemini Direct (OpenAI-compat)

- `gemini-3.1-pro` (别名：`gemini`、`gemini-pro`；旧：`gemini-3.1-pro-direct`)
- `gemini-3-flash` (`gemini-flash`；旧：`gemini-3-flash-direct`)
- `gemini-3.1-flash-lite` (`gemini-lite`；旧：`gemini-3.1-flash-lite-direct`)

### Google Gemini via OpenRouter (`-or` 后缀)

- `gemini-3.1-pro-or`
- `gemini-3-flash-or`
- `gemini-3.1-flash-lite-or`
- `nano-banana` (图像生成模型，OpenRouter)

### Google Gemma (OpenRouter)

- `gemma-4-31b` (别名：`gemma`、`gemma-4`)
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

### 小米 MiMo Direct (无后缀 — 主要)

- `mimo-v2-pro` (`mimo`；旧别名：`mimo-v2-pro-direct`)
- `mimo-v2-flash` (旧别名：`mimo-v2-flash-direct`)

### 小米 MiMo via OpenRouter (`-or` 后缀)

- `mimo-v2-pro-or`
- `mimo-v2-flash-or`

### GLM (Z.ai，OpenRouter)

- `glm-5`
- `glm-5-turbo` (别名：`glm`)

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

- `mistral-large-3` (别名：`mistral`、`mistral-large`)
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

每个 preset 的 token 窗口 (`max_context` / `max_output`) 各自设定 — 确切值请见 `src/kohakuterrarium/llm/presets.py`，或跑 `kt config llm show <name>` 查看。`controller.max_tokens` 会覆盖 `max_output`；要调整 compaction 窗口，请设 `compact.max_tokens`。

内置 preset 合并时也会吃进已安装套件贡献的 `llm_presets`；见 [configuration.md — 套件 manifest](configuration.md#套件-manifest-kohakuyaml)。

## Variation groups

Variation group 让一个 preset 可以暴露多个旋钮而不必重复条目。在 `llm:` / `--llm` 里用 `preset@group=option` 简写选择，或通过 `controller.variation_selections`；见 [配置参考 — Variation 选择器](configuration.md#variation-选择器)。

未列出的 preset 没有 variation group — 它们的默认值固定。

### OpenAI — Codex OAuth

| Preset | Group | Options |
|---|---|---|
| `gpt-5.4` | `reasoning` | `none`、`low`、`medium`、`high`、`xhigh` |
| `gpt-5.4` | `speed` | `normal`、`fast` (映射到 `service_tier: priority`) |
| `gpt-5.3-codex` | `reasoning` | `none`、`low`、`medium`、`high`、`xhigh` |
| `gpt-5.1` | `reasoning` | `none`、`low`、`medium`、`high`、`xhigh` |

### OpenAI — Direct API (`-api` 后缀)

Patch `extra_body.reasoning.effort`。

| Preset | Group | Options |
|---|---|---|
| `gpt-5.4-api`、`gpt-5.4-mini-api`、`gpt-5.4-nano-api`、`gpt-5.3-codex-api`、`gpt-5.1-api` | `reasoning` | `none`、`low`、`medium`、`high`、`xhigh` |

### OpenAI — OpenRouter (`-or` 后缀)

通过 OpenRouter 的统一参数 patch `extra_body.reasoning.effort`。

| Preset | Group | Options |
|---|---|---|
| `gpt-5.4-or`、`gpt-5.4-mini-or`、`gpt-5.4-nano-or`、`gpt-5.3-codex-or`、`gpt-5.1-or` | `reasoning` | `minimal`、`low`、`medium`、`high`、`xhigh` |

### Anthropic — Direct

通过 compat 层 patch `extra_body.output_config.effort`。

| Preset | Group | Options |
|---|---|---|
| `claude-opus-4.7` | `reasoning` | `low`、`medium`、`high`、`xhigh`、`max` |
| `claude-opus-4.6`、`claude-sonnet-4.6` | `reasoning` | `low`、`medium`、`high`、`max` |

Haiku 4.5 使用较旧的 extended-thinking (`budget_tokens`)，没有 variation group。

### Anthropic — OpenRouter (`-or` 后缀)

Patch `extra_body.reasoning.effort`。`xhigh` 只有 Opus 4.7 会认。

| Preset | Group | Options |
|---|---|---|
| `claude-opus-4.7-or` | `reasoning` | `minimal`、`low`、`medium`、`high`、`xhigh` |
| `claude-opus-4.6-or`、`claude-sonnet-4.6-or`、`claude-sonnet-4.5-or`、`claude-opus-4-or`、`claude-sonnet-4-or` | `reasoning` | `minimal`、`low`、`medium`、`high` |
| `claude-haiku-4.5-or` | `reasoning` | `off`、`low`、`medium`、`high` |

### Google Gemini — Direct

Patch `extra_body.google.thinking_config.thinking_level`。

| Preset | Group | Options |
|---|---|---|
| `gemini-3.1-pro` | `thinking` | `low`、`medium`、`high` |
| `gemini-3-flash`、`gemini-3.1-flash-lite` | `thinking` | `minimal`、`low`、`medium`、`high` |

### Google Gemini — OpenRouter

| Preset | Group | Options |
|---|---|---|
| `gemini-3.1-pro-or`、`gemini-3-flash-or`、`gemini-3.1-flash-lite-or` | `reasoning` | `minimal`、`low`、`medium`、`high` |

### Gemma / Qwen / Kimi / MiMo / GLM — OpenRouter

除另有说明外，共享同一套 OpenRouter 统一 reasoning group。

| Preset | Group | Options |
|---|---|---|
| `gemma-4-31b`、`gemma-4-26b` | `reasoning` | `minimal`、`low`、`medium`、`high` |
| `qwen3.5-plus`、`qwen3.5-flash`、`qwen3.5-397b`、`qwen3.5-27b`、`qwen3-coder`、`qwen3-coder-plus` | `reasoning` | `minimal`、`low`、`medium`、`high` |
| `kimi-k2.5` | `reasoning` | `minimal`、`low`、`medium`、`high` |
| `mimo-v2-pro`、`mimo-v2-flash`、`mimo-v2-pro-or`、`mimo-v2-flash-or` | `reasoning` | `minimal`、`low`、`medium`、`high` |
| `glm-5`、`glm-5-turbo` | `reasoning` | `minimal`、`low`、`medium`、`high` |

`kimi-k2-thinking` 总是开着 thinking — 没有 variation group。

### Mistral — OpenRouter

| Preset | Group | Options |
|---|---|---|
| `mistral-small-4` | `reasoning` | `none`、`high` |

其他 Mistral preset (`mistral-large-3`、`mistral-medium-*`、`mistral-small-3.2`、`codestral`、`devstral-*`、`pixtral-large`、`ministral-*`) 不是 reasoning 模型。`magistral-medium` 与 `magistral-small` 是 reasoning 总是开着的 — 没有 variation group。

### Grok / MiniMax — OpenRouter

Grok 4.x (`grok-4`、`grok-4.20`、`grok-4.20-multi`、`grok-4-fast`、`grok-4.1-fast`、`grok-code-fast`) 的 reasoning 是强制、不可配置的。`grok-3` / `grok-3-mini` 是旧的非 reasoning 模型。`minimax-m2.7` / `minimax-m2.5` 也是强制 reasoning。这些都不暴露 variation group。

---

## Prompt 插件

随附的 prompt 插件 (由 `prompt/aggregator.py` 加载)。依优先级排序 (数字越小越早)：

| Priority | Name | Emits |
|---|---|---|
| 50 | `ToolListPlugin` | 工具名 + 一行描述。 |
| 45 | `FrameworkHintsPlugin` | 框架指令范例 (`info`、`read_job`、`jobs`、`wait`) 与工具调用格式范例。 |
| 40 | `EnvInfoPlugin` | `cwd`、platform、date/time。 |
| 30 | `ProjectInstructionsPlugin` | 加载 `CLAUDE.md` 与 `.claude/rules.md`。 |

自定义 prompt 插件要继承 `BasePlugin` 并通过 creature config 里的 `plugins` 字段注册。生命周期与回调 hook 请见 [plugin-hooks.md](plugin-hooks.md)。

---

## Compose 代数

运算子优先级：`* > | > & > >>`。

| 运算子 | 含义 |
|---|---|
| `a >> b` | Sequence (自动 flatten)。`>> {key: fn}` 会形成 Router。 |
| `a & b` | Product (`asyncio.gather`；广播输入)。 |
| `a \| b` | Fallback (抓到例外就换下一个)。 |
| `a * N` | Retry (多 N 次尝试)。 |

Factory：`Pure`、`Sequence`、`Product`、`Fallback`、`Retry`、`Router`、`Iterator`。包装辅助：`agent(config_path)` 建立持久 agent、`factory(config)` 建立每次调用 ephemeral 的 agent。`effects.Effects()` 提供 side-effect 日志 handle。

Runnable 方法：`.map(f)` (后处理输出)、`.contramap(f)` (前处理输入)、`.fails_when(pred)` (在 predicate 为真时 raise)。

---

## MCP 接口

内置的 MCP 元工具 (当有配置 `mcp_servers` 时暴露)：

- `mcp_list` — 列出已连接的 server 与它们的工具。
- `mcp_call` — 调用某个 server 上的某个工具。
- `mcp_connect` — 连线到 config 里宣告的某个 server。
- `mcp_disconnect` — 切断连线。

Server 工具会在 system prompt 里以 `## Available MCP Tools` 章节呈现。Transport：`stdio` (subprocess) 与 `http`/SSE。

Python 接口：`kohakuterrarium.mcp` 里的 `MCPServerConfig`、`MCPClientManager`。

---

## Extension

套件的 `kohaku.yaml` 可以贡献 `creatures`、`terrariums`、`tools`、`plugins`、`llm_presets` 与 `python_dependencies`。`kt extension list` 会把它们列出来。Python 模组以 `module:class` 参照解析；config 以 `@pkg/path` 解析。见 [configuration.md — 套件 manifest](configuration.md#套件-manifest-kohakuyaml)。

---

## 延伸阅读

- 概念：[工具概念](../concepts/modules/tool.md)、[子代理概念](../concepts/modules/sub-agent.md)、[channel 概念](../concepts/modules/channel.md)、[模式概念](../concepts/patterns.md)。
- 指南：[Creature 指南](../guides/creatures.md)、[自定义模块指南](../guides/custom-modules.md)、[插件指南](../guides/plugins.md)。
- 参考：[配置参考](configuration.md)、[plugin-hooks](plugin-hooks.md)、[Python API 参考](python.md)、[CLI 参考](cli.md)。
