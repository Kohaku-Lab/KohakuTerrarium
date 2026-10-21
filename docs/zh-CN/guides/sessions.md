---
title: 会话与恢复
summary: .kohakutr 会话文件如何工作、如何恢复一个生物，以及如何重放对话历史。
tags:
  - guides
  - session
  - persistence
---

# 会话

写给需要持久化、恢复或归档 Agent 运行的读者。

会话把一次运行的对话、事件、子代理对话、频道历史、便笺、任务记录、可恢复触发器和配置元数据记录在 `.kohakutr` 文件中。恢复会根据这些记录重建生物 (creature)，而不是为运行中的进程创建快照，也不会回滚外部资源。

概念入门：[记忆与压缩](../concepts/modules/memory-and-compaction.md)、
[会话与环境](../concepts/modules/session-and-environment.md)。

### 压缩后的编辑与重新生成

压缩会改变实时提示并写入快速恢复快照，但不会删除追加式事件日志。Studio 使用持久化的 `event_id`、`turn_index` 和 `branch_id` 定位可编辑用户消息。保存并重新运行或重新生成时，会从目标历史构建新分支，只复用来源历史仍兼容的摘要和快照，不复用依赖已修改内容的表示。旧分支及其后续事件仍可切换和恢复。定位缺失、歧义、指向回合中注入输入或与所选分支冲突时，操作会在不修改历史的情况下失败。

`/branch <turn_index> <branch_id>` 选择一条血缘一致的已记录分支，不生成新轮次。它复用兼容的 checkpoint 和摘要，恢复分支坐标与压缩状态，并保存选择供恢复使用。`/branch latest` 沿同一血缘选择最新的**兼容**分支，不是独立取每个轮次的最大分支号。两者均使用现有重建预算与消息保留检查；轮次、压缩或后台任务活动期间拒绝切换。不带参数的 `/branch` 列出候选及当前选择。

#### 旧 checkpoint 的局部补全

兼容的 checkpoint 缺少部分工具调用关系时，不一定要展开整段历史。只有选定前缀和摘要已被证明可复用，重建才会保留它们，并仅补回缺失的完整 canonical assistant 轮次及其唯一归属的已完成结果。补全不会重新执行工具，也不会把补回的原件宣称为已被摘要消费。旧消费记录不完整时，摘要与这些可追踪的补充可能存在少量事实重复，因此不保证恢复后消息数零增长。

以下情况会拒绝该局部补全候选：

- 来源血缘与所选分支或目标边界冲突；
- 轮次归属、调用／job 身份或已完成结果存在歧义；
- 原始 canonical 轮次或必需结果缺失；
- 无法整轮恢复而不重复已有调用、不复活明确已覆盖或已清除的内容，或不违反目标／顺序边界。

回退 raw 重建前，可以尝试其他已证明适用的 checkpoint。一旦被拒候选带有清除 cutoff，替代候选就必须保留相同 cutoff，不能通过 raw 回退复活已清除前缀。重建提示在安装前仍须满足原有消息保留上限与配置的上下文预算（`compact.max_tokens`，不是自动压缩触发阈值）。准入失败会抛出 `ConflictError`，不安装该候选、不创建其编辑分支。token 检查是消息文本估算，不是 provider 精确计数。这不是普通续聊或未展开 rerun 的额外门槛，也不保证后续 provider 调用一定成功。

### 多模态编辑、重新生成与分支

历史稳定时，编辑或重新生成一个用户轮次会从事件日志重建其原始文本、图片和文件内容块。重新生成会复用这些内容块；编辑则提供新的内容块列表，并创建同级分支。继续较早的分支时，使用该分支的历史和兼容快照，恢复后也一样。在 Studio 中只修改文字会保留附件；删除或替换附件只影响新分支。

保存的助手消息和已完成工具结果保留结构化内容及调用／结果配对。失败的工具也可能返回诊断媒体或部分文件；保存的输出仍保持结构化，错误和退出状态另行记录。重放已完成结果不会再次执行工具。生成图片的字节不同时会使用不同的产物路径，因此新的同级分支不会覆盖较早的生成图片。已完成的后台结果可供保留其调用的兼容分支使用，但不适用于排除了该调用的分支；保存结果本身不会继续未选中的分支。

