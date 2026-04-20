---
title: 依赖图
summary: 模块导入方向的不变量，以及用于强制验证这些规则的测试。
tags:
  - dev
  - internals
  - architecture
---

# 依赖规则

这个包有严格的单向导入规范。规则通过约定维持，并由
`scripts/dep_graph.py` 验证。目前运行时依赖图没有循环；请继续保持。

## 一句话说清规则

`utils/` 是叶子节点，所有内容都可以导入它；它自身不从框架导入任何内容。`modules/` 只放协议。`core/` 是运行时本体——它会导入 `modules/` 和 `utils/`，但 **绝不** 导入 `builtins/`、`terrarium/` 或 `bootstrap/`。`bootstrap/` 和 `builtins/` 会导入 `core/` + `modules/`。`terrarium/` 和 `serving/` 会导入 `core/` + `bootstrap/`。`cli/` 和 `api/` 位于 `serving/` + `terrarium/` 之上。

## 分层

从叶子节点（底部）到传输层（顶部）：

```
  cli/, api/                    <- 传输层
  serving/, terrarium/          <- 协调层
  bootstrap/, builtins/         <- 装配 + 实现
  core/                         <- 运行时引擎
  modules/                      <- 协议（以及一些基类）
  parsing/, prompt/, llm/, …    <- 支撑包
  testing/                      <- 依赖整个栈，仅用于测试
  utils/                        <- 叶子节点
```

各层细节：

- **`utils/`** —— 日志、异步辅助工具、文件保护。不得导入任何框架内容。在这里加入框架导入几乎一定是错误的。
- **`modules/`** —— 协议与基类定义，例如 `BaseTool`、`BaseOutputModule`、`BaseTrigger` 等。这里不放实现，因此上层任何模块都可以依赖它们。
- **`core/`** —— `Agent`、`Controller`、`Executor`、`Conversation`、`Environment`、`Session`、频道、事件、registry，也就是运行时本体。`core/` 绝不能导入 `terrarium/`、`builtins/`、`bootstrap/`、`serving/`、`cli/` 或 `api/`，否则会重新引入循环。
- **`bootstrap/`** —— 从配置构建 `core/` 组件的工厂函数（LLM、工具、IO、子 Agent、触发器）。它会导入 `core/` 和 `builtins/`。
- **`builtins/`** —— 具体的工具、子 Agent、输入、输出、TUI、用户命令。内部 catalog（`tool_catalog`、`subagent_catalog`）是带延迟加载器的叶模块。
- **`terrarium/`** —— 多 Agent 运行时。导入 `core/`、`bootstrap/`、`builtins/`，但这些模块都不会反向导入 `terrarium/`。
- **`serving/`** —— `KohakuManager`、`AgentSession`。依赖 `core/` 和 `terrarium/`，与传输方式无关。
- **`cli/`、`api/`** —— 最上层。前者是 argparse 入口点，后者是 FastAPI 应用。两者都依赖 `serving/`。

请参阅 [`src/kohakuterrarium/README.md`](../../src/kohakuterrarium/README.md)，其中的 ASCII 依赖流程图是唯一可信来源。

## 为什么需要这些规则

这些规则服务于三个目标：

1. **没有循环。** 循环会导致初始化顺序脆弱、部分导入错误，以及启动时容易出问题的导入期副作用。
2. **可测试性。** 如果 `core/` 永远不导入 `terrarium/`，你就可以在不启动多 Agent 运行时的情况下对 controller 做单元测试。如果 `modules/` 只放协议，也能很容易替换实现。
3. **清晰的变更影响面。** 修改 `utils/` 时，所有内容都会重建；修改 `cli/` 时，其他部分都不会。分层让你能够预估一次改动的影响范围。

历史注记：以前曾存在一个循环 `builtins.tools.registry → terrarium.runtime → core.agent → builtins.tools.registry`。后来通过引入带延迟加载器的叶模块 `tool_catalog` 将其拆解。详情请参见 git 历史中 [`internals.md`](internals.md) 的 legacy notes 部分。现在只剩两个合理的 lazy import：`core/__init__.py` 使用 `__getattr__` 避免 `core.agent` 的初始化顺序问题，而 `terrarium/tool_registration.py` 会将 terrarium-tool registration 延后到首次查询时才执行。

