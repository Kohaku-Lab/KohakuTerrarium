---
title: CLI
summary: 每一个 kt 子指令 — run、resume、login、install、list、info、model、embedding、search、terrarium、serve、app。
tags:
  - reference
  - cli
---

# CLI 参考

所有 `kt` 指令、子指令、旗标。CLI 是框架给操作者的接口：启动Creature、启动Terrarium、管理套件、配置 LLM、提供 web UI、搜索已存储的会话。

Creature、Terrarium、root 代理的心智模型请参见 [边界概念](../concepts/boundaries.md)。任务导向的路径请参见 [快速开始指南](../guides/getting-started.md) 和 [Creature 指南](../guides/creatures.md)。

## 入口

- `kt` — 安装后的 console script。
- `python -m kohakuterrarium` — 同上。
- 不加子指令执行 (例如从 Briefcase 双击) 时，`kt` 会自动开桌面 app。

## 全域旗标

| 旗标 | 用途 |
|---|---|
| `--version` | 印出版本、安装来源、套件路径、Python 版本、git commit。 |
| `--verbose` | 配 `--version` 使用；加印 `$VIRTUAL_ENV`、executable、git branch。 |

---

## 核心指令

### `kt run`

跑一只Creature。

```
kt run <agent_path> [flags]
```

Positional：

- `agent_path` — 本地目录 (要有 `config.yaml`)，或套件参照，例如 `@kt-biome/creatures/swe`。

旗标：