内联字节是不可变副本：data URL 和内联文件内容（包括二进制 `FilePart.data_base64`）保留在历史中，因此修改提取文件在工作区中的派生副本不会改变后续重放。二进制文件在保存前不会被简化为展示占位文字。请求准备仍受现有文件读取器和 provider 能力约束；保存字节不代表模型能够读取所有 MIME 类型，展示预览仍会省略 base64 载荷。实时工作区路径（`file://` 等）仍是动态引用，请求准备时会重新从磁盘读取。

生成产物位于所属会话文件旁的 `<session>.artifacts/`（`generated_images/`、`generated_videos/`、`tool_outputs/`）。即使 `KT_SESSION_DIR` 指向不同的默认目录，显式 `--session` 路径、编程接口的 `session=` 或 `Terrarium(session_dir=...)` 仍决定这些产物的归属。发出的请求在 provider 边界将保存的产物 URL 解析为 data URL；对话快照保留简短的会话相对引用。只解析 provider 本次选用内容中的本地图片：被现有请求预算排除的图片，或属于该 provider 不会作为图片发送的角色的图片，不必为本次请求保持可读。

本版本的已知限制：

- Studio 拒绝行内编辑包含文本、图片和文件之外内容块类型的用户消息。
- 包含多个文本块的用户消息仍可重新运行，但 Studio 不会将这些文本改写成单个合并字段。
- 轮次或压缩正在执行、或后台工作尚未完成时，实际的历史切换、编辑／重新生成和回退操作会被拒绝。同一分支上的普通输入保留原有排队行为，不会变成历史切换。
- 快照恢复保存的消息和引用，而不是缺失的产物文件、进程或外部副作用。请将会话文件及其产物目录一起归档。本次必需的本地图片缺失时，请求准备会失败，而不是静默省略；不保证远程 URL 的可用性。
- 这些保证涵盖框架现有的文本／图片／文件表示和所选 provider 的能力，不承诺无损视觉摘要或跨 provider 历史迁移。

### 稳定多模态历史与压缩、checkpoint

编辑未压缩的尾部时，可以复用来源输入未变的摘要。编辑被该摘要消费过的轮次时，则会在不使用依赖该轮次的摘要的情况下重建目标前缀。返回较早分支或恢复其兼容 checkpoint 时，使用该分支的有效上下文，包括保留的媒体和调用／结果关系。仅为恢复历史，不会重新执行原工具或摘要过程。

压缩向摘要模型发送文本投影，而不是把原始图片交给模型做视觉分析。已被所选摘要替代的媒体，不必重新出现在后续 provider 请求中，但原始事件和仍可用的产物文件仍保留在保存的历史中。摘要未消费的后来工具结果，不会仅因其调用来自祖先轮次就一并被覆盖；仍适用既有来源记录和上下文省略规则。

checkpoint 可能已包含经过省略的工具输出，而原始历史重建可能在重新应用当前省略策略前恢复该输出的完整表示。这些表示不必是完全相同的字典。应在相同摘要、用户输入保留和省略策略下比较有效上下文；合法的完整表示恢复，不等于重新带回已被所选摘要消费的媒体。本地媒体只针对 provider 最终有效输入检查可用性，因此排除的媒体不会阻断续聊，必需媒体缺失仍显式失败。这些规则适用于稳定历史，不包含压缩或后台工作仍在运行时的历史操作。

摘要在捕获时绑定 canonical 轮次与工具结果。复用时排除保留的尾部以及被重新接上的用户消息，因此摘要只覆盖它实际替代的轮次。若 canonical 历史无法无歧义地绑定，压缩会以 `capture_unproven` 跳过：不调用摘要模型，也不改动实时对话。输入来源与结构上的替换覆盖分开处理：经过证明的兼容 completion 可以将覆盖传递给下一次压缩，新摘要的来源仍保持未知，而不是变成通用的原始重放替换。局部补全的轮次也使用相同的 canonical 身份与顺序规则，以便继续压缩。新消息会记录框架生成的任务状态前缀，捕获时按生产者的相同组装规则核对完整内容；后台工作结算后无需关闭默认任务提示即可压缩。

## `.kohakutr` 文件

`.kohakutr` 是一个 SQLite 数据库（经 KohakuVault），有九张表：

