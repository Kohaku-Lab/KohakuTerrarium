---
title: 配置
summary: Creature、Terrarium、LLM profile、MCP server、压缩、插件、输出接线的每一个字段。
tags:
  - reference
  - config
---

# 配置

Creature、Terrarium、LLM profile、MCP server、套件 manifest 的所有字段。文件格式：YAML (建议)、JSON、TOML。所有文件都支持加载时的 `${VAR}` / `${VAR:default}` 环境变数插值。

关于 Creature 和 Terrarium 如何组合的心智模型，请参见 [边界概念](../concepts/boundaries.md)。实际操作示例请参见 [配置指南](../guides/configuration.md) 和 [Creature 指南](../guides/creatures.md)。

## 路径解析

Config 中引用其他文件或套件的字段时，解析顺序如下：

1. `@<pkg>/<path-inside-pkg>` → `~/.kohakuterrarium/packages/<pkg>/<path-inside-pkg>` (遇到 `<pkg>.link` 会跟着走，给 editable 安装用)。
2. `creatures/<name>` 或类似的 project-relative 形式 → 从当前代理目录往上走到专案根。
3. 其他情况相对于代理目录 (继承情境下则 fallback 到 base config 的目录)。

---

## Creature配置 (`config.yaml`)

由 `kohakuterrarium.core.config.load_agent_config` 加载。文件查找顺序：`config.yaml` → `config.yml` → `config.json` → `config.toml`。

### 顶层字段

