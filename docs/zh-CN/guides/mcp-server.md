---
title: MCP 服务器
summary: 启动按工作区管理的 MCP 服务器，连接外部客户端，管理工具任务，并按需启用本地 Creature 或子代理委派。
tags:
  - guides
  - mcp
  - deployment
---

# MCP 服务器

MCP 服务器让外部 MCP 客户端使用当前工作区中的 KT 能力。你可以直接调用文件与命令工具，也可以按需配置本地 Creature 或一次性子代理来承接任务。仅使用直接工具时，不会创建 Creature，也不会启动本地模型。

`kt mcp-serve` 为每个工作区管理一个独立后台进程、带认证的 Streamable HTTP 端点，以及可选的 ngrok 隧道。本指南介绍的是“让外部客户端调用 KT”；若要让 Creature 调用其他 MCP 服务器，请阅读 [MCP 客户端配置](mcp.md)。

> **开放前请确认权限范围。** 默认工具包含文件写入和命令执行；工作区是默认执行目录，**不是沙箱**。完整连接 URL 含访问密钥，应当按凭据保管。详细说明见 [访问与隔离边界](#访问与隔离边界)。

首次使用从 [首次接入](#首次接入) 开始。已经连通时，可直接查看 [调用工具与管理任务](#调用工具与管理任务)、[自定义直接工具与插件](#自定义直接工具与插件) 或 [委派给本地 Creature 或子代理](#委派给本地-creature-或子代理)；连接或运行异常见 [故障排查](#故障排查)。

## 首次接入

### 准备条件与接入模式

安装包含 `kt mcp-serve` 的 KT 版本，并准备一个稳定的公网 HTTPS 入口。两种接入模式择一使用：

| 模式 | 你需要准备 | KT 负责的部分 |
| --- | --- | --- |
| `ngrok` | 已安装并配置认证的 ngrok、账号和固定 HTTPS 域名 | 启动、监督和回收 ngrok 隧道进程 |
| `external` | 自行维护的稳定 HTTPS 入口，并将请求转发到本机回环端口 | 只管理本地 MCP 服务，不启动或停止外部隧道 |

本地端口默认是 8765。使用 `external` 时，将入口转发到 `http://127.0.0.1:8765`；选择其他端口时相应调整。即使宿主机本身可公网访问，也需要 HTTPS 反向代理：KT 只监听回环地址，不自行终止 TLS。入口部署可参考 [反向代理部署](deployment-reverse-proxy.md)。

向导中的 **公网 HTTPS origin** 是入口的协议、域名及可选端口，例如 `https://your-domain.example`，不含 MCP 路径。它不是稍后要填写到客户端中的完整连接 URL。

### 配置并启动

在要提供给客户端的工作区中运行：

```powershell
kt mcp-serve setup
kt mcp-serve start
kt mcp-serve url
```

`setup` 打开交互式配置向导，依次选择接入模式、公网 origin、本地端口、可选的 MCP 工具配置文件，以及托管模式下的 ngrok 可执行文件和配置文件。首次只使用默认工具时，可以不指定工具配置文件。保存前会显示变更摘要并要求确认；在向导中取消或输入结束（EOF）不会改动原配置。

**`setup` 只保存配置。** 它不会启动服务、安装 ngrok、注册账号、分配域名或测试公网连通性。保存前会检查本地依赖；各项校验的时机见 [命令参数与非交互使用](#命令参数与非交互使用)。

`start` 使用已保存的配置启动服务，并等待公网就绪检查。退出码 0 表示已通过公网完成带认证的初始化，并确认连接到当前实例；非零退出码不一定表示本地进程未启动，见 [区分本地就绪与公网就绪](#区分本地就绪与公网就绪)。

### 添加到客户端

服务就绪后，人类可读的启动输出会显示完整连接 URL，也可通过 `kt mcp-serve url` 再次获取。在支持 Streamable HTTP 的外部客户端中添加 MCP 服务器，粘贴这个**完整 URL**，不要只填写公网 origin。

完整 URL 的形式为 `https://your-domain.example/mcp/<密钥>`，其中密钥是首次运行 `setup` 时生成的 43 个字符的随机串。

完整 URL 含访问密钥，不要放入版本控制、普通日志或公开截图。客户端要求的工具调用确认仍由客户端处理，KT 不会绕过确认。

### 验证首次工具调用

连接后，先让客户端调用不带 `job_id` 的 `job_status`，确认能够取得 `instance_id`；再调用 `tree` 查看工作区根目录，验证一次只读工具操作。这一步不需要启动本地模型，也不需要写入文件。

到这里应分别确认三个结果：配置已保存、服务器公网就绪、客户端实际能调用工具。只看到 `setup` 成功，并不代表后两步已经完成。

## 日常启停与状态

### 启动、停止与重新获取 URL

配置完成后，日常使用不需要重复 `setup`：

```powershell
kt mcp-serve start
kt mcp-serve status
kt mcp-serve url
kt mcp-serve stop
```

重复或并发 `start` 会复用已有实例，不会重复启动，也不会自动应用[待生效配置](#当前配置与待生效配置)。`stop` 会取消所属任务、关闭本地监听并回收所管理的 ngrok 进程，保留连接配置，不删除云端资源。再次启动会复用连接设置，但会创建新的运行实例；任务与会话的生命周期见 [调用工具与管理任务](#调用工具与管理任务)。

监督进程不是开机服务。崩溃或重启电脑后，需要重新运行 `start`。

### 管理其他工作区

默认管理当前目录。所有子命令都可以加 `--workspace PATH`，例如：

```powershell
kt mcp-serve status --workspace ./another-project
```

工作区身份会解析符号链接和 Windows 路径大小写。移动目录会产生不同的身份，旧 URL 不会自动绑定到新位置。连接记录损坏时，所有子命令（包括 `setup`）都会拒绝继续操作。当前版本不提供工作区迁移命令；更换密钥或修复记录的方法见 [密钥泄露或连接记录损坏](#密钥泄露或连接记录损坏)。

### 区分本地就绪与公网就绪

`start` 默认最多等待 30 秒，可通过 `--wait` 指定 1–120 秒的等待时间。例如：

```powershell
kt mcp-serve start --wait 60
kt mcp-serve status --json
```

退出码 1 可能表示本地服务已经运行，但公网尚未连通。此时查看状态中的 `local_ready`、`public_ready`、`tunnel_state` 和最近公网检查时间，不要仅凭启动命令的退出码判断进程是否存在。公网就绪状态始终针对正在运行的实例，而不是尚未生效的配置。

`status --json` 中与就绪相关的字段：

| 字段 | 取值与含义 |
| --- | --- |
| `state` | `starting` 启动中；`ready` 本地已监听，且最近一次公网检查通过；`offline` 本地已运行，但公网检查未通过或隧道未运行；`failed` 启动或运行出错，原因见 `error`；`stopped` 未运行；`unresponsive` 进程仍持有实例锁，但运行状态超过 30 秒未更新或无法读取 |
| `local_ready` / `public_ready` | 本地监听是否就绪 / 最近一次公网检查是否通过 |
| `tunnel_state` | `ngrok` 模式下为 `starting`、`connecting`、`online`、`reconnecting` 或 `stopped`；`external` 模式下固定为 `external` |
| `public_checked_at` | 最近一次公网检查的 Unix 时间戳（秒）。检查通过后约每 10 秒复查一次，未通过时约每 2 秒重试 |
| `error` | 最近一次错误信息，不含密钥 |
| `record_path` | 连接记录 `connection.json` 的完整路径 |

如果等待到期时监督进程还未接管实例，启动命令会回收该子进程并报告错误。较慢的宿主机可增加 `--wait` 后重试。

## 调用工具与管理任务

### 默认工具与普通调用

不指定工具配置文件时，默认提供九个直接工具：`read`、`write`、`edit`、`multi_edit`、`glob`、`grep`、`tree`、`bash` 和 `python`。这些工具共用工作区执行上下文。

前台调用返回 KT Executor 的 `job_id`、输出、错误、退出码和元数据。`job_id` 标识一次执行，后续查询、等待或取消都使用该 ID，不需要重新执行原操作。

读取图片文件时，结果以 MCP 图片内容返回。读取 PDF 时只返回文本，不返回渲染后的页面图片。

### 后台执行、查询与等待

对 `bash` / `python` 传入 `run_in_background: true` 会立即返回**同一个 KT job ID**，执行继续进行。以下四个任务工具用于查询和控制这些任务：

| 工具 | 行为 |
| --- | --- |
| `job_status` | 读取单个任务；省略 `job_id` 时列出保留任务及 `instance_id` |
| `job_wait` | 等待 0–60 秒，默认 10 秒；超时返回当前状态 |
| `job_cancel` | 取消所属的运行中任务；Creature 委派的取消范围更大，见 [补充输入与取消](#补充输入与取消) |
| `job_promote` | 将前台调用释放到后台，保留原 job ID，不重复执行 |

任务不会仅因经过一段时间就自动转后台。等待超时、等待请求断开或被取消，都不会取消所属任务；需要停止执行时，应显式使用 `job_cancel`。

**后台完成不会自动唤醒 ChatGPT 对话。** 客户端需要主动查询或等待结果。本地委派也使用这些任务工具，但委派提交本身已经异步，无需再调用 `job_promote`。

### 取消、重试与结果保留

任务结果与 MCP 查询是否成功是两件事。即使任务失败或被取消，读取或等待其保留记录仍是成功的 MCP 调用；`state`、`error` 和 `exit_code` 描述的是任务结果。未知任务、无效查询参数和前台执行失败仍属于 MCP 错误。

变更操作的 HTTP 回复丢失后，不要立即重复提交。先通过 `job_status` 查询；涉及委派时，同时检查会话状态，避免把回复丢失误当作任务未执行。

服务器最多保留 100 个已完成任务，直接工具任务与委派任务采用相同的有界保留规则。正常停止服务器会取消所属任务；重启创建新实例，不会恢复旧任务，旧 ID 不会匹配新任务。连接 URL 的身份与这些运行时状态相互独立，URL 不变不代表旧任务仍然存在。

## 自定义直接工具与插件

### 配置文件与路径规则

配置分为几个不同层次，不要把它们当作同一种文件：

| 配置 | 负责什么 | 如何指定 |
| --- | --- | --- |
| 连接配置 | 工作区身份、密钥、公网 origin、端口、接入模式及外部配置文件路径 | 由 `kt mcp-serve setup` 管理 |
| MCP 工具配置文件 | 直接工具、执行插件和委派目标注册 | `setup --config ./mcp.yaml` |
| Creature 或子代理定义 | 委派目标自己的模型、工具、插件和运行限制 | MCP 工具配置中的 `delegation.<别名>.config` |
| ngrok 配置文件 | ngrok 自身的设置 | 托管模式下的 `setup --ngrok-config PATH` |

首次运行 `setup` 时，连接配置保存到 `~/.kohakuterrarium/mcp-serve/<workspace-key>/connection.json`，后续启动复用该记录。自定义直接工具时，另建独立的 YAML 或 JSON 文件，而不是把普通 Creature 配置直接交给 `--config`。

以下示例统一将 `mcp.yaml` 放在工作区根目录，并在该目录运行 CLI，因此使用 `workspace: .`。`workspace` 必须存在，相对路径以**配置文件所在目录**为基准；解析后必须与 CLI 选定的工作区一致，否则会拒绝启动。

### 选择工具和设置运行参数

工作区根目录下的 `mcp.yaml`：

```yaml
name: KT tools
workspace: .
pwd_guard: warn
tools:
  - name: read
  - name: write
  - name: edit
  - name: multi_edit
  - name: glob
  - name: grep
  - name: tree
  - name: bash
    config:
      timeout: 60
      max_output: 262144
  - name: python
    config:
      timeout: 60
plugins: []
```

保存配置路径，再重启服务以启用它：

```powershell
kt mcp-serve setup --config ./mcp.yaml
kt mcp-serve stop
kt mcp-serve start
```

省略 `tools` 会启用前述九个工具。工具名称必须唯一，`type` 必须为 `builtin`（默认值）。支持 `max_output` 及各工具声明的运行时选项：`timeout` 适用于 bash/Python，`env` 适用于 bash。不接受逐工具 `working_dir`，因为目录由共享执行上下文提供。

MCP 工具配置顶层不接受控制器通知设置、LLM 配置、提示词、触发器、compact 或 AgentConfig 继承；这些字段会被明确拒绝，而不是悄悄忽略。需要本地模型执行任务时，应使用下一节的委派目标配置。

目录是默认执行位置，**不是沙箱**。KT 原有的先读后写、过期读取检查、路径保护和执行策略仍然适用。`pwd_guard` 控制对工作区外路径的访问。默认的 `warn` 会拦截首次访问某个工作区外路径的操作并返回警告，对同一路径再次执行即放行；`block` 始终拒绝；`off` 不检查。放行记录只在当前运行实例内有效，并由该实例的所有客户端共享。

### 执行插件及其能力限制

直接工具的执行插件使用常规 `name`、`type`、`module`、`class` 和 `options` 配置，只支持执行侧能力：加载与卸载、分发、执行前后钩子、运行时服务和转后台。插件加载失败会中止启动；覆盖 LLM、Agent 生命周期、事件、compact、提示词、可见性、命令或终止钩子的插件会被拒绝。

这些插件在运行时能获取的上下文只有工作目录、名称和实例 ID，没有宿主 Agent、Controller、会话持久化、模型切换或子代理创建能力。自定义插件需要遵守此约定；插件属于受信任的本地代码。这里的限制针对直接工具运行时，不替代委派目标自己的插件配置。

## 委派给本地 Creature 或子代理

本节是可选能力。只使用直接工具时，无需准备模型配置或注册委派目标。

> 委派目标共享工作区文件，但使用自己的工具与插件策略。直接 MCP 工具白名单不会限制委派能力；启用前请核对目标定义中的权限与运行限制，详见 [访问与隔离边界](#访问与隔离边界)。

### 选择目标类型

| 类型 | 适用方式 | 对话生命周期 |
| --- | --- | --- |
| `creature` | 需要多轮续接，或使用 Creature 的工具、插件和自动触发器 | 可用 `session_id` 续接；已关闭会话不能续接 |
| `subagent` | 一次性任务，无需父 Creature | 运行中可补充输入，完成后不能在同一对话继续；新任务创建新子代理 |

目标只从本地配置中注册。客户端可以选择别名，但不能提交配置路径、内联定义，或覆盖模型与工具设置。仅注册目标不会立即创建实例或启动模型。

### 注册目标并准备模型配置

先用常规 KT 命令配置本地模型凭据和模型配置，并准备好目标定义。Creature 使用普通 KT 配置格式；下面的 `coder` 示例要求已安装包含该定义的 `@kt-biome` 包。

在前述 `mcp.yaml` 中保留 `workspace: .`，添加 `delegation` 字段。下面只展示工作区与委派部分；已有的 `tools`、`plugins` 等字段可以保留：

```yaml
workspace: .
delegation:
  coder:
    kind: creature
    config: "@kt-biome/creatures/swe"
    description: "在此工作区实现并验证修改"
  reviewer:
    kind: subagent
    config: ./reviewer.yaml
    description: "审查具体修改并报告发现"
```

目标定义的相对路径以 MCP 工具配置文件所在目录为基准，已安装的 `@package/...` 引用沿用 KT 包解析规则。定义在委派实例创建时加载；错误定义会使该任务失败，不会悄悄丢弃配置的能力。

独立子代理的 YAML/JSON 文件使用普通子代理定义的字段（见 [子代理](sub-agents.md)）。将以下内容保存为与 `mcp.yaml` 同目录的 `reviewer.yaml`；`llm: default` 引用已配置的本地 KT 模型配置：

```yaml
name: reviewer
llm: default
system_prompt: "审查请求中的修改，报告具体发现。"
tools:
  - name: read
  - name: glob
  - name: grep
  - name: bash
    config:
      timeout: 60
can_modify: false
max_turns: 30
timeout: 600
plugins: []
```

子代理的 `tools` 支持工具名称或普通工具配置项。自定义工具、包工具和插件的写法与普通 KT 配置相同，相对路径以定义文件所在目录为基准。省略 `llm` 时，也可通过 `model` 选择子代理模型；这里没有父模型可继承。当前不支持交互式子代理。运行限制和沙箱策略应配置在目标定义及其插件中。

若尚未保存 `mcp.yaml` 的路径，运行 `kt mcp-serve setup --config ./mcp.yaml`。随后执行 `kt mcp-serve stop`、`kt mcp-serve start`，并刷新客户端工具列表。修改目标清单需要重启服务器；修改所引用的定义只影响新创建的委派实例，不改变现有实例。

### 发起任务与续接会话

注册委派目标后，会增加六个工具：

| 工具 | 用途 |
| --- | --- |
| `delegation_targets` | 列出目标别名和描述，不启动模型 |
| `delegate` | 提交 `target`、`prompt`，可用 `session_id` 续接 Creature 会话 |
| `delegation_send` | 向运行中的委派任务（按 `job_id`）补充信息 |
| `delegation_sessions` | 列出本服务器拥有的会话、忙碌状态和当前委派任务 |
| `delegation_history` | 分页读取会话活动或当前公开对话快照 |
| `delegation_close` | 停止并关闭本服务器拥有的会话，保留可读历史 |

一次典型的 Creature 委派流程如下。以下是 MCP 工具调用示意，不是终端命令：

1. 调用 `delegation_targets`，选择目标别名。
2. 调用 `delegate(target="coder", prompt="调查失败的测试")`，保存返回的 `job_id` 和 `session_id`。前者标识本次执行，后者标识对话；提交在模型执行前返回。
3. 使用 `job_status` / `job_wait` 获取结果，或用 `delegation_history` 查看活动。等待与重试遵循前述 [任务管理规则](#调用工具与管理任务)，无需 `job_promote`。
4. 本轮结束后，用 `delegate(target="coder", session_id=..., prompt="应用修复")` 续聊。省略 `session_id` 会创建独立对话；不再需要会话时调用 `delegation_close`。

每个 Creature 会话同时接受一个活动委派轮次。忙碌会话会拒绝新任务，而不是把它排队。自动触发轮次也可能使会话忙碌，此时不一定存在 MCP 委派 job ID。

`delegation_sessions` 读取 Creature 的实时状态，包括 `idle`、`paused` 和 `stopped`，不根据目标配置推断状态。遇到忙碌会话时，结合历史查看它正在做什么。

### 补充输入与取消

运行期间，用 `delegation_send` 向活动 `job_id` 补充信息。补充输入不是另一项排队委派。对 Creature 而言，补充输入会先被缓冲，在当前这批工具调用的结果写入对话之后、下一次调用模型之前并入本轮对话。只有委派任务仍在运行时才会接受补充输入；返回结果中 `accepted` 为 `false` 表示没有送达，例如任务已结束或正在取消。处理补充输入时，KT 可能将前台工具转为后台，随后原委派轮次结束。

**Creature 轮次结束不等于其后台任务全部结束。** 自动活动属于会话历史，不会被当作无关任务的结果。需要停止执行时，根据希望保留的会话状态选择操作：

| 操作 | 停止范围 | 后续是否可以续接 |
| --- | --- | --- |
| 对运行中的 Creature 委派执行 `job_cancel` | 复用 KT stop：停止该 Creature、触发器及所有由 KT 管理的工具和子代理，包括之前轮次留下的后台工作；等待清理并保留历史 | 同一服务器生命周期内，可显式使用原 `session_id` 续接；取消本身不会自动重启 |
| 对运行中的独立子代理委派执行 `job_cancel` | 只停止该子代理自己的任务范围，并等待原有取消链完成 | 一次性子代理不能续接，后续任务创建新实例 |
| 执行 `delegation_close` | 停止并关闭指定会话，保留可读历史 | 已关闭会话不能续接 |
| 停止 MCP 服务器 | 取消服务器所属任务，结束其会话生命周期 | 服务器重启后不自动恢复任务或会话，旧 MCP 句柄不再有效 |

> 对**已完成任务**调用 `job_cancel` 不会产生效果。要停止 Creature 会话中剩余的后台工作，应使用 `delegation_close`。取消不会回滚文件修改，也不承诺回收任意脱离 KT 管理的操作系统进程。

取消 Creature 后，显式续接同一个、本服务器拥有的会话，会从已持久化的会话文件重建运行时（见 [查看历史与会话生命周期](#查看历史与会话生命周期)）。这与“关闭会话”或“重启服务器”不同，不应混为一谈。

### 查看历史与会话生命周期

`delegation_history(session_id=..., view="events")` 查看活动；`view="conversation"` 查看公开消息和完整保留的工具结果。两种视图通过 `cursor` 和 `limit`（1–200）分页，但保留与分页方式不同：

| 视图 | 需要注意的限制 |
| --- | --- |
| `events` | 只保留最新 2,000 条事件；用 `truncated` / `earliest_cursor` 报告淘汰情况 |
| `conversation` | 分页读取的是可变快照；压缩或进行中的轮次可能改变偏移量 |

Creature 会话在显式关闭或服务器停止前保留；取消运行不会删除可供续接的历史。服务器不会连接其他 KT 进程、导入任意已保存对话，也不会在自身重启后自动恢复任务或会话。

Creature 持久化使用 `~/.kohakuterrarium/mcp-serve/sessions/<instance-id>/` 下的普通 `.kohakutr` 文件。文件保留不代表原 MCP 句柄还能使用：这些句柄只在当前服务器生命周期内有效。

## 修改配置并使其生效

### 保存配置与重启

再次运行 `setup` 即可修改连接配置。修改已有连接时，未指定的字段保留原值；首次创建连接时，未指定的可选字段采用默认值。向导中留空保留显示值，输入 `-` 清除可选文件路径。`--clear-config` 和 `--clear-ngrok-config` 分别清除对应的自定义文件路径，恢复默认设置。

**保存配置不会改变当前运行实例。** 新设置需要停止服务后重新启动才会生效；重复执行 `start` 只会复用当前实例并报告待生效变更，不会悄悄重启。

例如，已有连接改用一个准备好的新公网 origin：

```powershell
kt mcp-serve setup --non-interactive --origin https://new-domain.example
kt mcp-serve status
kt mcp-serve url                 # 服务运行时显示当前 URL；停止时显示已保存 URL
kt mcp-serve url --configured    # 显式获取下一次启动使用的 URL
kt mcp-serve stop
kt mcp-serve start
```

修改 origin 不会轮换密钥（轮换密钥使用 `rotate`，见 [密钥泄露或连接记录损坏](#密钥泄露或连接记录损坏)），但重启后需要更新客户端中的连接 URL。切换到 `external` 模式会清除已保存的 ngrok 专用设置；该模式下传入 ngrok 参数会被拒绝。

### 当前配置与待生效配置

`status` 显示 `active`、`configured`、`pending_changes` 和 `restart_required`，不显示凭据。它们分别用于查看当前运行配置、已保存配置、两者差异及是否需要重启。公网就绪检查仍针对当前运行实例。

当前实例及其所有隧道重试使用绑定运行标识的私密 `active.json` 快照，不会在任务中途采用待生效配置。

**快照只冻结 `setup` 管理的字段，不冻结所引用文件的内容。** 修改 MCP 工具配置文件，会在下一次工具进程启动时生效；修改 ngrok 配置文件可能影响下一次隧道重启。状态比较不检测、也不保证冻结这些文件的内容，因此没有 `pending_changes` 不代表外部文件没有修改。委派目标定义的加载时机见 [注册目标并准备模型配置](#注册目标并准备模型配置)。

### 并发修改与升级

向导会检查配置记录是否在打开后发生变化。如果另一个终端已保存新配置，本次保存会报冲突并要求重开向导，不覆盖对方修改。所有校验都先于单次原子保存。启动失败会保留新配置并报告错误，不自动回滚。

已有连接记录无需重新配置即可读取。较旧 CLI 启动的进程没有 active 快照，需要先停止并重新启动一次，再使用运行中配置编辑或通过新版 CLI 获取运行 URL。升级不会生成新的身份或密钥。

## 故障排查

监督进程和隧道的诊断信息保存在连接记录旁，即 `~/.kohakuterrarium/mcp-serve/<workspace-key>/` 下的 `server.log`、`tunnel.log`。排查连接问题时，先查看 `kt mcp-serve status --json`，再结合日志判断。

| 现象 | 检查与处理 |
| --- | --- |
| `start` 返回非零，但本地服务似乎已经运行 | 查看 `local_ready`、`public_ready`、`tunnel_state` 和最近公网检查时间；区分公网尚未连通与本地启动失败。若监督进程尚未接管就超时，可增加 `--wait` 后重试 |
| `setup` 成功，但无法从客户端连接 | `setup` 不验证公网连通性。确认入口转发到所选回环端口、服务公网就绪，并确认客户端填写的是完整连接 URL 而不是 origin |
| 本地端口被占用 | 检查占用情况，或用 `setup --port` 保存其他端口；`external` 入口的转发目标也需相应调整，再启动服务 |
| 报错 `Tool configuration belongs to a different workspace` | 对照 CLI 的当前目录或 `--workspace`，检查 `mcp.yaml` 中按文件所在目录解析的 `workspace`；两者必须指向同一工作区 |
| 修改配置后没有生效 | 查看 `pending_changes`、`restart_required`，停止后再启动；重复 `start` 不会重启。若改的是所引用文件的内容，状态差异不会检测它 |
| 增加了委派目标，但客户端找不到工具 | 修改目标清单后需要重启服务器，并刷新客户端工具列表 |
| 后台任务完成后没有收到回复 | 后台完成不会自动唤醒客户端对话；主动调用 `job_status` 或 `job_wait` |
| 委派返回 busy，但没有活动的 MCP 委派 job ID | Creature 可能正在处理自动触发轮次；检查 `delegation_sessions` 的实时状态和 `delegation_history` |
| 状态显示 `unresponsive` | 运行状态超过 30 秒未更新，不一定表示工具已停止执行；结合日志判断，原因见 [进程与隧道恢复机制](#进程与隧道恢复机制) |
| 报错 `Missing or invalid saved connection` | 连接记录损坏，见 [密钥泄露或连接记录损坏](#密钥泄露或连接记录损坏) |
| `rotate` 报错 `MCP instance lock is busy` | 服务仍在运行，先执行 `stop`；如果已经停止，稍后重试 |
| 重启后旧 `job_id` / `session_id` 不可用 | 服务器重启创建新实例，不自动恢复旧任务或会话；持久化文件与稳定连接 URL 不会延长旧 MCP 句柄的有效期 |

## 访问与隔离边界

完整密钥 URL 是**持有者凭据**，不是 OAuth 或 ChatGPT 账号身份；持有 URL 的人即可访问该实例的工具。`setup` 摘要、状态和生命周期命令的 JSON 输出会隐藏密钥，但人类可读的就绪启动输出和 `url` 命令会主动显示完整 URL。

同一服务器的所有已认证客户端共享直接工具的读取历史、工具、插件、任务，以及委派会话与历史访问权限；不同服务器实例的状态相互独立。不要把不同客户端或不同对话当作权限隔离边界。

委派实例继承 MCP 工作区作为工作目录，不同对话共享目录中的文件，不创建 worktree 或文件系统隔离。MCP 不额外增加路径限制；委派目标的工具、插件和自动触发器遵循目标配置，**不受直接 MCP 工具白名单及其策略约束**。需要的运行限制和沙箱策略应配置在目标定义及其插件中。

HTTPS 隧道在服务商处终止 TLS，不应假设服务商无法看到明文。

### 密钥泄露或连接记录损坏

密钥泄露或需要定期更换时，停止服务后用 `rotate` 生成新密钥：

```powershell
kt mcp-serve stop
kt mcp-serve rotate
kt mcp-serve start
kt mcp-serve url
```

`rotate` 只替换当前工作区的密钥，origin、接入模式、端口和配置文件路径等其他设置保持不变，其他工作区不受影响。它只能在服务停止时运行，也不会替你停止服务：实例仍在运行时会报错 `MCP instance lock is busy`，不做任何修改。执行时没有二次确认，完成后服务仍保持停止。轮换只要求已有有效的连接记录，不要求 ngrok 或工具配置等依赖可用。

`rotate` 的普通输出和 `--json` 输出都不包含密钥或连接 URL；用 `url` 获取新 URL，并更新所有客户端。重新启动后，旧 URL 不再通过认证；上一次运行留下的 `active.json` 快照也会在启动时被替换。建议逐条执行上面的命令，确认 `stop` 和 `rotate` 都成功后再继续。管理其他工作区时，每条命令都要带上相同的 `--workspace`。

`setup` 仍然不会更换密钥。如果在轮换前已经打开了 `setup` 向导，保存时会报配置冲突，需要重新运行 `setup`。

连接记录损坏时，`rotate`、`setup` 和其他子命令都会报错 `Missing or invalid saved connection`。此时按以下步骤重新生成连接：

1. 运行 `kt mcp-serve stop`。必须先停止服务，再移动记录。该命令仍会停止运行中的实例，但结束时可能报错，可以忽略。
2. 将 `connection.json` 移出原目录作为备份。记录损坏时无法查询状态，文件位于 `~/.kohakuterrarium/mcp-serve/<workspace-key>/`。
3. 重新运行 `kt mcp-serve setup`。这相当于首次配置：需要重新提供 origin、模式、端口、`--config` 等设置，保存时会生成新密钥。
4. 运行 `kt mcp-serve start`，在客户端中把旧 URL 替换为新 URL。

如果你有完好的备份，也可以在停止服务后用备份覆盖 `connection.json`，这样会保留原密钥和 URL。如果备份中的密钥可能已经泄露，恢复后应立即运行 `rotate`。

## 高级参考

### 命令参数与非交互使用

配置参数属于 **`setup`**，而不是 `start`：

| 命令 | 主要参数 |
| --- | --- |
| `setup` | `--mode`、`--origin`、`--port`、`--config`、`--ngrok-bin`、`--ngrok-config`、`--clear-config`、`--clear-ngrok-config`、`--non-interactive` |
| `start` | `--wait`，仅控制启动等待，不接受上述配置参数 |
| `url` | `--configured`，获取下一次启动使用的 URL |
| `rotate` | 无专用参数；仅在服务停止时替换密钥，保留其他设置 |
| 所有子命令 | `--workspace PATH` |
| `setup` / `start` / `stop` / `status` / `rotate` | `--json`，输出不含 MCP 密钥的 JSON |

没有已保存的配置时，`start` 会提示先运行 `setup`。脚本中可显式选择一种接入模式；以下两条是替代方案，不需要连续执行：

```powershell
kt mcp-serve setup --non-interactive --mode ngrok --origin https://your-fixed-domain.example
kt mcp-serve setup --non-interactive --mode external --origin https://your-domain.example
```

非 TTY 输入、`--non-interactive` 或 `--json` 会关闭 `setup` 的所有交互提示；缺少必要参数时返回非零退出码。交互模式下的命令行参数用于预填向导。

各命令的退出码：

| 退出码 | 情况 |
| --- | --- |
| 0 | 命令成功；对 `start` 而言，表示已通过公网就绪检查。交互式 `setup` 在确认环节选择不保存时也返回 0 |
| 1 | `start` 结束时公网尚未就绪（本地服务可能已在运行）；配置、依赖、锁或进程出错，例如没有已保存配置、另一条生命周期命令仍在执行、`stop` 在 20 秒内未能停止实例、对运行中的工作区执行 `rotate`；交互式 `setup` 被 Ctrl+C 或 EOF 中断 |
| 2 | 命令行参数不合法 |

使用 `--json` 时，出错的命令输出 `{"error": "..."}`。

保存前会校验 origin 和本地依赖：解析工具配置、在托管模式下确认能够找到 ngrok，以及确认显式指定的 ngrok 配置文件可读。ngrok 配置文件的内容由 ngrok 在启动时验证，本地端口是否被占用也在启动时检查。

### 进程与隧道恢复机制

后台监督进程为每个工作区持有操作系统文件锁，单凭旧 PID 不会认定进程归属。停止请求通过私密本地文件携带当前运行标识，不通过远程管理接口发送；旧停止请求不能停止新一轮实例。

公网故障不会触发随机域名回退，也不会创建新的工具实例。托管 ngrok 退出后按 1–30 秒的有界退避重试；仍在运行的 ngrok 自行处理网络重连。监督进程会定期检查公网实例身份，不下载任务内容。

仅托管模式会清除 ngrok 子进程继承的 HTTP 代理环境变量，保留 ngrok 自己的配置，不更改系统代理。归属管道让隧道守护进程能在监督进程意外退出时回收自己的 ngrok 子进程。

Windows 上短暂占用文件的读取者可能延迟状态文件的原子更新。此类诊断写入失败不会停止工具执行；运行状态超过 30 秒未更新时会显示为 `unresponsive`，而仍被持有的归属锁会阻止重复启动。

### 委派运行时与团队协作范围

Creature 使用 KT 无界面 I/O，并保留命名输出和触发器。Terrarium 配方不是委派目标：团队任务的关联、完成和取消需要单独的协作协议；内部使用 Terrarium 承载 Creature，并不提供这套团队级语义。

### 嵌入 ASGI 应用

`api.mcp_tools.create_app(config, secret=..., port=..., public_origin=...)` 返回 ASGI 应用。运行其 lifespan，并**关闭宿主访问日志**。所有请求（包括发现）都需要精确的密钥路径。SDK 看到的是已脱敏的路径，并校验允许的 Host/Origin。不要挂载未受保护的副本，也不要同时公开 Studio 管理 API。

## 参阅

- [MCP 客户端配置](mcp.md)：将外部 MCP 工具接入 Creature。
- [Creature 配置](creatures.md)：定义本地委派目标。
- [子代理](sub-agents.md)：子代理能力与配置。
- [反向代理部署](deployment-reverse-proxy.md)：维护外部 HTTPS 入口。
