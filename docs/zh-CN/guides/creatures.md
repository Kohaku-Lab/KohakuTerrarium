---
title: 编写 Creature
summary: 提示词设计、工具与子代理选择、LLM 配置选用，以及把Creature发布为可复用包。
tags:
 - guides
 - creature
 - authoring
---

# Creature

给想要编写、自定义或封装独立 agent 的读者。

 **Creature** 是一个自包含的 agent：拥有自己的控制器、工具、子代理、触发器、提示词与 I/O。一只 Creature 可以独立执行（`kt run path/to/creature`）、继承自另一只 Creature，或封装在包中发布。它永远不会知道自己是否身处某个 Terrarium 中。

概念预习：[什么是 agent](../concepts/foundations/what-is-an-agent.md)、[组合 agent](../concepts/foundations/composing-an-agent.md)、[模块索引](../concepts/modules/README.md)。

## 结构

一只 Creature存在于一个目录中：

```
creatures/my-agent/
  config.yaml            # 必填
  prompts/
    system.md            # 由 system_prompt_file 引用
    context.md           # 由 prompt_context_files 引用
  tools/                 # 可选的自定义工具模块
  subagents/             # 可选的自定义子代理设置
  memory/                # 可选的文字 / Markdown 记忆文件
```

查找顺序为：`config.yaml` → `config.yml` → `config.json` → `config.toml`。环境变量插值（`${VAR}` 或 `${VAR:default}`）可在 YAML 任意位置使用。

### 最小设置

```yaml
name: my-agent
controller:
  llm: claude-opus-4.6
system_prompt_file: prompts/system.md
tools:
  - read
  - write
  - bash
```

每个字段都对应到 `AgentConfig` dataclass。任务导向索引请见[设置](configuration.md)；完整字段请见 [配置参考](../reference/configuration.md)。

## 继承

可将既有Creature作为基底重用：

```yaml
name: my-swe
base_config: "@kt-biome/creatures/swe"
controller:
  reasoning_effort: high
tools:
  - name: my_tool          # 新工具，会附加进去
    type: custom
    module: ./tools/my_tool.py
```

规则——所有字段都遵循同一套统一模型：

- **纯量**：子层覆盖父层。
- **字典** （`controller`、`input`、`output`、`memory`、`compact`……）：浅层合并。
- **以识别键为准的列表 ** （`tools`、`subagents`、`plugins`、`mcp_servers`、`triggers`）：依 `name` 做 union。若名称冲突， ** 子层胜出**，并原地取代基底项目。没有 `name` 的项目则直接串接。
- **提示词文件**：`system_prompt_file` 会沿著继承链串接；行内 `system_prompt` 最后附加。
- `base_config` 可解析 `@pkg/...`、`creatures/<name>`（往上寻找专案根目录），或相对路径。

有两个指令可用来退出默认继承：

```yaml
# 1. 完全丢弃某个继承字段，然后从头定义
no_inherit: [tools, plugins]
tools:
  - { name: think, type: builtin }

# 2. 取代整条继承来的提示词链（是
#    no_inherit: [system_prompt, system_prompt_file] 的语法糖）
prompt_mode: replace
system_prompt_file: prompts/brand_new.md
```

### 何时使用 `prompt_mode: replace`

这对 **子代理 ** 与 **Terrarium 中的 Creature** 特别有用：它们可能继承同一个基底 persona，但需要完全不同的语气。

```yaml
# Creature 设置中的子代理项目
subagents:
  - name: niche_responder
    base_config: "@kt-biome/subagents/response"
    prompt_mode: replace
    system_prompt_file: prompts/niche_persona.md
```

```yaml
# Terrarium 中的 Creature，把 OOTB Creature重新用途化为团队专家
creatures:
  - name: reviewer
    base_config: "@kt-biome/creatures/critic"
    prompt_mode: replace
    system_prompt: |
      You are the team's lead reviewer. Speak only to approve or reject, with one-line reasoning.
```

