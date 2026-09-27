---
title: MCP 服务器
summary: 配置按工作区管理的 MCP 服务器，向外部客户端提供 KT 工具，并将任务委派给本地 Creature 或子代理。
tags:
  - guides
  - mcp
  - deployment
---

# MCP 服务器

独立 MCP 服务器向外部 MCP 客户端提供 KT 工具。未配置委派目标时，它不会创建 Creature，也不会启动本地模型。配置委派目标后，客户端还可以调用本地 Creature 和独立的一次性子代理。

`kt mcp-serve` 为每个工作区管理一个独立后台进程、带认证的 Streamable HTTP 端点，以及可选的 ngrok 隧道。

## 配置一次，日常启动与停止

安装包含 `kt mcp-serve` 的 KT 版本，然后选择公网接入方式：使用托管 ngrok 时，需要先准备账号和固定 HTTPS 域名；已有稳定 HTTPS 入口时，则使用 external 模式。在要提供给客户端的工作区中运行：

```powershell
kt mcp-serve setup
kt mcp-serve start
kt mcp-serve status
kt mcp-serve stop
kt mcp-serve start
```

`setup` 在交互式终端中打开配置向导，用于选择接入模式、公网 HTTPS origin、本地端口（默认 8765）、可选的工具配置文件，以及托管模式下的 ngrok 可执行文件和配置文件。保存前会显示变更摘要并要求确认。取消或输入结束（EOF）不会改动原配置。

Setup 只保存配置，不启动服务，也不安装 ngrok、注册账号、分配域名或测试公网连通性。保存前会校验 origin 和本地依赖：解析工具配置、确认能够找到 ngrok，以及确认显式指定的 ngrok 配置文件可读。ngrok 配置文件的内容由 ngrok 在启动时验证；本地端口是否被占用也在启动时检查。

脚本中可使用显式参数：

```powershell
kt mcp-serve setup --non-interactive --mode ngrok --origin https://your-fixed-domain.example
kt mcp-serve setup --non-interactive --mode external --origin https://your-domain.example
```

`--ngrok-bin`、`--ngrok-config`、`--port` 和 `--config` 都属于 **setup**。`start` 只接受 `--wait` 等生命周期参数，不接受配置参数；没有已保存的配置时，启动命令会提示先运行 setup。

非 TTY 输入、`--non-interactive` 或 `--json` 会关闭所有交互提示；缺少必要参数时返回非零退出码。交互模式下的命令行参数用于预填向导。JSON 输出不包含 MCP 密钥。

首次 setup 将规范化的工作区路径、公网 origin、随机密钥、本地端口、接入模式和可选的工具配置路径保存到 `~/.kohakuterrarium/mcp-serve/<workspace-key>/connection.json`，后续启动会复用这些设置。服务就绪后，人类可读的启动输出会显示完整连接 URL，方便主动复制；也可用 `kt mcp-serve url` 再次查看。请将此 URL 视为私密凭据。Setup 摘要、状态和生命周期命令的 JSON 输出会隐藏密钥。

所有命令都可加 `--workspace PATH` 管理另一个目录。工作区身份会解析符号链接和 Windows 路径大小写。移动目录会产生不同的身份，旧 URL 不会自动绑定到新位置。连接记录损坏时会拒绝继续操作，需要显式恢复；当前版本不提供工作区迁移或密钥轮换命令。

后台监督进程为每个工作区持有操作系统文件锁。并发或重复 `start` 会复用现有实例，单凭旧 PID 不会认定进程归属。停止请求通过私密本地文件携带当前运行标识，不通过远程管理接口发送；旧的停止请求不能停止新一轮实例。`stop` 会取消所属任务、关闭本地监听并回收所管理的 ngrok 进程，保留连接配置，不删除云端资源。

`start` 默认等待最多 30 秒，直到通过公网完成带认证的初始化，并确认返回的正是当前实例；可用 `--wait 1..120` 调整。退出码 0 表示公网就绪已验证。退出码 1 也可能表示本地服务已运行、但公网尚未连通：此时查看 `status` 中的 `local_ready`、`public_ready`、`tunnel_state` 和最近公网检查时间。

如果等待到期时监督进程还未接管实例，启动命令会回收该子进程并报告错误。较慢的宿主机可增加 `--wait` 后重试。

公网故障不会触发随机域名回退，也不会创建新的工具实例。托管 ngrok 退出后按 1–30 秒的有界退避重试；仍在运行的 ngrok 自行处理网络重连。监督进程会定期检查公网实例身份，不下载任务内容。

