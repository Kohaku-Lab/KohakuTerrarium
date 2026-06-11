---
title: Studio
summary: Terrarium 引擎之上的管理层：目录、身份、会话、持久化、挂载策略与编辑器。
tags:
  - concepts
  - studio
  - architecture
---

# Studio

## 它是什么

**Studio** 是 `Terrarium` 运行时引擎之上的管理层。它不是 UI，也不是
又一个 Agent。它是一层共享的 Python 接口，承担每个 UI 和自动化脚本
原本都得各自重写的那些事：

- 包与内置组件的**目录**查询；
- LLM profile、API key、MCP、UI 偏好等**身份**状态；
- 基于 `Terrarium` 引擎的活动**会话生命周期**；
- 已保存会话的**持久化**：列表、恢复、fork、历史、导出；
- 实时**挂载策略**：IO 聊天、频道观察、trace、日志、工作区文件、pty；
- Studio **编辑器**：工作区的生物 (creature) / 模块 CRUD 与脚手架。

Python 门面是 `kohakuterrarium.Studio`。HTTP API、Web UI、`kt` 命令
和你自己的代码都应该委托给同一套 Studio 操作，而不是各自复制
目录/会话/设置逻辑。

## 层级栈

按三个编程门面来想：

| 门面 | 层 | 掌管 |
|---|---|---|
| `Agent` / 生物内部 | 生物 | 一个 LLM 控制器及其工具、触发器、子代理、插件、记忆、I/O。 |
| `Terrarium` | 运行时引擎 | 活动生物、图拓扑、频道、输出连线、热插拔、引擎事件。 |
| `Studio` | 管理层 | 目录、身份、活动会话、已保存会话、挂载策略、编辑器工作流。 |

下层不导入上层：

- 生物代码不知道 `Terrarium` 或 `Studio` 的存在。
- `Terrarium` 托管生物，但不知道 `Studio`、HTTP 或 CLI。
- `Studio` 包住一个 `Terrarium` 引擎（传 `engine=`，或让 `Studio()`
  自己持有一个），在其上叠加管理语义。它的状态以实例为作用域：
  两个 Studio 包两个引擎，会话注册表绝不共享。
- `api/`、`cli/` 和前端是 Studio 上的适配器。Studio 自己抛类型化的
  `kohakuterrarium.errors` 异常；只有 `api/` 适配器把它们翻译成
  HTTP 状态码。

结构就是：一个运行时引擎、一个管理层、几层薄薄的 UI 适配器。

## 为什么需要 Studio

Studio 出现之前，同样的职责散落在多个地方：

- 包列表同时活在 `kt list` 和 Web 路由里；
- profile/key/MCP 逻辑分散在 `kt config`、`kt model`、`kt login` 和
  `/api/settings`；
- 活动 agent 路由与 terrarium 路由各有一份重复的生命周期逻辑；
- 已保存会话的查看/导出/对比/恢复代码与运行时会话创建彼此独立；
- WebSocket 的聊天/日志/文件/终端端点各自维护一套挂载策略。

Studio 把这些收敛成每个关注点一份实现。CLI 输出终端形状的内容，
HTTP API 序列化 JSON，前端渲染面板，但干活的都是 Studio。

## Studio 的会话 vs Terrarium 的图

`Terrarium` 掌管的是**图**：活动生物的连通分量。单个生物是一张图，
多生物团队也是一张图。连接两张图会合并；断开可能拆分。

当用户或 UI 在管理一张图时，Studio 把它叫作**会话**。会话句柄携带：

- `session_id`：即图 id；
- `kind`：单生物图是 `"creature"`，由配方启动的多生物图是
  `"terrarium"`；
- 供 UI 标签页和生物级操作使用的生物摘要；
- Studio 关心的元数据，如配置路径、工作目录、创建时间。

这就是为什么公开的活动会话 API 用
`/api/sessions/{sid}/creatures/{cid}/...` 这样的 URL：生物操作总是
以拥有它的图/会话为作用域。

已保存会话则不同：它们是磁盘上的 `.kohakutr` 文件。Studio 的
persistence 可以列出它们、把它们恢复进运行中的引擎、fork 它们，
以及构建事后的查看器载荷。

## 挂载策略

不是每个生物都是聊天机器人。监控器可能没有用户输入；调度器可能只
产日志；多 Agent 团队需要的可能是频道观察器而不是聊天框。Studio 把
**运行**一个生物和把 UI **挂载**到它上面分开。

挂载策略回答的是：“对这个运行中的生物或会话，哪种实时视图 / 控制面
说得通？”

| 策略 | 形态 | 用途 |
|---|---|---|
| IO 聊天 | 读/写流 | 对话型生物。 |
| 频道观察 | 只读流 | 不打扰监听者地检视图频道流量。 |
| Trace | 只读流 | 引擎事件、轮次、拓扑变化、工具活动。 |
| 日志 | 只读流 | 进程/运行时日志。 |
| 工作区文件 | 浏览/监视 | 文件面板与编辑器刷新。 |
| PTY | 读/写终端 | 挂到生物工作目录的 Shell。 |

Web 仪表盘通过 HTTP/WebSocket 适配器暴露这些。`Studio.attach` 命名
空间目前提供可用策略的通告；更多编程式流辅助方法可以加在那里，
不需要改运行时引擎。

## 别把 Studio 和 Web 仪表盘混为一谈

Web 仪表盘是 UI。Studio 是仪表盘所调用的 Python 管理层。不起 Web
服务器也能用 Studio：

```python
from kohakuterrarium import Studio

async with Studio() as studio:
    session = await studio.sessions.start_creature("@kt-biome/creatures/general")
    print(session.session_id)
```

也可以运行 Web 仪表盘，它在同一套 Studio/Terrarium 概念之上挂载
FastAPI 路由和 WebSocket 端点：

```bash
kt web
```

两条路共享同一个心智模型：Studio 管理会话；Terrarium 运行生物。

## 该用哪一层

- 需要对单个生物的模块、事件队列、输出处理器或测试脚手架做完全的
  底层控制时，直接用 **`Agent`**。
- 需要运行时拓扑（添加生物、连接频道、热插拔、观察引擎事件）时，
  用 **`Terrarium`**。
- 构建 UI、服务、自动化或脚本，需要面向用户的管理面（包、设置、
  活动会话、已保存会话、挂载策略、编辑器）时，用 **`Studio`**。

## 另请参阅

- [Terrarium](multi-agent/terrarium.md)：Studio 所包的运行时引擎。
- [编程式用法](../guides/programmatic-usage.md)：如何嵌入 `Studio` 和 `Terrarium`。
- [Studio 指南](../guides/studio.md)：任务导向的示例。
- [Python API](../reference/python.md)：签名与命名空间地图。