## 工具：`scripts/dep_graph.py`

这是一个静态 AST 分析器。它会遍历 `src/kohakuterrarium/` 下的每个 `.py`，解析 `import` / `from ... import`，并将每条边分类为：

- **runtime** —— 模块加载时在顶层执行的导入。
- **TYPE_CHECKING** —— 受 `if TYPE_CHECKING:` 保护，不会进入运行时图。
- **lazy** —— 函数内部导入，不会进入运行时图。

只有 runtime 边会计入循环检测。

### 命令

```bash
# Summary stats + cross-group edge counts (default)
python scripts/dep_graph.py

# Runtime SCC cycle detection
python scripts/dep_graph.py --cycles

# Graphviz DOT output (pipe into `dot -Tsvg`)
python scripts/dep_graph.py --dot > deps.dot

# Render a matplotlib group + module plot into plans/
python scripts/dep_graph.py --plot

# All of the above
python scripts/dep_graph.py --all
```

关键输出：

- **Top fan-out** —— 导入其他模块最多的模块，通常会是装配代码（`bootstrap/`、`core/agent.py`）。
- **Top fan-in** —— 被导入次数最多的模块，通常应以 `utils/`、`modules/base`、`core/events.py` 为主。
- **Cross-group edges** —— 类似柱状图的读数，表示有多少条边跨越包边界。如果出现新的 `core/` → `terrarium/` 边，请调查原因。
- **SCCs** —— 应该始终为空。如果 Tarjan 算法找到了非平凡 SCC，说明运行时图中存在循环。

`--plot` 标志会输出 `plans/dep-graph.png`（组级别、环形布局）和 `plans/dep-graph-detailed.png`（模块级别、同心圆布局）。当重构重新整理依赖边时，这两张图都很适合用于 PR 审查。

### 什么时候该运行

- 在新增子包的 PR 之前。
- 当你怀疑存在循环导入时（症状：启动时出现提到 partially initialized module 的 `ImportError`）。
- 大型重构之后，作为健康检查。

运行 `python scripts/dep_graph.py --cycles`，并确认输出为：

```
None found. The runtime import graph is acyclic.
```

如果不是，请先修复循环再合并。

## 新增包

先选对层级。问自己：

- **它有运行时行为，还是只有基类 / 协议？** 协议放在 `modules/`；运行时行为放在 `core/` 或专用子包。
- **它需要 `core.Agent` 吗？** 如果需要，它就位于 `core/` 之上，而不是里面。
- **它是内建项（随 KT 一起发布）还是扩展？** 内建项放在 `builtins/`；扩展放在独立包中，并通过 package manifest 接入。

然后遵守该层的导入规则：

- `utils/` 不导入任何框架侧内容。
- `modules/` 可以导入 `utils/` 和核心类型，其余不行。
- `core/` 可以导入 `modules/`、`utils/`、`llm/`、`parsing/`、`prompt/`，绝不能导入 `terrarium/`、`serving/`、`builtins/`、`bootstrap/`。
- `bootstrap/` 和 `builtins/` 会导入 `core/` + `modules/`。
- 其他一切都位于其上。

如果一条新边看起来很别扭，那它大概率确实有问题。请引入一个叶辅助模块（例如 `tool_catalog`）来拆掉循环，而不是靠函数内部导入硬撑。函数内部导入并不鼓励（见 CLAUDE.md 的 Import Rules），它是最后手段，不是首选方案。

## 另见

- [CLAUDE.md 的 Import Rules](../../CLAUDE.md) —— 这套规范强制执行的约定。
- [`src/kohakuterrarium/README.md`](../../src/kohakuterrarium/README.md) —— 正典 ASCII 流程图。
- [框架内部机制](internals.md) —— 按流程说明各子包用途的地图。