| 表 | 用途 |
|---|---|
| `meta` | 会话元数据、配置快照、terrarium 拓扑 |
| `state` | 每个 Agent 的便笺、轮次计数、累计 token 用量、可恢复触发器 |
| `events` | 追加式日志：每个文本块、工具调用、触发、token 用量事件 |
| `channels` | 按频道名索引的频道消息历史 |
| `subagents` | 按父 Agent + 名称 + 运行编号索引的子代理对话快照 |
| `jobs` | 工具与子代理的任务记录 |
| `conversation` | 每个 Agent 最新的对话快照（用于快速恢复） |
| `fts` | 事件上的 FTS5 索引（供 `kt search`） |
| `vectors` | 可选的嵌入列（由 `kt embedding` 填充） |

事件数据是追加式的，版本管理走 KohakuVault 的自动打包。二进制产物
还可以放在同级的 `<session>.artifacts/` 目录里：一次运行生成过图片
或其他二进制输出时，请把 `.kohakutr` 文件和它的 artifacts 目录一起
归档。

## 会话放在哪

```
~/.kohakuterrarium/sessions/<name>.kohakutr
```

`<name>` 由生物/terrarium 名加时间戳自动生成。`KT_SESSION_DIR` 会统一调整 CLI、Studio 和 web API 的根目录；单次运行显式指定的 `--session <path>` 优先级更高。生成媒体存放在所属运行的会话文件旁，包括自定义 `session=` 路径或 `Terrarium(session_dir=...)` 指定的目录；两者不同时，不会改存到全局 `KT_SESSION_DIR` 下。目录名称固定为：provider 生成图片使用 `generated_images/`，视频使用 `generated_videos/`，普通工具的二进制输出仍使用 `tool_outputs/`。历史产物路径继续可读且不会迁移。删除会话数据不会删除保留的产物；用 `--no-session` 可以关闭会话持久化。

### 运行时存储边界

进程运行期间，请保持会话存储目录及其祖先目录的位置不变。运行时删除、重命名或
移动这些目录属于未定义行为，由调用方负责；通过 Web 文件管理、Lab、编辑器、
shell 命令或外部程序进行的操作也不例外。框架不会增加文件系统监控或权限门禁
来强制执行这一要求。

同样，通过通用文件 API、写入／编辑工具或直接数据库访问，编辑、替换、删除或
重命名 `.kohakutr` 数据库及相关 SQLite 文件（例如 `-wal`、`-shm`），不属于
受支持的运行时维护操作。正常的记录、Memory 更新、迁移、fork 和会话删除应使用
会话 API。与会话存储无关的普通项目文件编辑不受影响。

下文的元数据读取协调仅覆盖单机单进程内受支持的框架操作，不协调外部进程或
绕过这些操作的任意自定义代码，也不重新设计既有遗留读写路径之间的并发关系。

### 从源码 checkout 检查离线会话

在依赖可用的源码 checkout 中执行：

```bash
python scripts/inspect_session.py /path/to/saved.kohakutr
python scripts/inspect_session.py /path/to/saved.kohakutr --all --search "query"
```

检查脚本拥有一个 `SessionStore.open_readonly()` 句柄，只关闭自己的句柄；输出
失败或中断时也会执行关闭。它不改变会话状态或最近活跃时间，也不在关闭时持久化
重建的事件计数器。它沿用现有 Store 资源登记：同进程内的快读会延迟其打开，源资源
释放不确定时则阻止打开。

这是**业务只读**，不是 SQLite `mode=ro`：native 表初始化和 SQLite 协调仍可能
修改 schema 或文件字节。请对离线会话使用该脚本，不要与另一个写入同一文件的进程
同时使用。它既不借用也不终结运行中 owner 的 Store，也不增加跨进程协调。

摘要计数枚举 key，不解码事件／频道／子代理的 payload；显式详情和搜索选项仍会
读取内容。打开没有持久化事件计数器的旧会话时，Store 构造阶段仍可能扫描事件值。
仅按 key 统计摘要的优化并未消除这项独立的遗留启动成本。

### 已保存 Conversation 的统计按需加载

打开或刷新已保存会话查看器的 **Conversation** 标签时，会加载历史索引、树和历史页，
不主动计算统计摘要。索引中的 `viewer_agents` 按**同一个 Store** 未筛选摘要的默认顺序
列出主代理和 attached 代理，不代表集群合并摘要的完整目录。历史索引仍只对应一个 Store；
`targets` 保持原有合法 Conversation 目标（含频道），发现 attached
命名空间不会自动把它变成新的 Conversation 目标。

