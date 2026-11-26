---
title: 内建插件
summary: 框架附带的运行时插件参考：sandbox、budget、repeat_guard、permgate、compact.auto。
tags:
  - reference
  - plugins
  - builtin
---

# 内建插件

KohakuTerrarium 附带以下运行时插件。它们在框架里没有任何特权，用的
是和使用者插件一样的 hook。它们在 agent 启动时被自动发现；config 用
和其他插件一样的 `plugins:` 区块来启用。

关于框架 / 插件边界的设计意图，以及 sandbox 插件的逐步示范，请见
[guides/plugins 的工作示例](../guides/plugins.md#worked-example-why-sandbox-is-a-plugin-not-a-framework-feature)。

| 名称 | Priority | Hooks | 实现 |
|------|----------|-------|------|
| [`sandbox`](#sandbox) | 1 | `pre_tool_execute`、`runtime_services`、`on_load` | 文件 / 网络 / 子行程能力闸门。 |
| [`budget`](#budget) | 5 | `pre_llm_call`、`post_llm_call`、`pre_tool_execute`、`pre_subagent_run`、`get_prompt_content` | 回合 / 工具呼叫 / 墙钟预算记账。 |
| [`repeat_guard`](#repeat_guard) | 10 | `pre_tool_execute`、`post_tool_execute`、`on_event` | 阻挡一直以同样方式失败的工具调用。 |
| [`compact.auto`](#compactauto) | 30 | `post_llm_call`、`on_load` | 在高 token LLM 呼叫之后触发 context 压缩。 |
| [`permgate`](#permgate) | 100 | `pre_tool_execute`、`on_load` | 互动式使用者批准工具呼叫。 |

`pre_*` hook 在低 priority 先跑。上面列的顺序是预设注册顺序。

`repeat_guard` **默认开启**（`ui.apps`、`ui.declarative` 也是），其他插件默认关闭。
`repeat_guard` 不增加提示词，也不增加工具。在 `plugins:` 中加一项即可关闭：

```yaml
plugins:
  - name: repeat_guard
    enabled: false
```

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
| `os_enforce` | enum | `auto` / `require` / `off` | `auto` | 子进程的内核写入限制（macOS `sandbox-exec`、Linux Landlock）。`auto`＝支持时启用，`require`＝不支持时拒绝启动，`off`＝只做规则检查。 |
| `fs_write_allow` | list[str] | 路径 | `[]` | 你指定的例外：即使在 `fs_deny` 之内也可写（`fs_write=deny` 时除外）。 |
| `locked` | bool | true / false | false | 拒绝运行时停用、修改选项与切换工作目录（HTTP 返回 409）。运行时只能开启不能关闭；以 `KT_SANDBOX_ALLOW_UNLOCK=1` 重启服务才能解锁。 |
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

- **路径范围**：`default` 允许 `cwd` 之下；`workspace` 允许 `cwd` 之
  下并拒绝向上穿越；`broad` 在 `fs_deny` 之外都允许；`deny` 全挡。
- **网络闸门**：当 `network=allow` 且设定了 `network_allowlist` 时，
  只有清单上的主机能过；否则在网络允许时全过。当 `network=deny` 时，
  所有网络工具呼叫（`web_fetch`、`web_search`）都会抛
  `PluginBlockError`。
- **子行程闸门**：插件透过 `runtime_services()` 发布
  `subprocess_runner` 服务。需要起子行程的工具（`bash` 等）从
  `ToolContext.runtime_services` 取用。Runner 会先检查 syscall 等级
  （`pure` 挡所有 spawn、`fs` 挡网路呼叫、`shell` 全允许）与网络白名
  单，然后才委派给 `asyncio.create_subprocess_exec`。
- **`backend=audit`**：违规会透过 agent logger 记录而不抛出。适合
  在新 workload 上做第一轮发现。
- **热重设**：呼叫插件的 `refresh_options()` 会重建内部能力结构；
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

要停止一直失败的同一个工具调用，请见 [`repeat_guard`](#repeat_guard)。

## `repeat_guard`

阻止模型反复发出一直失败的同一个工具调用。默认开启。
实现：`src/kohakuterrarium/builtins/plugins/repeat_guard/plugin.py`。

### Options

| Option | 类型 | 预设 | 用途 |
|--------|------|------|------|
| `repeat_failures` | int ≥ 0 | 3 | 同一个调用以相同参数、相同错误连续失败这么多次后，阻挡下一次相同调用。`0` 表示关闭。 |

### 行为

- **相同调用**：工具名加上参数，以规范化 JSON 比较（键的顺序不影响）。
  忽略调用控制参数 `run_in_background` 与 `allow_concurrent`。
- **失败**：工具结果带有错误或非零退出码。只有当错误与输出都和该调用上一次
  失败相同时才累加；不同的失败会把计数重置为 1。输出每次都不同的命令
  （例如会印出耗时的测试）因此不会达到上限。
- **重置**：同一调用成功会结束它的计数，其他调用不影响它。新回合开始时清空
  全部计数：使用者讯息、频道讯息、计时器，或任何不是后台工具 / 子 agent
  结果的事件。不会跨使用者回合阻挡。
- **阻挡**：达到 `repeat_failures` 次相同失败后，下一次相同调用不会执行，
  模型收到以下工具结果：

  ```text
  This exact call to image_gen failed 3 times in a row with the same arguments: <上一次错误的第一行>. Change the arguments or take another approach; repeating it will fail the same way.
  ```

  同一回合内之后的相同调用都得到同样的结果；被阻挡的调用不增加计数。
  改变任何一个参数的调用照常执行。
- **范围**：计数属于插件实例。每个 agent、每次子 agent 执行都有自己的实例，
  共用同一个工具也不会互相影响。被其他插件（`sandbox`、`budget`、`permgate`）
  阻挡的调用没有执行，不计入。计数不随 session 保存。

### 范例

```yaml
plugins:
  - name: repeat_guard
    options:
      repeat_failures: 5   # 0 表示关闭；`enabled: false` 移除插件
```

## `compact.auto`

在 LLM token 用量越过 compact manager 配置的门槛时，提前触发背景 context 压缩。
实现：`src/kohakuterrarium/builtins/plugins/compact/auto.py`。

### 配置

这个插件没有插件专属 options。请通过 host 的
[`compact:` 配置块](configuration.md#压缩)配置 manager；SubAgent 的内联
写法见 [SubAgent 指南](../guides/sub-agents.md#子代理自动压缩)。

### 行为

每一次 LLM 呼叫之后，`post_llm_call` 都会拿 token 用量和门槛比较。如
果超过，就呼叫 `context.compact_manager.trigger_compact()` 排程非同
步压缩：控制器继续跑，summariser 在背景工作，切换发生在回合之
间。见
[非阻塞压缩](../concepts/impl-notes/non-blocking-compaction.md)。

对主 Agent 而言，关闭 `compact.auto` 只会移除这个提前触发 hook；工具结果
回传后和 turn 结束时的核心延后检查仍然保留。SubAgent 没有独立的延后
host-loop 检查，因此仍需 `compact.auto` 才能自动压缩。两种情况下，
`compact.enabled: false` 都会关闭自动触发；主 Agent 上显式执行的
`/compact` 仍然可用。

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
| `gated_tools` | list[str] | `[]` | 需要批准的工具。空列表 = 每个工具；`"*"` 也匹配每个工具。 |
| `allowlist` | list[str] | `[]` | 永远不需要批准的工具，即使也列在 `gated_tools` 里。 |
| `timeout_s` | float | null | 等待使用者的秒数；null = 一直等。超时没有回应就拒绝。 |
| `surface` | `modal` / `chat` | `modal` | 批准提示出现的位置。 |

### 行为

`pre_tool_execute` 在设定的 `surface` 上发出可互动的 `confirm` 输出
事件，写明工具名并摘要其参数，然后等待回覆。选项为 `allow_once`、
`allow_session`（「Always allow」）与 `deny`。`allow_session` 在这个插件
实例存续期间不再询问该工具，不会持久化。其他任何结果（拒绝、取消、
`timeout_s` 内没有回应、bus 错误）都会抛 `PluginBlockError`，让 agent
把它当成普通工具失败处理。

例外只有一种：插件没有可以显示提示的宿主 agent 时，受管的调用会不经询问
直接放行。这发生在它的 `on_load` 执行之前，以及不带 agent 载入插件的宿主
（例如 MCP 工具服务器）里。fork 的工具策略比较严格：父 agent 没有 output
router 时会拒绝该调用。

网页 UI 与 TUI 把批准渲染成输出流上的内联 prompt；CLI 也透过同一个
output bus 出场，并经由 input loop 回覆。没有使用者介面的无头部署应该
关掉 permgate 或设 `timeout_s`，让没人回应的调用被拒绝，而不是一直等。

### 范例

```yaml
plugins:
  - name: permgate
    options:
      gated_tools: ["bash", "write", "edit"]
      allowlist: ["read", "grep", "glob"]
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
      gated_tools: ["*"]
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      walltime_budget: [300, 600]
```

## 另见

- [guides/plugins](../guides/plugins.md)：怎么写自己的插件。
- [reference/plugin-hooks](plugin-hooks.md)：每个 hook 的 signature。
- [concepts/modules/plugin](../concepts/modules/plugin.md)：设计理由。