| 旗标 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `--log-level` | `DEBUG\|INFO\|WARNING\|ERROR` | `INFO` | Root logger 等级。 |
| `--log-stderr` | `auto\|on\|off` | `auto` | 把 log 镜像到 stderr。`auto` = I/O 模式不是 cli/tui 时开启 (例如 `plain`、`stdout`、`custom`、`package`)；`off` = 永不开启；`on` = 永远开启。 |
| `--session` | path | 自动 | 会话档；绝对路径或放在 `~/.kohakuterrarium/sessions/` 下的名字。 |
| `--no-session` | flag | — | 完全不做持久化。 |
| `--llm` | str | — | 覆盖 LLM profile (例如 `gpt-5.4`、`claude-opus-4.7`)。可以带 variation 选择器 — 见 [配置参考](configuration.md#variation-选择器)。 |
| `--mode` | `cli\|plain\|tui` | 自动 | 互动模式。TTY 上默认 `cli`，非 TTY 默认 `plain`。 |

行为：

- `@package/...` 会解析到 `~/.kohakuterrarium/packages/<pkg>/...`，遇到 `.link` 指标会跟 (给 editable 安装用)。
- 除非有 `--no-session`，会在 `~/.kohakuterrarium/sessions/` 自动建一个 `.kohakutr` 会话。
- 离开时会印 `kt resume <name>` 提示。
- 在 Rich CLI 模式下，Ctrl+C 会中断当前 turn；空闲时按两次 Ctrl+C（或 Ctrl+D / `/exit`）会干净退出。

### `kt resume`

恢复之前的会话。类型 (agent 或 terrarium) 从会话档自动检测。

```
kt resume [session] [flags]
```

Positional：

- `session` — 名字前缀、完整档名、或完整路径。不给就进互动式选单 (显示最近 10 个)。

旗标：

| 旗标 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `--pwd` | path | 会话记录的 cwd | 覆盖工作目录。 |
| `--last` | flag | — | 直接 resume 最近一个，不弹选单。 |
| `--log-level` | 同 `kt run` | | |
| `--log-stderr` | 同 `kt run` | `auto` | 把 log 镜像到 stderr。 |
| `--mode` | 同 `kt run` | | Terrarium会话强制 `tui`。 |
| `--llm` | str | | 覆盖这次 resume 的 LLM profile。支持 variation 选择器简写。 |

行为：

- `.kohakutr` 与旧的 `.kt` 扩展名都接受、会自动去掉。
- 前缀比对有歧义时会弹选单。

### `kt list`

列已安装的套件与本地代理。

```
kt list [--path agents]
```

| 旗标 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `--path` | str | `agents` | 除了已安装套件之外，另外扫的本地目录。主要在当 cwd 是有自己 `agents/` 目录的专案时有用；已安装的套件无论 `--path` 为何都会出现。 |

### `kt info`

印出Creature config 的名称、描述、模型、工具、子代理、文件。

```
kt info <agent_path>
```

---

## Terrarium

### `kt terrarium run`

跑多代理Terrarium。

```
kt terrarium run <terrarium_path> [flags]
```

Positional：

- `terrarium_path` — YAML 档或 `@package/terrariums/<name>`。

旗标：

| 旗标 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `--log-level` | 同 `kt run` | | |
| `--seed` | str | — | 启动时注入到 seed 频道的 prompt。 |
| `--seed-channel` | str | `seed` | 接收 `--seed` 的频道。 |
| `--observe` | 频道名字 list | — | 要观察的频道 (plain/log 模式)。 |
| `--no-observe` | flag | — | 关掉所有观察。 |
| `--session` | path | 自动 | 会话档路径。 |
| `--no-session` | flag | — | 关掉持久化。 |
| `--llm` | str | — | 覆盖 **每一只** Creature (含 root) 的 LLM profile。 |
| `--mode` | `cli\|plain\|tui` | `tui` | UI 模式。 |

行为：

- `tui`：多 tab view — root + 每只Creature + 每条频道。
- `cli`：把 root (有的话) 或第一只Creature挂到 RichCLI。
- `plain`：把观察到的频道消息流式到 stdout。

### `kt terrarium info`

印出Terrarium名称、Creature清单、listen/send 频道、频道清单。

```
kt terrarium info <terrarium_path>
```

---

## 套件

### `kt install`

从 git URL 或本地路径安装套件。

```
kt install <source> [-e|--editable] [--name <name>]
```

| 旗标 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `-e`、`--editable` | flag | — | 写一个 `<name>.link` 指向来源，而不是复制内容。 |
| `--name` | str | 从 URL/路径推得 | 覆盖安装的套件名称。 |

`<source>` 可以是：

- Git URL (clone 到 `~/.kohakuterrarium/packages/<name>`)。
- 本地目录 (复制进去；若加 `-e` 则 link)。

### `kt uninstall`

移除已安装的套件。

```
kt uninstall <name>
```

### `kt update`

更新 git 来源的套件。会跳过 editable 与非 git 的套件。

```
kt update [target] [--all]
```

| 旗标 | 型别 | 说明 |
|---|---|---|
| `--all` | flag | 更新每一个 git 来源的套件。 |

### `kt edit`

用 `$EDITOR` (没有则 `$VISUAL`、最后 `nano`) 打开Creature或Terrarium config。

```
kt edit <target>
```

`target` 接受套件参照 (`@pkg/creatures/name`) 与本地路径。

---

## 配置：`kt config`

### `kt config show`

印出 CLI 用到的每个 config 档路径。

### `kt config path`

印出某个 config 的路径，对象可以是：`home`、`llm_profiles`、`api_keys`、`mcp_servers`、`ui_prefs`。

```
kt config path [name]
```

### `kt config edit`

用 `$EDITOR` 打开 config 档。不给名字默认打开 `llm_profiles`。

```
kt config edit [name]
```

### `kt config provider` (别名：`kt config backend`)

管理 LLM provider (backend)。

#### `kt config provider list`

显示每个 provider 的名称、backend 类型、Base URL。

#### `kt config provider add`

互动式。会问 backend type、base URL、`api_key_env`、provider identity，以及要暴露的 provider-native tools。prompt 会提供 `openai`、`anthropic` 与 `codex`；存储时也使用这些规范 backend type 值。

```
kt config provider add [name]
```

`kt config provider add` 和 `kt config provider edit` 共用同一条互动式路径；唯一差别是 `edit` 需要给 positional 名称，并预填现有值。

#### `kt config provider edit`

字段同 `add`，预填现有值。

```
kt config provider edit <name>
```

#### `kt config provider delete`

```
kt config provider delete <name>
```

### `kt config llm` (别名：`kt config model`、`kt config preset`)

管理 LLM preset。

#### `kt config llm list`

显示 Name、Provider、Model、Groups 栏位 (该 preset 的 variation group 名称，以逗号分隔；没有就空) 以及 Default 标记。默认只列使用者定义的 preset。

| 旗标 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `--all` | flag | — | 也包含所有内置 preset。会分组显示 (User presets / Built-in presets)；Legend 会标示哪些条目已配置 API key / OAuth。 |

#### `kt config llm show`

印出完整 preset：名称、provider、backend type、model、`max_context` / `max_output`、base URL、`api_key_env`、temperature、reasoning_effort、service_tier、目前的 variation selection (若有)、preset 宣告的 variation group 及其选择器范例 (例如 `claude-opus-4.7@reasoning=xhigh`)、以及 `extra_body`。

```
kt config llm show <name>
```

#### `kt config llm add`

互动式。会询问是否把新 preset 设成默认 (默认 No)。

```
kt config llm add [name]
```

#### `kt config llm edit`

```
kt config llm edit <name>
```

#### `kt config llm delete`

```
kt config llm delete <name>
```

#### `kt config llm default`

不给引数时印出目前默认。给 `name` 就设成默认。

```
kt config llm default [name]
```

### `kt config key`

管理存起来的 API key。

#### `kt config key list`

字段：provider、api_key_env、来源 (`stored`/`env`/`missing`)、遮罩过的值。

#### `kt config key set`

把 API key 存到 `~/.kohakuterrarium/api_keys.yaml`。没给 `value` 时会遮罩提示输入。

```
kt config key set <provider> [value]
```

#### `kt config key delete`

清除存起来的 key (provider 的条目本身保留)。

```
kt config key delete <provider>
```

### `kt config login`

`kt login` 的别名。见 [Auth](#auth)。

### `kt config mcp`

管理全域 MCP server 目录 (`~/.kohakuterrarium/mcp_servers.yaml`)。

- `list` — 显示文件路径与 server 清单。
- `add [name]` — 互动式。会问 transport (`stdio`/`http`)、command、args JSON、env JSON、URL。
- `edit <name>` — 互动式编辑。
- `delete <name>` — 移除条目。

---

## Auth

### `kt login`

对某个 provider 做验证。

```
kt login <provider>
```

- `codex` backend：OAuth device-code 流程。Token 存在 `~/.kohakuterrarium/codex-auth.json`。
- API-key backend：遮罩提示输入，存到 `~/.kohakuterrarium/api_keys.yaml`。

---

## Model

### `kt model`

`kt config llm` 的薄向后兼容包装。新文件请优先使用 `kt config llm` 形式；`kt model` 保留作为一行别名。

```
kt model list
kt model default [name]
kt model show <name>
```

---

## 记忆与搜索

### `kt embedding`

为已存的会话建 FTS 与向量索引。`<session>` 接受名字前缀、完整档名或路径；旧的 `.kt` 扩展名除了 `.kohakutr` 外也会辨识。

```
kt embedding <session> [--provider ...] [--model ...] [--dimensions N]
```

| 旗标 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `--provider` | `auto\|model2vec\|sentence-transformer\|api` | `auto` | Auto 优先用 jina-v5-nano。 |
| `--model` | str | 视 provider 而定 | 该 provider 对应的 model，含别名如 `@tiny`、`@best`、`@multilingual-best`。 |
| `--dimensions` | int | — | Matryoshka 截断 (较短的向量)。 |

### `kt search`

搜索会话的记忆。`<session>` 接受与 `kt embedding` 相同的形式 (旧的 `.kt` 也辨识)。

```
kt search <session> <query> [flags]
```

| 旗标 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `--mode` | `fts\|semantic\|hybrid\|auto` | `auto` | 搜索模式。Auto 有向量就走 semantic，否则走 FTS。 |
| `--agent` | str | — | 只看某只代理的事件。 |
| `-k` | int | `10` | 最多返回几笔。 |

---

## Web 与桌面 UI

### `kt web`

跑 web server (阻塞、单一程序)。

```
kt web [flags]
```

| 旗标 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `--host` | str | `127.0.0.1` | 绑定 host。 |
| `--port` | int | `8001` | 绑定 port。被占用会自动递增。 |
| `--dev` | flag | — | 只起 API (前端自己用 `vite dev` 起)。 |
| `--log-level` | 同 `kt run` | | |

### `kt app`

跑原生桌面版本 (需要 pywebview)。

```
kt app [--port 8001] [--log-level ...]
```

### `kt serve`

Web server 的 daemon 管理。程序状态存在 `~/.kohakuterrarium/run/web.{pid,json,log}`。

#### `kt serve start`

以 detached 方式启动 server 程序。

```
kt serve start [--host 127.0.0.1] [--port 8001] [--dev] [--log-level INFO]
```

#### `kt serve stop`

先 SIGTERM，过 grace period 后 SIGKILL。

```
kt serve stop [--timeout 5.0]
```

| 旗标 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `--timeout` | float | `5.0` | 等待 graceful shutdown 的秒数。 |

#### `kt serve restart`

先 `stop` 再 `start`，把所有旗标转给 `start`。

#### `kt serve status`

印出 `running` / `stopped` / `stale`、PID、URL、started_at、版本、git commit。

#### `kt serve logs`

读 `~/.kohakuterrarium/run/web.log`。

```
kt serve logs [--follow] [--lines 80]
```

| 旗标 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `--follow` | flag | — | Tail log。 |
| `--lines` | int | `80` | 一开始印的行数。 |

---

## Extension

### `kt extension list`

列已安装套件提供的所有工具、插件、LLM preset。Editable 安装会标注。

### `kt extension info`

显示套件 metadata，加上它的Creature、Terrarium、工具、插件、LLM preset。

```
kt extension info <name>
```

---

## MCP (每只代理)

### `kt mcp list`

列某只代理 `config.yaml` 的 `mcp_servers:` 区段里宣告的 MCP server。字段：name、transport、command、URL、args、env key。

```
kt mcp list --agent <path>
```

MCP server 也可以放在 `~/.kohakuterrarium/mcp_servers.yaml` 全域目录，由 [`kt config mcp`](#kt-config-mcp) 管理。两边登记互不相干 — per-agent 条目在代理启动时挂上；目录条目不会自动挂，但可以依名称参照。

---

## 文件路径

| 路径 | 用途 |
|---|---|
| `~/.kohakuterrarium/` | Home。 |
| `~/.kohakuterrarium/llm_profiles.yaml` | LLM preset 与 provider。 |
| `~/.kohakuterrarium/api_keys.yaml` | 存储的 API key。 |
| `~/.kohakuterrarium/mcp_servers.yaml` | 全域 MCP server 目录。 |
| `~/.kohakuterrarium/ui_prefs.json` | UI 偏好配置。 |
| `~/.kohakuterrarium/codex-auth.json` | Codex OAuth token。 |
| `~/.kohakuterrarium/sessions/*.kohakutr` | 存起来的会话 (旧的 `*.kt` 也接受)。 |
| `~/.kohakuterrarium/packages/` | 已安装套件 (复制或 `.link` 指标)。 |
| `~/.kohakuterrarium/run/web.{pid,json,log}` | Web daemon 状态。 |

## 环境变数

| 变数 | 用途 |
|---|---|
| `EDITOR`、`VISUAL` | `kt edit` / `kt config edit` 用的编辑器。 |
| `VIRTUAL_ENV` | `kt --version --verbose` 会印出。 |
| `<PROVIDER>_API_KEY` | 每个 provider 的 `api_key_env` 指到的变数。 |
| `KT_SHELL_PATH` | 覆盖 `bash` 工具用的 shell。 |
| `KT_SESSION_DIR` | 覆盖 web API 的会话目录 (默认 `~/.kohakuterrarium/sessions`)。 |

## Exit code

- `0` — 成功。
- `1` — 一般错误。
- 编辑器的 exit code — 用于 `kt edit` / `kt config edit`。

## 互动式 prompt

这些指令可能会进入互动式 prompt：

- `kt resume` 没给引数或前缀有歧义。
- `kt terrarium run` 没 root 也没 `--seed`。
- `kt login`。
- `kt config` 下面所有 `... add` 子指令。
- `kt config key set` 没给值。

## 套件参照语法

`@<pkg-name>/<path-inside-pkg>` 会解析到 `~/.kohakuterrarium/packages/<pkg-name>/<path-inside-pkg>`，或跟 `<pkg-name>.link` 走。`kt run`、`kt terrarium run`、`kt edit`、`kt update`、`kt info` 都接受。

## Terrarium TUI slash 指令

在 `kt terrarium run --mode tui` 里，输入列接受 slash 指令。内置：`/exit`、`/quit`。其他指令来自Terrarium注册的 user command。见 [builtins.md#user-commands](builtins.md#user-commands)。

## 延伸阅读

- 概念：[边界概念](../concepts/boundaries.md)、[会话持久化概念](../concepts/impl-notes/session-persistence.md)。
- 指南：[快速开始指南](../guides/getting-started.md)、[会话指南](../guides/sessions.md)、[Terrarium 指南](../guides/terrariums.md)。
- 参考：[配置参考](configuration.md)、[内置模块参考](builtins.md)、[HTTP API 参考](http.md)。