切换到 Trace、Overview 或 Cost 时，才按需请求当前摘要；同一数据刷新代次复用已成功
或正在进行的摘要请求。除非用户明确选择代理，Trace 仍等待未筛选摘要确定默认选择。
Live Inspector 即使停留在 Conversation，也保持摘要和 header 总量刷新。仅枚举 key
的代理发现不会消除旧会话缺失持久化计数器时的 Store 初始化扫描。

## 持久化哪些东西

每个轮次 KohakuTerrarium 都会记录：

- **对话快照**：经 msgpack 的原始消息 dict。保留 `tool_calls`、
  多模态内容和元数据。
- **事件日志**：每个文本块、工具调用、子代理输出、触发器触发、
  频道消息、压缩、中断或错误各一条。这是权威历史。
- **子代理对话**：在子代理销毁之前保存，事后可以检查它干了什么。
- **便笺与频道消息**：按 Agent、按频道各自记录。
- **任务记录**：长时间运行的工具与子代理的输出。
- **可恢复触发器**：任何 `resumable: True` 的 `BaseTrigger` 子类
  序列化进 `state`，恢复时还原。
- **配置快照**：运行时完全解析后的配置，磁盘上的配置变了也能
  重建 Agent。
- **二进制产物**：生成的图片等二进制输出写在会话文件旁的
  `<session>.artifacts/` 下。

## 恢复

### Web 恢复预检的元数据读取

`POST /api/sessions/{name}/resume/preflight` 会在其**本地元数据读取后段**自动
尝试选择性只读查询，不需要配置开关或请求参数。查询可见已提交的 SQLite WAL
数据，将元数据行读入内存并释放源资源后，再由私有内存解码器解码。它不会对源文件
执行迁移、checkpoint 或业务状态更新。

Best effort 指的是**受准入控制的优化**，而不是静默恢复错误：

- 若相关已登记遗留资源（例如运行中的 Store 或 Memory 索引）占用源文件，或另一个
  快读先获得准入，本地读取会使用既有的复制路径。在作出此选择前，前置路径解析
  阶段本身也可能等待正在执行的快读。
- 候选源 SQL 一旦开始，读取或解码失败就会报告错误，不再通过复制重试。
  本地候选 SQL／解码失败返回 HTTP 500，
  `detail.code = session_metadata_read_failed`。
- 若无法确认源资源已释放，进程内针对该文件身份的相关已登记访问将继续被阻止。
  预检的资源错误映射返回 HTTP 503，
  `detail.code = session_metadata_resource_unavailable`；后续请求在路径解析
  阶段遇到该状态时也如此。这不是应盲目重试的普通忙碌响应。请停止相关工作，
  等待 worker 完成，然后正常关闭／重启进程；重启不等于数据库修复。源句柄或
  worker 可能仍存活时，不要手动清除占用状态。
- 已确认源资源释放后发生的私有解码器失败，不会隔离源文件。