| 字段 | 型别 | 默认 | 必要 | 说明 |
|---|---|---|---|---|
| `name` | str | — | 是 | Creature名称。没设 `session_key` 时就拿来当默认 session key。 |
| `version` | str | `"1.0"` | 否 | 资讯用。 |
| `base_config` | str | `null` | 否 | 要继承的 parent config (`@package/path`、`creatures/<name>`、或相对路径)。 |
| `controller` | dict | `{}` | 否 | LLM/控制器区块。见 [Controller](#controller-区块)。 |
| `system_prompt` | str | `"You are a helpful assistant."` | 否 | 行内 system prompt。 |
| `system_prompt_file` | str | `null` | 否 | Markdown prompt 档路径，相对于代理目录。会沿继承链串接。 |
| `prompt_context_files` | dict[str,str] | `{}` | 否 | Jinja 变数 → 文件路径；prompt 渲染时读进来插入。 |
| `skill_mode` | str | `"dynamic"` | 否 | `dynamic` (需要时通过 `info` 框架指令载) 或 `static` (完整文件一次塞进去)。 |
| `include_tools_in_prompt` | bool | `true` | 否 | 是否纳入自动生成的工具清单。 |
| `include_hints_in_prompt` | bool | `true` | 否 | 是否纳入框架提示 (工具调用语法、`info` / `read_job` / `jobs` / `wait` 指令范例)。 |
| `max_messages` | int | `0` | 否 | 对话上限。`0` = 无上限。 |
| `ephemeral` | bool | `false` | 否 | 每回合后清空对话 (group-chat 模式)。 |
| `session_key` | str | `null` | 否 | 覆盖默认 session key (原本是 `name`)。 |
| `input` | dict | `{}` | 否 | Input 模组配置。见 [Input](#input)。 |
| `output` | dict | `{}` | 否 | Output 模组配置。见 [Output](#output)。 |
| `tools` | list | `[]` | 否 | 工具条目。见 [工具](#工具)。 |
| `subagents` | list | `[]` | 否 | 子代理条目。见 [子代理](#子代理)。 |
| `triggers` | list | `[]` | 否 | trigger条目。见 [trigger](#trigger)。 |
| `compact` | dict | `null` | 否 | 压缩配置。见 [压缩](#压缩)。 |
| `startup_trigger` | dict | `null` | 否 | 启动时触发一次的trigger。`{prompt: "..."}`。 |
| `termination` | dict | `null` | 否 | 终止条件。见 [终止](#终止)。 |
| `max_subagent_depth` | int | `3` | 否 | 子代理最大嵌套深度。`0` = 无上限。 |
| `tool_format` | str \| dict | `"bracket"` | 否 | `bracket`、`xml`、`native`，或自定义 dict 格式。 |
| `mcp_servers` | list | `[]` | 否 | 每只代理的 MCP server。见 [MCP server](#Creature-config-里的-mcp-server)。 |
| `plugins` | list | `[]` | 否 | Lifecycle 插件。见 [插件](#插件)。 |
| `no_inherit` | list[str] | `[]` | 否 | 改成 **替换** 而非合并的 key。例如 `[tools, subagents]`。 |
| `memory` | dict | `{}` | 否 | `memory.embedding.{provider,model}`。见 [记忆](#记忆)。 |
| `output_wiring` | list | `[]` | 否 | 每只Creature的回合输出自动路由。见 [输出接线](#输出接线)。 |

### Controller 区块

为了向后兼容，下列字段也可以放在顶层。

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `llm` | str | `""` | `~/.kohakuterrarium/llm_profiles.yaml` 里的 profile 参照 (例如 `gpt-5.4`、`claude-opus-4.6`)。 |
| `model` | str | `""` | 没设 `llm` 时用的行内 model id。 |
| `auth_mode` | str | `""` | 空 (自动)、`codex-oauth` 等。 |
| `api_key_env` | str | `""` | 装 key 的环境变数。 |
| `base_url` | str | `""` | 覆盖 endpoint URL。 |
| `temperature` | float | `0.7` | Sampling temperature。 |
| `max_tokens` | int \| null | `null` | 输出上限。 |
| `reasoning_effort` | str | `"medium"` | `none`、`minimal`、`low`、`medium`、`high`、`xhigh`。 |
| `service_tier` | str | `null` | `priority`、`flex`。 |
| `extra_body` | dict | `{}` | 原样合并进 request JSON。 |
| `skill_mode`、`include_tools_in_prompt`、`include_hints_in_prompt`、`max_messages`、`ephemeral`、`tool_format` | | | 对映顶层同名字段。 |

每回合解析顺序：

1. CLI 旗标 `--llm`。
2. `controller.llm`。
3. `controller.model` (+ 可选的 `base_url` / `api_key_env`)。
4. `llm_profiles.yaml` 的 `default_model`。

### Input

Dict 字段：`{type, module?, class_name?, options?, ...型别专属 key}`。

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `type` | str | `"cli"` | `cli`、`tui`、`asr`、`whisper`、`none`、`custom`、`package`。 |
| `module` | str | — | 给 `custom` (例如 `./custom/input.py`) 或 `package` (例如 `pkg.mod`) 用。 |
| `class_name` | str | — | 要 instantiate 的类别。 |
| `options` | dict | `{}` | 模组专属选项。 |
| `prompt` | str | `"> "` | CLI prompt (cli input)。 |
| `exit_commands` | list[str] | `[]` | 触发离开的字符串。 |

### Output

支持一个默认输出，并可通过 `named_outputs` 配置旁路输出（例如 Discord webhook）。

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `type` | str | `"stdout"` | `stdout`、`tts`、`tui`、`custom`、`package`。 |
| `module` | str | — | `custom`/`package` 输出模块用。 |
| `class_name` | str | — | 要 instantiate 的类别。 |
| `options` | dict | `{}` | 模组专属选项。 |
| `controller_direct` | bool | `true` | 把控制器文本通过默认输出送出。 |
| `named_outputs` | dict[str, OutputConfigItem] | `{}` | Named 侧输出。每个 item 结构跟默认相同。 |

### 工具

工具条目列表。每一项可以是 dict，或同名内置工具的简写字符串。

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | — | 工具名 (必填)。 |
| `type` | str | `"builtin"` | `builtin`、`custom`、`package`。 |
| `module` | str | — | 给 `custom` (例如 `./custom/tools/my_tool.py`) 或 `package` 用。 |
| `class_name` | str | — | 要 instantiate 的类别。 |
| `doc` | str | — | 覆盖 skill 文件档。 |
| `options` | dict | `{}` | 工具专属选项。 |

简写：

```yaml
tools:
  - bash
  - read
  - write
```

### 子代理

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | — | 子代理识别字。 |
| `type` | str | `"builtin"` | `builtin`、`custom`、`package`。 |
| `module` | str | — | 给 `custom`/`package` 用。 |
| `config_name` | str | — | 模组里具名的 config 对象 (例如 `MY_AGENT_CONFIG`)。 |
| `description` | str | — | 父代理 prompt 里用到的描述。 |
| `tools` | list[str] | `[]` | 子代理被允许使用的工具。 |
| `can_modify` | bool | `false` | 子代理能不能做会改东西的操作。 |
| `interactive` | bool | `false` | 跨回合持续活着、接收 context update。 |
| `options` | dict | `{}` | 子代理专属选项。 |

### trigger

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `type` | str | — | `timer`、`idle`、`webhook`、`channel`、`custom`、`package`。 |
| `module` | str | — | 给 `custom`/`package` 用。 |
| `class_name` | str | — | 要 instantiate 的类别。 |
| `prompt` | str | — | trigger触发时注入的默认 prompt。 |
| `options` | dict | `{}` | trigger专属选项。 |

各型别常见选项：

- `timer`：`interval` (秒)。
- `idle`：`timeout` (秒)。
- `channel`：频道名 + filter。
- `webhook`：endpoint 配置。

### 压缩

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 开启压缩。 |
| `max_tokens` | int | profile 默认 | 目标 token 上限。 |
| `threshold` | float | `0.8` | 达到 `max_tokens` 多少比例时启动压缩。 |
| `target` | float | `0.5` | 压缩后目标比例。 |
| `keep_recent_turns` | int | `5` | 原样保留的回合数。 |
| `compact_model` | str | 控制器的 model | 摘要用的 LLM 覆盖。 |

### 输出接线

这是框架级路由条目列表。每回合结束时，框架都会构造一个 `creature_output` `TriggerEvent`，直接推送到每个目标 Creature 的事件队列中，完全绕过频道。讨论请参见 [Terrarium 指南 — 输出接线](../guides/terrariums.md#输出接线) 和 [模式概念](../concepts/patterns.md)；本节是配置参考。

条目字段：

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `to` | str | — | 目标 Creature 名称，或特殊字符串 `"root"`。 |
| `with_content` | bool | `true` | 设 `false` 时，事件 `content` 为空 (只是 metadata ping)。 |
| `prompt` | str \| null | `null` | 接收端 prompt override 的模板。没设时依 `with_content` 用默认模板。 |
| `prompt_format` | `simple` \| `jinja` | `"simple"` | `simple` 用 `str.format_map`；`jinja` 用 `prompt.template` 渲染 (支持条件式 / filter)。 |

模板变数 (两种格式都有)：`source`、`target`、`content`、`turn_index`、`source_event_type`、`with_content`。

简写形式中，裸字符串等同于 `{to: <str>, with_content: true}`：

```yaml
output_wiring:
  - runner                                   # 简写
  - { to: root, with_content: false }        # lifecycle ping
  - to: analyzer
    prompt: "[From coder] {content}"         # simple (默认)
  - to: critic
    prompt: "{{ source | upper }}: {{ content }}"
    prompt_format: jinja
```

注意：

- 只有Creature跑在Terrarium里时才有意义。独立Creature设了 `output_wiring` 也不会发出任何东西 (resolver 是Terrarium runtime 挂上去的；独立代理拿到的是 空操作 resolver，只会 log 一次)。
- 未知 / 停掉的目标会被记录日志后跳过；不会向源 Creature 的 turn finalisation 抛出异常。
- 源头的 `_finalize_processing` 会立刻跑完 — 每个目标的 `_process_event` 各自在自己的 `asyncio.Task` 里跑，不会因为某个接收者慢就把源头卡住。

### 终止

任何非零门槛都会生效。输出含有关键字时，关键字比对会停下代理。

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `max_turns` | int | `0` | |
| `max_tokens` | int | `0` | |
| `max_duration` | float | `0` | 秒。 |
| `idle_timeout` | float | `0` | 无事件秒数。 |
| `keywords` | list[str] | `[]` | 区分大小写的 substring 比对。 |

### Creature config 里的 MCP server

每只代理自己的 MCP server。代理启动时连线。

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | — | Server 识别字。 |
| `transport` | `stdio` \| `http` | — | Transport。 |
| `command` | str | — | stdio 执行档。 |
| `args` | list[str] | `[]` | stdio 参数。 |
| `env` | dict[str,str] | `{}` | stdio 环境变数。 |
| `url` | str | — | HTTP/SSE endpoint。 |

### 插件

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | — | 插件识别字。 |
| `type` | str | `"builtin"` | `builtin`、`custom`、`package`。 |
| `module` | str | — | 给 `custom` (例如 `./custom/plugins/my.py`) 或 `package` 用。 |
| `class` 或 `class_name` | str | — | 要 instantiate 的类别。 |
| `description` | str | — | 自由格式 metadata。 |
| `options` | dict | `{}` | 插件专属选项。 |

简写：裸字符串会被解析为套件中的插件名。

### 记忆

```yaml
memory:
  embedding:
    provider: model2vec       # 或 sentence-transformer、api
    model: "@best"            # preset 别名或 HuggingFace 路径
```

Provider 选项：

- `model2vec` (默认，不用 torch)。
- `sentence-transformer` (torch，质量较高)。

Preset 别名：`@tiny`、`@base`、`@retrieval`、`@best`、`@multilingual`、`@multilingual-best`、`@science`、`@nomic`、`@gemma`。

### 继承规则

`base_config` 走前面路径解析规则。合并用一套规则套在所有字段上：

- **标量** — 子层覆盖。
- **Dict** (`controller`、`input`、`output`、`memory`、`compact`…) — 浅层合并；子层 key 在顶层覆盖父层。
- **以 identity 为 key 的 list ** (`tools`、`subagents`、`plugins`、`mcp_servers`、`triggers`) — 依 `name` 联集。撞名时 ** 子层胜出** 并原地替换 base 条目 (保留 base 顺序)。没 `name` 的项目会串接。
- **其他 list** — 子层替换父层。
- **Prompt 档** — `system_prompt_file` 沿继承链串接；行内 `system_prompt` 最后附上。

两个可以退出默认行为的指令：

| 指令 | 效果 |
|-----------|--------|
| `no_inherit: [field, …]` | 列出的字段抛弃继承值。对标量、dict、identity list、prompt 链都适用。 |
| `prompt_mode: concat \| replace` | `concat` (默认) 保留继承 prompt 档链 + 行内；`replace` 清空继承 prompt — 等同 `no_inherit: [system_prompt, system_prompt_file]`。 |

 **范例。**

覆盖某个继承来的工具但不替换整份清单：

```yaml
base_config: "@kt-biome/creatures/swe"
tools:
  - { name: bash, type: custom, module: ./tools/safe_bash.py, class: SafeBash }
```

清空重来：完全抛弃继承的工具。

```yaml
base_config: "@kt-biome/creatures/general"
no_inherit: [tools]
tools:
  - { name: think, type: builtin }
```

为特殊人格完全替换 prompt：

```yaml
base_config: "@kt-biome/creatures/general"
prompt_mode: replace
system_prompt_file: prompts/niche.md
```

### 文件惯例

```
creatures/<name>/
  config.yaml           # 必要
  prompts/system.md     # 有参照就要存在
  tools/                # 自定义工具模组
  memory/               # context 档
  subagents/            # 自定义子代理 config
```

---

## Terrarium配置 (`terrarium.yaml`)

由 `kohakuterrarium.terrarium.config.load_terrarium_config` 加载。

```yaml
terrarium:
  name: str
  root:                  # 选用 — Terrarium外的 root 代理
    base_config: str     # 或任何 AgentConfig 字段直接行内写
    ...
  creatures:
    - name: str
      base_config: str   # 旧别名：`config:`
      channels:
        listen: [str]
        can_send: [str]
      output_log: bool         # 默认 false
      output_log_size: int     # 默认 100
      ...                      # 任何 AgentConfig 覆盖
  channels:
    <name>:
      type: queue | broadcast  # 默认 queue
      description: str
    # 或简写 — 字符串即为 description：
    # <name>: "description"
```

Terrarium字段摘要：

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | — | Terrarium名称。 |
| `root` | object | `null` | 选用的 root 代理配置。一定会拿到Terrarium管理工具。 |
| `creatures` | list | `[]` | 跑在Terrarium里的Creature。 |
| `channels` | dict | `{}` | 共享频道宣告。 |

Creature条目字段 (也接受任何 AgentConfig 字段直接行内写，例如 `system_prompt_file`、`controller`、`output_wiring` 等等)：

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | — | Creature名称。 |
| `base_config` (或 `config`) | str | — | Config 路径 (代理 config)。 |
| `channels.listen` | list[str] | `[]` | Creature消费的频道。 |
| `channels.can_send` | list[str] | `[]` | Creature能发布的频道。 |
| `output_log` | bool | `false` | 为每个 Creature 捕获 stdout。 |
| `output_log_size` | int | `100` | 每只Creature log buffer 最大行数。 |
| `output_wiring` | list | `[]` | 框架层级把这只Creature回合结束的输出自动送给其他Creature。条目形状见 [输出接线](#输出接线)。 |

频道条目字段：

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `type` | `queue` \| `broadcast` | `queue` | 传递语意。 |
| `description` | str | `""` | 会写在频道拓朴 prompt 里。 |

自动创建的频道：

- 每只Creature一条以它名字命名的 `queue` (DM 用)。
- 设了 `root` 时多一条 `report_to_root` queue。

Root 代理：

- 拿到附有 `terrarium_*` 与 `creature_*` 工具的 `TerrariumToolManager`。
- 自动 listen 每一条Creature频道、收 `report_to_root`。
- 继承 / 合并规则跟Creature相同。

---

## LLM profile (`~/.kohakuterrarium/llm_profiles.yaml`)

```yaml
version: 3
default_model: <preset name>

backends:
  <provider-name>:
    backend_type: openai | codex | anthropic
    base_url: str
    api_key_env: str

presets:
  <preset-name>:
    provider: <backend-name>   # backends 参照或内置
    model: str                 # model id
    max_context: int           # 默认 256000
    max_output: int            # 默认 65536
    temperature: float         # 选用
    reasoning_effort: str      # none | minimal | low | medium | high | xhigh
    service_tier: str          # priority | flex
    extra_body: dict
```

内置 provider 名称 (不可覆盖)：`codex`、`openai`、`openrouter`、`anthropic`、`gemini`、`mimo`。

所有附带的 preset 请看 [builtins.md — LLM presets](builtins.md#llm-presets)。

---

## MCP server 目录 (`~/.kohakuterrarium/mcp_servers.yaml`)

全域 MCP registry，用来替代每只代理的 `mcp_servers:`。

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

字段：

| 字段 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | — | 唯一识别字。 |
| `transport` | `stdio` \| `http` | — | Transport。 |
| `command` | str | — | stdio 执行档。 |
| `args` | list[str] | `[]` | stdio 参数。 |
| `env` | dict[str,str] | `{}` | stdio 环境变数。 |
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

| 字段 | 型别 | 说明 |
|---|---|---|
| `name` | str | 套件名称；会装在 `~/.kohakuterrarium/packages/<name>/`。 |
| `version` | str | Semver。 |
| `description` | str | 自由格式。 |
| `creatures` | list | `[{name}]` — `creatures/<name>/` 下的Creature config。 |
| `terrariums` | list | `[{name}]` — `terrariums/<name>/` 下的Terrarium config。 |
| `tools` | list | `[{name, module, class}]` — 提供的工具类别。 |
| `plugins` | list | `[{name, module, class}]` — 提供的插件。 |
| `llm_presets` | list | `[{name}]` — 提供的 LLM preset (实际值在套件里)。 |
| `python_dependencies` | list[str] | Pip requirement 字符串。 |

安装模式：

- `kt install <git_url>` — clone。
- `kt install <path>` — 复制。
- `kt install <path> -e` — 写一个指到来源的 `<name>.link`。

---

## API key 存储 (`~/.kohakuterrarium/api_keys.yaml`)

由 `kt login` 与 `kt config key set` 管理。格式：

```yaml
openai: sk-...
openrouter: sk-or-...
anthropic: sk-ant-...
```

解析顺序：存储的文件 → 环境变数 (`api_key_env`) → 空。

---

## 延伸阅读

- 概念：[边界概念](../concepts/boundaries.md)、[组合 Agent 概念](../concepts/foundations/composing-an-agent.md)、[多代理概念](../concepts/multi-agent/README.md)。
- 指南：[配置指南](../guides/configuration.md)、[Creature 指南](../guides/creatures.md)、[Terrarium 指南](../guides/terrariums.md)。
- 参考：[CLI 参考](cli.md)、[内置模块参考](builtins.md)、[Python API 参考](python.md)。
