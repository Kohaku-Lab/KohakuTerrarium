---
title: 内建插件
summary: 框架附带的四个运行时插件参考 —— sandbox、budget、permgate、compact.auto。
tags:
  - reference
  - plugins
  - builtin
---

# 内建插件

KohakuTerrarium 附带四个运行时插件。它们在框架里没有任何特权 —— 用的
是和使用者插件一样的 hook。它们在 agent 启动时被自动发现；config 用
和其他插件一样的 `plugins:` 区块来启用。

关于框架 / 插件边界的设计意图，以及 sandbox 插件的逐步示范，请见
[guides/plugins —— 工作示例](../guides/plugins.md#worked-example-why-sandbox-is-a-plugin-not-a-framework-feature)。

| 名称 | Priority | Hooks | 实现 |
|------|----------|-------|------|
| [`sandbox`](#sandbox) | 1 | `pre_tool_execute`、`runtime_services`、`on_load` | 文件 / 网络 / 子行程能力闸门。 |
| [`budget`](#budget) | 5 | `pre_llm_call`、`post_llm_call`、`pre_tool_execute`、`pre_subagent_run`、`get_prompt_content` | 回合 / 工具呼叫 / 墙钟预算记账。 |
| [`compact.auto`](#compactauto) | 30 | `post_llm_call`、`on_load` | 在高 token LLM 呼叫之后触发 context 压缩。 |
| [`permgate`](#permgate) | 100 | `pre_tool_execute`、`on_load` | 互动式使用者批准工具呼叫。 |

`pre_*` hook 在低 priority 先跑。上面列的顺序是预设注册顺序。

## `sandbox`

硬性能力闸门。在不修改工具本身的前提下限制工具与子行程能做什么。
实现：`src/kohakuterrarium/builtins/plugins/sandbox/plugin.py`。

### Options

| Option | 类型 | 值 | 预设 | 用途 |
|--------|------|----|------|------|
| `enabled` | bool | true / false | true | 总开关。 |
| `backend` | enum | `auto` / `audit` / `off` | `auto` | `auto` = 违规时阻挡；`audit` = 仅记录；`off` = 不强制。 |
| `profile` | enum | `PURE` / `READ_ONLY` / `WORKSPACE` / `NETWORK` / `SHELL` | `WORKSPACE` | 能力预设。 |
| `fs_read` / `fs_write` | enum | `default` / `deny` / `workspace` / `broad` | 来自 profile | 文件读 / 写范围覆盖。 |
| `network` | enum | `default` / `deny` / `allow` | 来自 profile | 网络存取覆盖。 |
| `syscall` | enum | `default` / `pure` / `fs` / `shell` / `any` | 来自 profile | 子行程能力等级。 |
| `env` | enum | `default` / `filtered` / `inherit` | 来自 profile | 子行程环境变数处理。 |
| `tmp` | enum | `default` / `private` / `shared` | 来自 profile | 临时目录隔离。 |
| `fs_deny` | list[str] | 路径 | `[]` | 额外的路径黑名单（支持环境变数展开）。 |
| `network_allowlist` | list[str] | 主机名 | `[]` | `network=allow` 时的允许清单。空清单 = 网络允许时全允许。 |
| `blocked_tools` | list[str] | 工具名 | `[]` | 不论参数都不允许呼叫的工具。 |

### Profile 预设

| Profile     | fs_read | fs_write   | network | syscall | env      |
|-------------|---------|------------|---------|---------|----------|
| `PURE`      | deny    | deny       | deny    | pure    | filtered |
| `READ_ONLY` | broad   | deny       | default | fs      | default  |
| `WORKSPACE` | default | workspace  | allow   | fs      | default  |
| `NETWORK`   | deny    | deny       | allow   | default | default  |
| `SHELL`     | default | workspace  | allow   | shell   | filtered |

### 行为

- **路径范围** —— `default` 允许 `cwd` 之下；`workspace` 允许 `cwd` 之
  下并拒绝向上穿越；`broad` 在 `fs_deny` 之外都允许；`deny` 全挡。
- **网络闸门** —— 当 `network=allow` 且设定了 `network_allowlist` 时，
  只有清单上的主机能过；否则在网络允许时全过。当 `network=deny` 时，
  所有网络工具呼叫（`web_fetch`、`web_search`）都会抛
  `PluginBlockError`。
- **子行程闸门** —— 插件透过 `runtime_services()` 发布
  `subprocess_runner` 服务。需要起子行程的工具（`bash` 等）从
  `ToolContext.runtime_services` 取用。Runner 会先检查 syscall 等级
  （`pure` 挡所有 spawn、`fs` 挡网路呼叫、`shell` 全允许）与网络白名
  单，然后才委派给 `asyncio.create_subprocess_exec`。
- **`backend=audit`** —— 违规会透过 agent logger 记录而不抛出。适合
  在新 workload 上做第一轮发现。
- **热重设** —— 呼叫插件的 `refresh_options()` 会重建内部能力结构；
  随后的 `pre_tool_execute` 呼叫看到的就是新政策，无须重启。

### 配置范例

```yaml
plugins:
  - name: sandbox
    options:
      profile: WORKSPACE
      network_allowlist: ["api.example.com", "github.com"]
      fs_deny: ["~/.ssh", "$HOME/.aws"]
```

```yaml
# audit-only 模式做安全推行
plugins:
  - name: sandbox
    options:
      profile: SHELL
      backend: audit
```

```yaml
# 纯计算，零 I/O
plugins:
  - name: sandbox
    options:
      profile: PURE
      blocked_tools: ["web_fetch", "web_search", "bash"]
```

## `budget`

多轴预算记账与强制。
实现：`src/kohakuterrarium/builtins/plugins/budget/plugin.py`。

### Options

| Option | 类型 | 预设 | 用途 |
|--------|------|------|------|
| `turn_budget` | `[soft, hard]` ints 或 null | null | LLM 回合数上限。soft = 警告；hard = 阻挡。 |
| `tool_call_budget` | `[soft, hard]` ints 或 null | null | 总工具呼叫数上限。 |
| `walltime_budget` | `[soft, hard]` 秒 或 null | null | 墙钟秒数上限。 |
| `subagent_turn_budget` | `[soft, hard]` ints 或 null | null | 单一子 agent 执行的回合数上限。 |
| `inject_alert` | bool | true | 越过 soft 阈值时把警告注入下一则 system / user 讯息。 |

任何轴跨越 hard 阈值时，下一个 `pre_tool_execute` /
`pre_llm_call` / `pre_subagent_run` 会抛 `PluginBlockError`，错误讯息
点出哪个轴。跨过 soft 阈值时则注入警告，让 LLM 在 hard 阈值之前主动
收尾。

### 状态

状态以 per-session 形式储存在 session store，命名空间是
`plugin:budget:*`，所以 resume 会保留累积计数。

### 范例

```yaml
plugins:
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      walltime_budget: [300, 600]
```

## `compact.auto`

在 LLM token 用量越过门槛时触发背景 context 压缩。
实现：`src/kohakuterrarium/builtins/plugins/compact/auto.py`。

### Options

| Option | 类型 | 预设 | 用途 |
|--------|------|------|------|
| `threshold_ratio` | float | 0.7 | 当 prompt token 用量超过模型 context 窗口的这个比例时触发。 |
| `min_messages` | int | 8 | 在允许压缩之前的最小讯息数。 |

### 行为

每一次 LLM 呼叫之后，`post_llm_call` 都会拿 token 用量和门槛比较。如
果超过，就呼叫 `context.compact_manager.trigger_compact()` 排程非同
步压缩 —— 控制器继续跑，summariser 在背景工作，切换发生在回合之
间。见
[非阻塞压缩](../concepts/impl-notes/non-blocking-compaction.md)。

### Pack 别名

`auto-compact` 是一个内建 pack，展开为单一带预设 options 的
`compact.auto` 启用。只要功能开启即可的 config 可以使用
`default_plugins: ["auto-compact"]`。

## `permgate`

互动式使用者批准工具呼叫。Agent 在 output bus 上发出确认事件、等使用
者回覆，再依答案进行或终止。
实现：`src/kohakuterrarium/builtins/plugins/permgate/plugin.py`。

### Options

| Option | 类型 | 预设 | 用途 |
|--------|------|------|------|
| `enabled` | bool | true | 总开关。 |
| `tools` | list[str] / `"all"` | `"all"` | 哪些工具需要批准。`"all"` = 每个工具。 |
| `whitelist` | list[str] | `[]` | 跳过批准的工具（使用者选「always」时也会被加进 per-session 「always allow」清单）。 |
| `auto_approve_pattern` | str | "" | regex 模式；符合的工具 args 会自动批准而不询问。 |
| `prompt_template` | str | 预设 | 自订显示给使用者的批准 prompt。 |

### 行为

`pre_tool_execute` 发出 `tool_approval_request` 输出事件，附工具名、
args、request id。Agent 在 output bus 上等一个匹配 id 的
`tool_approval_reply` 事件；回覆带 `decision ∈ {approve, deny,
always}`。`always` 把工具加进 per-session whitelist（持久化在插件 state
里）。`deny` 抛 `PluginBlockError`，让 agent 把它当成普通工具失败处
理。

网页 UI 与 TUI 把批准渲染成输出流上的内联 prompt；CLI 也透过同一个
output bus 出场，并经由 input loop 回覆。没有使用者介面的无头部署应该
关掉 permgate 或设 `auto_approve_pattern: "^.*$"`。

### 范例

```yaml
plugins:
  - name: permgate
    options:
      tools: ["bash", "write", "edit"]
      whitelist: ["read", "grep", "glob"]
```

## 启用总览

```yaml
# 最严的 agent：权限闸门 + 预算 + 自动压缩。
default_plugins: ["auto-compact"]
plugins:
  - name: sandbox
    options:
      profile: WORKSPACE
      network_allowlist: ["api.example.com"]
  - name: permgate
    options:
      tools: "all"
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      walltime_budget: [300, 600]
```

## 另见

- [guides/plugins](../guides/plugins.md) —— 怎么写自己的插件。
- [reference/plugin-hooks](plugin-hooks.md) —— 每个 hook 的 signature。
- [concepts/modules/plugin](../concepts/modules/plugin.md) —— 设计理由。
