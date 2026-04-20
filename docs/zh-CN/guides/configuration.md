---
title: 编写配置文件
summary: Creature 设置的结构、继承、提示词链，以及日常编写会用到的重要字段。
tags:
 - guides
 - config
 - creature
---

# 设置

给想要微调一只现成的Creature、或接一只新的Creature，而不想把参考文件每个字段都读过的人。

Creature 设置用 YAML (也支持 JSON/TOML)。每个顶层 key 对映到 `AgentConfig` 的一个字段；`controller`、`input`、`output` 这类子区块是自己的 dataclass、有自己的字段。这份指南以任务为导向 — 完整的字段清单请看 [配置参考](../reference/configuration.md)。

相关概念：[Creatures 指南](creatures.md)、[组合一个 agent](../concepts/foundations/composing-an-agent.md)。

任何地方都能用环境变量插值：`${VAR}` 或 `${VAR:default}`。

## 怎么换 model？

从 `~/.kohakuterrarium/llm_profiles.yaml` 挑一个 preset (或用 `kt config llm add` 新增)：

```yaml
controller:
  llm: claude-opus-4.6
  reasoning_effort: high
```

或是在命令列只为这次执行覆盖：

```bash
kt run path/to/creature --llm gpt-5.4
```

如果想全部写死在 config 里、不要 profile 档，就用 `model` + `api_key_env` + `base_url`：

```yaml
controller:
  model: gpt-4o
  api_key_env: OPENAI_API_KEY
  base_url: https://api.openai.com/v1
  temperature: 0.3
```

## 怎么继承 OOTB Creature？

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

纯量会覆盖；`controller`/`input`/`output` 是浅层合并；列表会延伸、并依 `name` 去重。如果要取代整个列表而不是延伸：

```yaml
no_inherit: [tools, subagents]
```

## 怎么加工具？

内置工具的简写：

```yaml
tools:
  - bash
  - read
  - web_search
```

带选项的：

```yaml
tools:
  - name: web_search
    options:
      max_results: 10
      region: us-en
```

本地 custom 模块：

```yaml
tools:
  - name: my_tool
    type: custom
    module: ./tools/my_tool.py
    class_name: MyTool
```

来自已安装包的 `kohaku.yaml`：

```yaml
tools:
  - name: kql
    type: package
```

协定请看 [自定义模块指南](custom-modules.md)。

## 怎么加子代理？

```yaml
subagents:
  - plan
  - worker
  - name: my_critic
    type: custom
    module: ./subagents/critic.py
    config_name: CRITIC_CONFIG
    interactive: true       # 跨父回合持续活著
    can_modify: true
```

内置：`worker`、`coordinator`、`explore`、`plan`、`research`、`critic`、`response`、`memory_read`、`memory_write`、`summarize`。

## 怎么加触发器？

```yaml
triggers:
  - type: timer
    options: { interval: 300 }
    prompt: "Check for pending tasks."
  - type: channel
    options: { channel: alerts }
  - type: idle
    options: { timeout: 120 }
    prompt: "If the user seems stuck, ask."
```

内置：`timer`、`idle`、`webhook`、`channel`、`custom`、`package`。触发器触发时 `prompt` 会塞进 `TriggerEvent.prompt_override`。

## 怎么设置压缩？

```yaml
compact:
  enabled: true
  threshold: 0.8
  target: 0.5
  keep_recent_turns: 5
  compact_model: gpt-4o-mini
```

压缩在做什么请看 [会话](sessions.md)。

## 怎么加自定义 input？

```yaml
input:
  type: custom
  module: ./inputs/discord.py
  class_name: DiscordInput
  options:
    token: "${DISCORD_TOKEN}"
    channel_id: 123456
```

内置型别：`cli`、`tui`、`asr`、`whisper`、`none`。协定请看 [自定义模块指南](custom-modules.md)。

## 怎么加 named output sink？