默认值（`prompt_mode: concat`）适合用在：你想扩展基底提示词，而不是取代它，尤其当它代表的是某种通用契约时。

### 覆盖与扩展列表项目

以 `name` 发生冲突时，子层项目会胜出：

```yaml
base_config: "@kt-biome/creatures/general"
tools:
  - { name: bash, type: custom, module: ./tools/safe_bash.py, class: SafeBash }
```

子层的 `bash` 会原地取代基底的 `bash`；其他继承来的工具则会保留。

## 提示词文件

请将 system prompt 放在 Markdown 中。里面只放 **人格与指引** ——工具列表、调用语法与完整工具文件都会自动聚合。

```markdown
<!-- prompts/system.md -->
You are a focused SWE agent. Use tools immediately rather than narrating.
Prefer minimal diffs. Validate before declaring done.
```

模板变量来自 `prompt_context_files`：

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

聚合器会自动附加工具列表、框架提示、环境信息与 `CLAUDE.md`。请不要自行重复这些内容。

## Skill mode：dynamic 与 static

- `skill_mode: dynamic`（默认）— 工具会以单行描述出现在提示词中。控制器会在需要时通过 `info` 框架指令加载完整文件。
- `skill_mode: static` — 所有工具文件都会预先内嵌（system prompt 较大，但 round-trip 较少）。

除非你需要固定、可稽核的提示词，否则建议使用 `dynamic`。

## 工具格式

它控制 LLM 输出工具调用（以及框架指令调用）时所用的语法。这会同时影响 parser 与 system prompt 中的 framework-hints 区块。

以下是 `bash` 调用、`command=ls` 的具体例子：

- `bracket`（默认）— 以 `[/name]` 开始、`[name/]` 结束，参数用 `@@key=value` 行表示：
 ```
 [/bash]
 @@command=ls
 [bash/]
 ```
- `xml` — 标准的带属性标签形式：
 ```
 <bash command="ls"></bash>
 ```
- `native` — 提供者原生 function calling（OpenAI / Anthropic tool use）。LLM 不输出文字区块，而由 API 以结构化方式携带调用。
- dict — 自定义分隔符（见 [配置参考](../reference/configuration.md)）。

三种格式可以互换——选择最适合你模型的即可。`native` 在主流提供者上通常最稳定；`bracket` 则几乎到处都能用，包括本地模型。

## 工具与子代理

```yaml
tools:
  - read                              # shorthand = builtin
  - bash
  - name: my_tool                     # custom / package tool
    type: custom
    module: ./tools/my_tool.py
    class_name: MyTool
  - name: web_search
    options:
      max_results: 5
  # 把通用 trigger 暴露成 setup tool —— LLM 可以在执行期
  # 调用这个工具名称来安装它。框架会以 `CallableTriggerTool`
  # 包装 trigger 类别；简短描述前面会加上「 **Trigger**  — 」
  # 让 LLM 知道这是在安装长期副作用，而不是立即执行一次行为。
  - { name: add_timer, type: trigger }
  - { name: watch_channel, type: trigger }
  - { name: add_schedule, type: trigger }

subagents:
  - worker
  - plan
  - name: my_specialist
    type: custom
    module: ./subagents/specialist.py
    config_name: SPECIALIST_CONFIG
    interactive: true                 # 在父代理多轮之间持续存活
    can_modify: true
```

可安装型 trigger 是逐Creature opt-in 的——没有任何 `type: trigger` 项目的Creature，无法在执行期安装 trigger。每个通用 `BaseTrigger` 子类别都会宣告自己的 `setup_tool_name`（例如 `add_timer`）、`setup_description` 与 `setup_param_schema`。若要自己编写，请见[自定义模块 — Triggers](custom-modules.md)。