若使用独立维护的稳定入口：

```powershell
kt mcp-serve setup --non-interactive --mode external --origin https://your-domain.example
kt mcp-serve start
```

将该入口转发到选定的本机回环端口。KT 不会启动或停止外部隧道。即使宿主机本身可公网访问，也需要 HTTPS 反向代理：KT 只监听回环地址，不自行终止 TLS。仅托管模式会清除 ngrok 子进程继承的 HTTP 代理环境变量，保留 ngrok 自己的配置，不更改系统代理。

监督进程和隧道的诊断信息保存在连接记录旁的 `server.log`、`tunnel.log`。归属管道让隧道守护进程能在监督进程意外退出时回收自己的 ngrok 子进程。监督进程本身不是开机服务：崩溃或重启电脑后，需要重新运行 `start`。

Windows 上短暂占用文件的读取者可能延迟状态文件的原子更新。此类诊断写入失败不会停止工具执行；心跳长时间未更新时状态会变为 `unresponsive`，而仍被持有的归属锁会阻止重复启动。

## 运行期间修改已保存配置

再次运行 setup 即可修改配置。省略的字段保留原值，新配置使用默认值。`--clear-config` 和 `--clear-ngrok-config` 分别恢复相应默认设置；向导中留空保留显示值，输入 `-` 清除可选文件路径。切换到 external 模式会清除已保存的 ngrok 专用设置，该模式下传入 ngrok 参数会被拒绝。修改 origin 不会轮换密钥，但会提示更新 ChatGPT 中的连接 URL。

服务运行时可以保存新配置。当前实例及其所有隧道重试使用绑定运行标识的私密 `active.json` 快照，不会在任务中途采用待生效配置。新设置在下一次实例启动时生效。`status` 显示 `active`、`configured`、`pending_changes` 和 `restart_required`，不显示凭据。公网就绪状态始终针对正在运行的实例。重复 `start` 会复用该实例并报告待生效变更，不会悄悄重启。

```powershell
kt mcp-serve setup --non-interactive --origin https://new-domain.example
kt mcp-serve status
kt mcp-serve url                 # running URL; saved URL when stopped
kt mcp-serve url --configured    # explicitly copy the next-start URL
kt mcp-serve stop
kt mcp-serve start
```

向导会检查配置记录是否在打开后发生变化。如果另一个终端已保存新配置，本次保存会报冲突并要求重开向导，不覆盖对方修改。所有校验都先于单次原子保存。启动失败会保留新配置并报告错误，不自动回滚。

快照只冻结 **setup 管理的字段**，不冻结外部文件内容。修改被引用的工具配置文件，会在下一次工具进程启动时生效；修改 ngrok 配置文件可能影响下一次隧道重启。状态比较不检测、也不保证冻结这些文件的内容。

已有连接记录无需重新配置即可读取。较旧 CLI 启动的进程没有 active 快照，需要先停止并重新启动一次，再使用运行中配置编辑或通过新版 CLI 获取运行 URL。升级不会生成新的身份或密钥。

## 配置

不指定 `--config` 时，下列九个工具使用默认配置。要自定义，在 setup 中通过 `--config` 指定独立 YAML 或 JSON 文件：

```yaml
name: KT tools
workspace: ./work
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

## 委派给本地 Creature 或子代理

在同一个 `setup --config` 指定的配置文件中注册目标：

```yaml
workspace: ./work
delegation:
  coder:
    kind: creature
    config: "@kt-biome/creatures/swe"
    description: "Implement and verify changes in this workspace"
  reviewer:
    kind: subagent
    config: ./reviewer.yaml
    description: "Review a concrete change and report findings"
```

目标清单来自本地配置。客户端只能选择别名，不能提交配置路径、内联定义，或覆盖模型与工具设置。相对路径以 MCP 配置文件所在目录为基准，已安装的 `@package/...` 引用沿用 KT 包解析规则。定义在实例创建时加载；错误定义会使该任务失败，不会悄悄丢弃配置的能力。修改目标清单需要重启服务器；修改所引用的定义只影响新实例，不改变现有实例。

Creature 使用普通 KT 配置格式。独立子代理不需要父 Creature，其 YAML/JSON 文件采用 SubAgentConfig 字段，使用 `llm` 选择本地 KT 模型配置，`tools` 支持工具名称或普通工具配置项：

```yaml
name: reviewer
llm: default
system_prompt: "Review the requested change. Report concrete findings."
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