当工具或子代理想把东西导到特定频道 (TTS、Discord、文件) 时很实用：

```yaml
output:
  type: stdout
  named_outputs:
    tts:
      type: tts
      options: { provider: edge, voice: en-US-AriaNeural }
    discord:
      type: custom
      module: ./outputs/discord.py
      class_name: DiscordOutput
      options: { webhook_url: "${DISCORD_WEBHOOK}" }
```

## 怎么用插件挡工具？

一个会挡掉危险指令的 lifecycle 插件：

```yaml
plugins:
  - name: tool_guard
    type: custom
    module: ./plugins/tool_guard.py
    class: ToolGuard
    options:
      deny_patterns: ["rm -rf", "dd if="]
```

插件类别怎么写请看 [插件指南](plugins.md)，参考实作在 [examples/plugins/tool_guard.py](../../examples/plugins/tool_guard.py)。

## 怎么注册 MCP server？

每只 Creature：

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

全域 (`~/.kohakuterrarium/mcp_servers.yaml`) 用同一份 schema。请看 [MCP 指南](mcp.md)。

## 怎么换工具调用格式？

```yaml
tool_format: bracket        # 默认：[/name]@@arg=value\n[name/]
# 或
tool_format: xml            # <name arg="value"></name>
# 或
tool_format: native         # provider 原生的 function calling
```

每种格式的具体样子看 [Creature指南 — 工具格式](creatures.md)；要做完全自定义的分隔符看 [配置参考](../reference/configuration.md)。

## 怎么选 dynamic 或 static skill mode？

```yaml
skill_mode: dynamic   # 默认 — `info` 框架指令会在需要时才载完整文件
# 或
skill_mode: static    # 完整工具文件直接塞进 system prompt
```

## 怎么让Creature 没有用户输入也能持续工作？

```yaml
input:
  type: none
triggers:
  - type: timer
    options: { interval: 60 }
    prompt: "Check for anomalies."
```

`none` input 加任何一种触发器就是标准的 monitor agent 模式。

## 怎么设执行上限？

```yaml
termination:
  max_turns: 15
  max_duration: 600
  idle_timeout: 120
  keywords: ["DONE", "ABORT"]
```

任一条件符合就会停下代理。

## 怎么让多只Creature共用状态 (不通过Terrarium)？

给它们一样的 `session_key`：

```yaml
name: writer
session_key: shared-workspace
---
name: reviewer
session_key: shared-workspace
```

这两只Creature现在会共用 `Scratchpad` 与 `ChannelRegistry`。当多只Creature跑在同一个程序、又不想搭Terrarium时很方便。

## 怎么设置记忆 / embedding？

```yaml
memory:
  embedding:
    provider: model2vec
    model: "@retrieval"
```

详情看 [记忆指南](memory.md)。

## 怎么把Creature固定在特定工作目录？

```bash
kt run path/to/creature --pwd /path/to/project
```

`pwd` 会传进每个工具的 `ToolContext`。

## 疑难排解

- **环境变量没有展开**。 用 `${VAR}` (有大括号)。`$VAR` 会被当字面字符串。
- **子 config “丢失” 了父层的某个工具**。 因为你写了 `no_inherit: [tools]`。移除就会改成延伸。
- **Config 加载成功但工具不见了**。 简写名称会去查内置工具目录 — 拼错会静静 fall through。跑 `kt info path/to/creature` 检查。
- **两个配置互相冲突**。 CLI 覆盖 (`--llm`) > config > `llm_profiles.yaml` 的 `default_model`。

## 延伸阅读

- [配置参考](../reference/configuration.md) — 每个字段、型别、默认值。
- [Creatures 指南](creatures.md) — 目录结构与解剖。
- [插件指南](plugins.md)、[自定义模块指南](custom-modules.md)、[MCP 指南](mcp.md)、[记忆指南](memory.md) — 特定介面怎么接。