完整的工具与子代理目录请见 [reference/builtins 参考](../reference/builtins.md)；编写自定义内容请见[自定义模块指南](custom-modules.md)。

## 触发器

```yaml
triggers:
  - type: timer
    options: { interval: 600 }
    prompt: "Health check: anything pending?"
  - type: channel
    options: { channel: alerts }
  - type: custom
    module: ./triggers/webhook.py
    class_name: WebhookTrigger
```

内置型别：`timer`、`idle`、`webhook`、`channel`、`custom`、`package`。见 [概念 / Trigger](../concepts/modules/trigger.md)。

## 启动触发器

会在Creature启动时触发一次：

```yaml
startup_trigger:
  prompt: "Review the project status and plan today's work."
```

## 终止条件

```yaml
termination:
  max_turns: 20
  max_duration: 300          # 秒
  idle_timeout: 60           # 无事件多久后视为超时（秒）
  keywords: ["DONE", "SHUTDOWN"]
```

只要任一条件达成，agent 就会停止。`keywords` 会对控制器输出做子字符串比对。

## Session key

多只Creature可通过设置 `session_key` 共享同一个 `Session`（scratchpad + channels）：

```yaml
session_key: shared_workspace
```

默认值是Creature的 `name`。在Terrarium 中，每只 Creature都有自己的私有 `Session` 与共享 `Environment`；见 [概念 / 会话与环境](../concepts/modules/session-and-environment.md)。

## 框架指令

控制器可以输出内嵌指令直接与框架沟通（不需工具 round-trip）。这些指令会记录在提示词中的 framework-hints 区块。

框架指令与工具调用共用同一语法家族——也就是你设置的 `tool_format`（bracket、XML、native）是什么，它就用什么。以下为默认 bracket 例子，placeholder 以裸识别字表示：

- `[/info]tool_or_subagent[info/]` — 按需加载完整文件。
- `[/read_job]job_id[read_job/]` — 读取背景作业输出（在 body 中接受 `--lines N` 与 `--offset M`）。
- `[/jobs][jobs/]` — 列出执行中的作业与其 ID。
- `[/wait]job_id[wait/]` — 阻塞目前这一轮，直到背景作业完成。

指令名称与工具名称共享同一个命名空间；读取作业输出的指令之所以叫 `read_job`，就是为了避免与 `read` 文件读取工具冲突。

这些机制让 agent 能读取串流工具输出、查询自己没记住的文件，以及与自己的背景工作同步。

## 用户指令

由 **用户** 在 CLI/TUI 提示字元输入的斜线指令。内置如下：

| 指令 | 别名 | 效果 |
|---|---|---|
| `/help` | `/h`, `/?` | 列出指令 |
| `/status` | `/info` | 模型、消息、工具、作业、压缩状态 |
| `/clear` | | 清除对话 |
| `/model [name]` | `/llm` | 列出或切换 LLM 配置 |
| `/compact` | | 手动压缩 |
| `/regen` | `/regenerate` | 重新执行上一轮 assistant 回应 |
| `/plugin [list\|enable\|disable\|toggle] [name]` | `/plugins` | 管理生命周期插件 |
| `/exit` | `/quit`, `/q` | 优雅退出 |

自定义用户指令可放在 `builtins/user_commands/`，也可封装在包中发布。编写方式请见[自定义模块指南](custom-modules.md)。

## 输入与输出

```yaml
input:
  type: cli                  # 或：tui、whisper、asr、none、custom、package
  prompt: "> "
  history_file: ~/.my_agent_history

output:
  type: stdout               # 或：tts、tui、custom、package
  named_outputs:
    discord:
      type: custom
      module: ./outputs/discord.py
      class_name: DiscordOutput
      options: { webhook_url: "${DISCORD_WEBHOOK}" }
```

`named_outputs` 让工具或子代理能路由到特定输出端（例如 Discord webhook、TTS、文件）。详见 [概念 / Output](../concepts/modules/output.md)。