自定义工具、包工具和插件沿用 KT 现有工厂，相对于定义位置解析。省略 `llm` 时，也可通过 `model` 选择子代理模型；这里没有父模型可继承。首版不支持交互式子代理。运行限制和沙箱策略应配置在目标定义及其插件中。

先用常规 KT 命令配置本地模型凭据和模型配置，再保存并启用服务器配置：

```bash
kt mcp-serve setup --config ./mcp.yaml
kt mcp-serve stop
kt mcp-serve start
```

重启后刷新客户端工具列表。注册委派目标后会增加六个工具：

| 工具 | 用途 |
| --- | --- |
| `delegation_targets` | 列出目标别名和描述，不启动模型 |
| `delegate` | 提交 `target`、`prompt`，可用 `session_id` 续接 Creature 会话 |
| `delegation_send` | 按 KT 现有输入语义向活动 `job_id` 补充信息 |
| `delegation_sessions` | 列出本服务器拥有的会话、忙碌状态和当前委派任务 |
| `delegation_history` | 分页读取会话活动或当前公开对话快照 |
| `delegation_close` | 停止并关闭本服务器拥有的会话，保留可读历史 |

典型调用流程：

1. 调用 `delegation_targets`，选择目标别名。
2. 调用 `delegate(target="coder", prompt="Investigate the failing test")`。保存返回的两个 ID：`job_id` 标识本次执行，`session_id` 标识对话。提交在模型执行前返回。
3. 使用 `job_status` / `job_wait` 获取结果。每次最多等待 60 秒，等待超时或客户端断开都不会取消执行。委派任务已经异步运行，无需 `job_promote`。
4. 用 `delegation_history(session_id=..., view="events")` 查看活动，或用 `view="conversation"` 查看公开消息和完整保留的工具结果。通过 `cursor` 和 `limit`（1–200）分页。活动只保留最新 2,000 条事件，使用 `truncated` / `earliest_cursor` 报告淘汰情况。对话分页读取可变快照，压缩或进行中的轮次可能改变偏移量。
5. 用 `delegate(target="coder", session_id=..., prompt="Apply the fix")` 续聊；省略 `session_id` 则创建独立对话。
6. 用 `job_cancel` 停止当前委派；不再需要会话时调用 `delegation_close`。

不要仅因 HTTP 回复丢失就重复提交，先查询任务和会话。同一服务器的所有已认证客户端共享该服务器的会话与历史访问权限。委派任务记录与直接工具任务采用相同的有界保留规则。

### 运行与取消语义

委派实例继承 MCP 工作区作为工作目录。不同对话共享该目录中的文件，不创建 worktree 或文件系统隔离。MCP 不额外增加路径限制；工具、插件和自动触发器遵循目标配置，独立于直接 MCP 工具白名单及其策略。Creature 使用 KT 无界面 I/O，并保留命名输出和触发器。仅注册目标不会立即创建实例。

每个 Creature 会话同时接受一个活动委派轮次。忙碌会话会拒绝新任务，包括正在处理自动触发轮次的情况；此时可能没有 MCP 委派 job ID。会话列表读取 Creature 的实时状态，包括 idle、paused 和 stopped，不根据目标配置推断状态。可通过历史查看其活动。

运行期间的补充输入不是另一项排队委派，而是在 KT 正常边界交付。处理补充输入时，KT 可能将前台工具转为后台，随后原委派轮次结束。取消已完成任务不会产生效果；要停止会话中剩余的工作，应使用 `delegation_close`。Creature 轮次结束不等于其后台任务全部结束；自动活动属于会话历史，不会被当作无关任务的结果。

对 Creature 调用 `job_cancel` 会复用 KT stop：停止该实例、触发器及其所有由 KT 管理的工具和子代理，包括之前轮次留下的后台工作。取消会等待清理并保留对话历史。之后显式续接同一个、本服务器拥有的会话时，会通过 KT 现有持久化与恢复流程重建运行时；取消本身不会自动重启。

取消独立子代理只停止它自己的任务范围，并等待原有取消链完成。两种取消操作都不会回滚文件修改，也不承诺回收任意脱离管理的操作系统进程。

Creature 会话在显式关闭或服务器停止前一直存活；已关闭会话不能续接。子代理是一次性的：运行中接受补充输入，完成后不能在同一对话继续，新任务会创建新的子代理。

服务器不会连接其他 KT 进程、导入任意已保存对话，也不会在自身重启后自动恢复任务或会话。Creature 持久化使用 `~/.kohakuterrarium/mcp-serve/sessions/<instance-id>/` 下的普通 `.kohakutr` 文件，MCP 句柄仍只在当前服务器生命周期内有效。

