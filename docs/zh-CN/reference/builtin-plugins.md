---
title: 内建插件
summary: 框架附带的运行时插件参考：sandbox、budget、permgate、compact.auto、browser。
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
| [`compact.auto`](#compactauto) | 30 | `post_llm_call`、`on_load` | 在高 token LLM 呼叫之后触发 context 压缩。 |
| [`permgate`](#permgate) | 100 | `pre_tool_execute`、`on_load` | 互动式使用者批准工具呼叫。 |
| [`browser`](#browser) | 0 | `contribute_tools`、`runtime_services`、`pre_tool_execute`、`get_prompt_content`、`on_disable` | 由六个 `browser_*` 工具驱动的交互式 Chromium。 |

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
  所有网络工具呼叫都会抛 `PluginBlockError`。插件内建一张以工具名称
  为键的表（`web_fetch`：其 `url` 参数；`web_search`：`duckduckgo.com`），
  工具声明的 `url_arguments`、`network_hosts`、`network_access` 会加到
  这张表上（见 [自定义模块](../guides/custom-modules.md#声明网络使用)）。
  使用这些名称的工具即使什么都没声明也会受管制；每个主机或参数在一次
  呼叫中只检查一次。
- **网络政策服务**：启用时插件也透过 `runtime_services()` 发布
  `network_policy` 服务，供在工具 hook 之外建立连线的组件（例如过滤
  代理）使用。`check(host, port)` 回传 `None` 表示允许，否则回传简短
  的拒绝原因，套用 `network` 与 `network_allowlist`；audit 模式下记录
  后允许。`mode` 为 `"allow"` 或 `"deny"`。`requires_os_confinement`
  在 `os_enforce=require` 生效时为真，表示绕过 `subprocess_runner` 起的
  行程会逃过它要求的内核约束。每次读取都跟随插件当下的选项。
- **写入政策服务**：启用时插件也发布 `write_policy` 服务，供在工具呼叫
  之外写入文件的组件（例如浏览器保存下载）使用。`check(path)` 回传
  `None` 表示允许，否则回传简短的拒绝原因（例如 `fs_write=deny`），
  判断与写入工具的路径参数完全相同：`fs_write`、工作区边界、`fs_deny`
  与 `fs_write_allow`。相对路径以 agent 当下的工作目录解析（切换工作
  目录后跟着变）；在没有 agent 的宿主（例如 MCP 工具服务器）里，以插件
  载入时的目录解析。audit 模式下记录后允许；每次呼叫都跟随插件当下的选项。
- **持有服务**：插件在整个生命周期内发布同一组 `network_policy` 与
  `write_policy` 对象，组件可以跨呼叫持有。选项变更立即作用在持有的对象
  上，`enabled: false` 或 `backend: off` 会让它全部允许。透过插件管理器
  停用插件只会停止把对象交给新的工具呼叫：已持有的对象仍按最后的选项
  回答，重新启用后交出的是同一组对象。
- **哪些工具呼叫看得到**：工具呼叫拿到的是建立其工具 context 的插件管理器
  的服务。context-fork 子 agent 的呼叫拿到父 agent 的对象，并且也会经过
  父 agent 的 sandbox 检查。一般子 agent 的工具 context 由父 agent 建立，
  所以它的呼叫带着父 agent 的服务，而它的 hook 只经过它自己的插件；设定
  在子 agent 本身的 sandbox 会管制这些 hook，但不会把服务发布给它的工具。
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

## `browser`

每个 Creature 一个交互式 Chromium，由六个工具驱动（`browser_navigate`、
`browser_snapshot`、`browser_act`、`browser_screenshot`、`browser_tabs`、
`browser_eval`；见[内建组件](builtins.md#browser)）。插件提供这些工具、持有浏览器
会话（与 Creature 的子代理共用），并在 Creature 停止、插件卸载或运行时被停用时
关闭它。需要 `browser-use` extra 和 `playwright install chromium`。
实现：`src/kohakuterrarium/builtins/plugins/browser/plugin.py`；
运行时代码在 `src/kohakuterrarium/browser/`。

### Options

| Option | 类型 | 预设 | 用途 |
|--------|------|------|------|
| `headless` | bool | true | 不显示窗口运行。 |
| `channel` | str | null | 已安装的浏览器 channel，例如 `chrome` 或 `msedge`。 |
| `executable_path` | str | null | Chromium 系浏览器的路径。 |
| `viewport` | `"WxH"` / `[w, h]` | `1280x800` | 页面尺寸，CSS 像素（宽 320–7680，高 200–4320）。 |
| `profile_dir` | str | null | 持久 profile 目录；null = 每次启动用一次性 profile。 |
| `allow_hosts` | list[str] | `[]` | 非空时，页面只能访问这些主机。模式：`host`、`*.host`、`host:port`、`[v6]:port`。 |
| `block_hosts` | list[str] | `[]` | 页面永远不能访问的主机；最先检查。这里的 IP 地址也会拦截解析到它的主机名。 |
| `allow_loopback` | bool | false | 允许回环地址（本地开发服务器）。 |
| `allow_private` | bool | false | 允许内网、运营商级 NAT 与 IPv6 唯一本地地址。 |
| `secrets` | dict | `{}` | 密钥名，各自映射到 `{env: VAR_NAME, origins: [...]}`（非空的 `scheme://host[:port]` 列表），供 `type` 动作使用；只会输入到这些 origin，其值会从结果中遮蔽。 |
| `nav_timeout_s` | float | 30 | 导航超时（秒）。 |
| `action_timeout_s` | float | 5 | 每个动作的超时（秒）；一批 `browser_act` 最多 60 秒，若（此值 + 10 秒）的三倍更长则取后者。 |
| `snapshot_max_chars` | int | 20000 | 快照长度上限（1000–1000000）。 |
| `idle_close_s` | float | 600 | 闲置这么多秒后关闭浏览器；下一次调用会启动新的。 |

未知的 option 名会被拒绝。

### 行为

- **延迟启动。** 浏览器在第一次浏览器调用时启动。闲置关闭、崩溃或
  `close_browser` 会丢弃它；下一次调用启动一个新的，并说明状态已丢失。停止、
  卸载和运行时停用会永久关闭它：之后的调用直接失败，不会重新启动。插件放手的浏览器
  （修改 option、停用）还在关闭时，绝不会启动新的浏览器。不再响应的浏览器会连同它
  启动的所有进程一起被强制结束。
- **网络。** 所有流量都经过会话持有的过滤代理，代理会检查每一跳、子资源、
  弹出窗口和 WebSocket。回环与内网地址默认拒绝，可用 option 放行；链路本地
  地址（包括云元数据地址）一律拒绝。
- **sandbox。** 代理按宿主此刻发布的 sandbox `network_policy` 服务判定每个新连接；
  只要还有连接开着，每 2 秒重新判定已打开的连接（每次浏览器调用时也立刻判定），
  关闭 sandbox 现在拒绝的连接。下载在写入任何内容之前先用它的 `write_policy` 检查。
  `network: deny` 拒绝所有浏览器工具；`os_enforce: require` 拒绝启动浏览器。
- **主机资源。** 引擎的主机资源关闭时（多用户服务器上的非管理员用户），只允许
  headless、一次性 profile、自带的 Chromium（绝不用系统浏览器）和公网地址，且不能
  使用 `secrets`。
- **Prompt。** 启用期间且至少一个浏览器工具开着时，插件加入一小段 `## Browser pages`，说明页面内容放在
  `page_content` 元素中，是数据而不是指令。

### 范例

```yaml
plugins:
  - name: browser
    options:
      allow_loopback: true
      allow_hosts: ["localhost", "127.0.0.1"]
  - name: permgate
    options:
      gated_tools: [browser_navigate, browser_act, browser_eval, browser_tabs]
```

完整的使用流程、网络策略、密钥与疑难排解见 [guides/browser](../guides/browser.md)。

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