这项优化不扩展到共用分类或版本探测读取、远端／集群预检分支、历史浏览、其他
元数据消费者或实际恢复操作，也不提供跨进程安全保证。上述
[运行时存储边界](#运行时存储边界)是受支持运行条件的一部分。

### CLI 恢复

```bash
kt resume --last            # most recent session
kt resume                   # interactive picker (10 most-recent shown)
kt resume my-agent_20240101 # by name prefix
kt resume ~/backup/run.kohakutr
```

恢复类型自动检测：Agent 会话挂载单个生物；terrarium 会话挂载完整
连线并强制 TUI 模式。

参数与 `kt run` 相同：`--mode`、`--llm`、`--log-level`，外加
`--pwd <dir>` 覆盖工作目录。

编程式恢复与之对应（见[在 Python 里使用会话](#在-python-里使用会话)）：

```python
from kohakuterrarium import Terrarium

# Fresh engine from a saved session...
engine = await Terrarium.resume("runs/swe_20240101.kohakutr", pwd="/work", llm=None)

# ...or adopt into an engine that's already running other graphs.
graph_id = await engine.adopt_session("runs/other.kohakutr")
```

两者都接受路径或 `SessionStore`；`llm=` 是可选的选择器字符串覆盖。
文件存在但无法恢复（已保存会话类型未知、元数据缺少配置路径）会抛
`ValueError`。

恢复做了什么：

1. 从 `meta` 读配置快照。
2. 重新加载磁盘上的当前配置（你后来改的提示词/工具会生效）。
3. 合并：配置快照提供会话身份；当前配置提供运行逻辑。
4. 重建 Agent，挂上同一个 `SessionStore`，重注入对话快照，
   回放便笺/频道/触发器状态。
5. 控制器从头启动；之前的事件已在上下文中。

也就是说，小的配置漂移没问题（换个 LLM、改个提示词）。结构性漂移
（重命名生物、删掉它正在用的工具）可能导致回放错误；需要完美保真
时，把会话钉死在它原来的配置上。

## Web UI 中的开放对话

Conversations Rail 只显示当前仍附加 runtime 的对话。持久化对话的生命周期仍是独立维度：

- **在线且开放：** runtime 已附加，因此对话显示在 Rail 中。
- **休眠且开放：** 当前没有附加 runtime。保存的对话仍可从 Sessions 访问，但不会渲染在 Rail 中。
- **已结束：** 用户显式结束对话。保存的历史仍可在 Sessions 中查看。

关闭 Chat 或 Inspector 标签页只会 detach 该视图，不会停止或结束对话，因此仍在线的 runtime 会保留在 Rail 中。后端将已停止或断线的 Creature 报告为非活跃后，对应行会在下一次刷新时消失。Sessions 历史页继续保持只读，并沿用现有的 **View** 与 **Resume** 操作。

每个新持久化的对话都有稳定的 `conversation_id`。runtime graph ID 在重启或恢复后可以变化，文件也可以移动或重命名，但这些变化不会产生重复记录。`GET /api/sessions/open` 使用该 identity 聚合在线 runtime 与带开放 marker 的已保存 session，并让在线行优先；Rail 只渲染 `is_live: true` 的行。Session index 与查询按已认证用户的 session directory 隔离，因此一个用户的索引不会返回另一个用户的数据。

保存的生命周期 marker 是显式的。引入 marker 之前创建的旧 session 仍可在历史中访问。普通 runtime Stop 保留开放 marker，并记录 paused/dormant 状态；显式 **End** 清除 marker 并记录 terminal 状态。本地、远程与 cluster 操作都会在 Rail 刷新前同步保存文件中的 marker。

Resume 按保存对话执行 singleflight：两个浏览器同时请求时只执行一次服务端恢复；取消一个等待者不会取消共享恢复，失败后仍可重试。Cluster resume 采用 all-or-error 语义：任何成员恢复失败或保存的连接无法重建时，服务端会补偿已恢复成员、清理 partial runtime metadata、保留原始保存生命周期，并返回非成功响应，而不是留下 degraded partial cluster。

## 中断与恢复工作流

```bash
kt run @kt-biome/creatures/swe
# work... then Ctrl+C twice while idle (or Ctrl+D / /exit)
# later:
kt resume --last
```

在 Rich CLI 模式下，Ctrl+C 中断当前轮次；空闲时按两次 Ctrl+C
（或 Ctrl+D / `/exit`）会优雅退出、落盘会话存储并打印恢复提示。
强制杀死（SIGKILL）会跳过最后的落盘，但得益于追加式写入，最近的
状态大多已经在磁盘上了。

## 复制或归档会话

```bash
# Backup
cp ~/.kohakuterrarium/sessions/swe_20240101.kohakutr ~/backups/
cp -r ~/.kohakuterrarium/sessions/swe_20240101.artifacts ~/backups/   # if present

# Resume from a moved location
kt resume ~/backups/swe_20240101.kohakutr
```

不想恢复、只想看看，就用 `SessionReader`（见下）：它以只读方式
打开文件，检视永远不会碰 `status` 或 `last_active`。

## 在 Python 里使用会话

### 创建会话：引擎持有的持久化

持久化是引擎上的一个关键字参数；会话元数据由框架自己写入并校验：

```python
from kohakuterrarium import Terrarium

# Autosession: every graph gets <session_dir>/<graph_id>.kohakutr
# automatically (merge/split children land there too).
engine = Terrarium(session_dir="runs/")

# Per-creature control via session=:
c = await engine.add_creature(
    "@kt-biome/creatures/general",
    session="runs/student-42.kohakutr",   # mint the store at this exact path
)
# session=True   -> mint in the default session dir
# session=False  -> no persistence, even under autosession
# session=<SessionStore> -> attach an existing store as-is
# session=None (default) -> follow the engine (autosession / graph store / off)

# Recipes: one terrarium-typed store for the whole graph.
await engine.apply_recipe("@kt-biome/terrariums/swe_team", session="runs/team.kohakutr")
```

`await engine.shutdown()`（或离开 `async with` 块）会关闭引擎创建的
所有存储，文件不再卡在 `status: "running"`。

### 恢复

- `await Terrarium.resume(store_or_path, *, pwd=None, llm=None)`：
  起一个新引擎并接管已保存的会话。
- `await engine.adopt_session(store_or_path, *, pwd=None, llm=None)`：
  恢复进运行中的引擎；返回新的 `graph_id`。

恢复根据会话元数据里记录的配置路径（包括 `@pkg` 引用）重建拓扑，
并使用每个 Agent 各自的工作目录，不会对你的进程 `os.chdir`。
恢复会在创建 writer store、runtime、lifecycle 或 adoption 之前执行只读
工作目录预检。目录失效时必须选择新目录、只看历史或取消，不会静默回退到
KohakuTerrarium 进程目录。使用 `workspace_overrides={"creature-id":
"/new/path"}` 只替换确认的成员；共享失效路径也可以使用预检返回的
`gap_id` 成组替换。标量 `pwd=` 仅保留为显式全队兼容覆盖，不能与
`workspace_overrides` 同时使用。成功替换会永久写回 manifest；远程和集群
会在实际 worker 上校验路径，并在第一次 adoption 前完成全成员预检。集群
API 还可按成员会话 ID 限定替换，让不同 worker 上相同的 creature 或路径组
目标选择不同目录。若回滚无法持久化，会话会标记为 `partial_dirty`；后续预检
和恢复会失败关闭，直到会话修复。

### 读取：`SessionReader`

`SessionReader` 是 `.kohakutr` 文件的只读检视接口。它经
`SessionStore.open_readonly` 打开，读取绝不会更新 `last_active` 或
改写 `status`：

```python
from kohakuterrarium import SessionReader

with SessionReader("~/backups/swe_20240101.kohakutr") as r:
    print(r.meta["status"], r.agents)

    for turn in r.turns():               # live-branch turns, reassembled
        tools = [tc["name"] for tc in turn.tool_calls]
        print(f"[{turn.source}] {turn.user_text!r} -> "
              f"{turn.assistant_text[:60]!r} tools={tools}")

    events = r.events()                  # the raw append-only log
    convo = r.conversation()             # final snapshot (message dicts)
    chan = r.channel_messages("tasks")   # one channel's history

    r.index()                            # ad-hoc FTS index, then:
    hits = r.search("score.json", k=5)
```

`turns()` 会跳过重生成 / 编辑产生的兄弟分支：你看到的就是所有查看
器展示的那条活动分支。`search()` 只对已建索引的会话返回结果
（`kt embedding`，或临时用 `reader.index()` 建 FTS）。

需要原始读写访问时，`SessionStore(path)` 还在，但任何列表 / 预览 /
查看器用途都应该用 `SessionStore.open_readonly(path)`（或干脆用
`SessionReader`）：普通的打开 + 关闭会把会话标记成已暂停并更新
`last_active`，破坏按最近使用排序。

## 压缩

上下文快满时，压缩会收缩对话。按生物配置：

```yaml
compact:
  enabled: true
  threshold: 0.8              # compact when context hits 80% of window
  target: 0.5                 # aim for 50% after compaction
  keep_recent_turns: 5        # always preserve the last N turns verbatim
  compact_model: gpt-4o-mini  # cheaper model for the summarization pass
```

压缩在后台运行（见 [concepts/modules/memory-and-compaction](../concepts/modules/memory-and-compaction.md)）：
控制器继续干活；新摘要就绪后，对话被换入。每次压缩都记成一个事件。

手动压缩：

```
/compact
```

在 CLI/TUI 提示符下输入。把长会话交接出去、或当作上下文喂给另一次
运行之前，先压一下很有用。

## 列出与搜索会话

`kt serve` 的 Web UI 和 `GET /api/sessions` 由一个旁路索引支撑：
`<session_dir>/.kt-index.kvault` 这一个 SQLite 文件缓存每个会话的
列表型元数据（名称、状态、最后活跃时间、Agent、预览等），并对文本
列提供 BM25 搜索。你不会直接和它打交道；它跨服务器重启保持一致，
服务器停机时用 `kt run` 起的会话也能对上。

`GET /api/sessions` 的查询参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `limit` | `20` | 页大小 |
| `offset` | `0` | 页偏移 |
| `search` | `""` | 对 `name` / `preview` / `config_path` / `agents` / `pwd` 的 FTS5 查询 |
| `sort` | `last_active` | `last_active` \| `created_at` \| `name` \| `status` \| `relevance` |
| `order` | `desc` | `desc` \| `asc` |
| `status` | （无） | 精确匹配（`running`、`paused` 等） |
| `config_type` | （无） | 精确匹配（`agent`、`terrarium`） |
| `node_id` | （无） | 精确匹配，按运行该会话的 lab 节点过滤 |
| `refresh` | `false` | 列表前做增量对账，只重读 `(mtime, size)` 变了的文件 |
| `full_rescan` | `false` | 强制重读每个文件（手动改过磁盘上的 `.kohakutr` 之后用） |

`sort=relevance` 只有设置了 `search` 时才有意义；用其他排序时，先
收集 FTS 命中集，再按指定字段排序。

索引如何在不手动刷新的情况下保持同步：

- **推送。**API 服务器运行期间，它持有的每个 `SessionStore` 都按
  防抖节奏（每 20 个事件或 5 秒，先到为准）把更新推进索引，正常
  使用时索引不会落后。
- **启动对账。**每次服务器启动，索引对会话目录做一次指纹差分，只
  重读变过的文件。首次启动会完整读取每个文件（*bootstrap* 步骤）
  并记住成功过。
- **`?refresh=true`。**按需触发同样的增量对账，刚把备份的
  `.kohakutr` 复制进会话目录之后特别有用。

旁路索引可以放心删除：下次列表会从 `.kohakutr` 文件重建它。索引里
没有任何唯一状态。

不经 HTTP 层的编程式列表：

```python
from kohakuterrarium.studio.persistence.session_index import (
    get_session_index_default,
)

index = get_session_index_default()
page = index.list(search="auth bug", sort="relevance", limit=10)
for row in page.rows:
    print(row["name"], row["last_active"], row["preview"])
```

## 记忆搜索

会话同时也是可搜索的知识库。建好索引之后：

```bash
kt embedding ~/.kohakuterrarium/sessions/swe.kohakutr
kt search swe "auth bug"
```

Agent 自己可以用 `search_memory` 工具搜索。完整讲解：[记忆](memory.md)。

## 禁用持久化

有时你只想要一次性的运行：

```bash
kt run @kt-biome/creatures/swe --no-session
```

不创建 `.kohakutr`。这同时会让压缩失去从磁盘找回早先轮次的能力
（内存内照常压缩）。

## 故障排查

- **压缩跑不完 / OOM。**压缩模型沿用了重型的控制器模型。把
  `compact_model` 设成便宜的（`gpt-4o-mini`、`claude-haiku`）。
- **恢复报 `tool not registered`。**生物配置变了（某个工具被删），
  但对话还引用它。手动改 `config.yaml` 把工具加回来，或开新会话。
- **`kt resume` 找不到我刚见过的会话。**会话按前缀对
  `~/.kohakuterrarium/sessions/` 下的文件名解析。重命名或移动过的
  文件请传完整路径。
- **复制会话后生成的图片不见了。**同级的 `<session>.artifacts/`
  目录也要一起复制，不能只拷 `.kohakutr`。
- **`.kohakutr` 文件很大。**事件日志是追加式的；长会话会增长。
  归档旧会话，或把工作拆成多个会话。压缩只收缩活动对话，完整事件
  历史会保留供搜索。
- **恢复后子代理输出缺失。**子代理对话在子代理完成时保存。父级在
  子代理中途被打断时，最新快照就是上一个检查点持久化的内容。

## 另请参阅

- [记忆](memory.md)：对会话历史的 FTS、语义和混合搜索。
- [配置](configuration.md)：压缩配方和会话相关参数。
- [编程式用法](programmatic-usage.md)：用 Python 驱动 Agent 和引擎。
- [参考 / Python API](../reference/python.md#会话)：`SessionReader` / `SessionStore` 签名。
- [概念 / 记忆与压缩](../concepts/modules/memory-and-compaction.md)：压缩的工作方式。