Terrarium 配方不是委派目标。团队任务的关联、完成和取消需要单独的协作协议；内部使用 Terrarium 承载 Creature，并不提供这套团队级语义。

## 直接工具的配置细节

`workspace` 必须存在，相对路径以配置文件所在目录为基准。省略 `tools` 会启用前述九个工具。工具名称必须唯一，`type` 必须为 `builtin`（默认值）。支持 `max_output` 及各工具声明的运行时选项：`timeout` 适用于 bash/Python，`env` 适用于 bash。不接受逐工具 `working_dir`，因为目录由共享执行上下文提供。顶层的控制器通知设置、LLM 配置、提示词、触发器、compact 和 AgentConfig 继承会被明确拒绝，不会悄悄忽略。

目录是默认执行位置，**不是沙箱**。KT 原有的先读后写、过期读取检查、路径保护和执行策略仍然适用。`pwd_guard: warn` 会在首次访问目录外文件时返回警告；有意重试会遵循 KT 现有规则。

执行插件使用常规 `name`、`type`、`module`、`class` 和 `options` 配置。只支持执行侧插件能力：加载与卸载、分发、执行前后钩子、运行时服务和转后台。配置的插件加载失败会中止启动。覆盖 LLM、Agent 生命周期、事件、compact、提示词、可见性、命令或终止钩子的插件会被拒绝。

这些插件的 PluginContext 提供工作目录、名称和实例 ID，但没有宿主 Agent、Controller、会话持久化、模型切换或子代理创建能力。自定义插件需要遵守此约定；插件属于受信任的本地代码。

## 嵌入服务器

`api.mcp_tools.create_app(config, secret=..., port=..., public_origin=...)` 返回 ASGI 应用。运行其 lifespan，并**关闭宿主访问日志**。所有请求（包括发现）都需要精确的密钥路径。SDK 看到的是已脱敏的路径，并校验允许的 Host/Origin。不要挂载未受保护的副本，也不要同时公开 Studio 管理 API。

## 调用、任务与状态

前台调用返回 Executor job ID、输出、错误、退出码和元数据。原生图片文件通过 KT 现有媒体解析器转换为 MCP 图片内容。PDF 文本可用；由于此运行时没有持久化产物存储，共享标准化逻辑目前会省略生成的页面图片。

对 bash/Python 传入 `run_in_background: true` 会立即返回**同一个 KT job ID**，执行继续进行。四个工具提供现有 JobStore 的访问接口：

| 工具 | 行为 |
| --- | --- |
| `job_status` | 读取单个任务，或列出保留任务及 `instance_id` |
| `job_wait` | 等待 0–60 秒，默认 10 秒；超时返回当前状态 |
| `job_cancel` | 取消所属的运行中任务；Creature 委派会停止整个实例 |
| `job_promote` | 将前台调用释放到后台，不重复执行 |

即使任务失败或被取消，读取或等待其保留记录仍是成功的 MCP 调用；`state`、`error` 和 `exit_code` 描述的是任务结果。未知任务、无效查询参数和前台执行失败仍属于 MCP 错误，因此客户端不会把成功的取消状态查询误判为无效调用。

不会仅因经过一段时间就自动转后台。等待请求断开或被取消，不会取消所属任务；需要取消时使用 `job_cancel`。变更操作的回复丢失后，先查询任务再决定是否重试。后台完成**不会**自动唤醒 ChatGPT 对话，客户端需要主动查询或等待。

同一服务器的所有已认证客户端共享读取历史、工具、插件和任务；不同实例的状态相互独立。正常停止通过 KT 原有流程取消所属任务。重启创建新实例，不会恢复旧任务，旧 ID 不会匹配新任务。现有 JobStore 最多保留 100 个已完成任务；URL 身份与这些内存状态相互独立。

## 访问边界

完整密钥 URL 是持有者凭据，不是 OAuth 或 ChatGPT 账号身份；持有 URL 的人即可访问该实例的工具。请避免将其写入版本控制和普通日志。HTTPS 隧道在服务商处终止 TLS，不应假设服务商无法看到明文。客户端确认仍由客户端决定，KT 不会绕过这些确认。

## 参阅

- [MCP 客户端配置](mcp.md)：将外部 MCP 工具接入 Creature。
- [Creature 配置](creatures.md)：定义本地委派目标。
- [子代理](sub-agents.md)：子代理能力与配置。
- [反向代理部署](deployment-reverse-proxy.md)：维护外部 HTTPS 入口。
