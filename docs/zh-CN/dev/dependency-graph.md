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

`utils/` 是叶子节点，所有内容都可以导入它；它自身不从框架导入任何内容。`modules/` 只放协议。`core/` 是 Creature 运行时——它会导入 `modules/` 和 `utils/`，但 **绝不** 导入 `builtins/`、`terrarium/`、`studio/`、`bootstrap/`、`api/` 或 `cli/`。`bootstrap/` 和 `builtins/` 在 `core/` + `modules/` 之上装配具体组件。`terrarium/` 托管 graph 中的 Creature 并导入 `core/` + `bootstrap/`。`studio/` 位于 `terrarium/` 之上，负责管理策略。`cli/` 与 `api/` 是 `studio/` / `terrarium/` 加启动 glue 的顶层适配器。

## 分层

从叶子节点（底部）到用户/API 层（顶部）：

```text
  cli/, api/                    <- 用户/API 适配器
  studio/                       <- 管理 facade 与策略
  serving/                      <- 启动 helper + 旧版兼容 wrapper
  terrarium/                    <- Creature graph runtime engine
  bootstrap/, builtins/         <- 装配 + 实现
  core/                         <- Creature runtime
  modules/                      <- 协议（以及一些基类）
  parsing/, prompt/, llm/, …    <- 支撑包
  testing/                      <- 依赖整个栈，仅用于测试
  utils/                        <- 叶子节点
```

各层细节：

- **`utils/`** —— 日志、异步辅助工具、文件保护。不得导入任何框架内容。在这里加入框架导入几乎一定是错误的。
- **`modules/`** —— 协议与基类定义，例如 `BaseTool`、`BaseOutputModule`、`BaseTrigger` 等。这里不放实现，因此上层任何模块都可以依赖它们。
- **`core/`** —— `Agent`、`Controller`、`Executor`、`Conversation`、`Environment`、`Session`、频道、事件、registry，也就是 Creature runtime。`core/` 绝不能导入 `terrarium/`、`studio/`、`builtins/`、`bootstrap/`、`serving/`、`cli/` 或 `api/`，否则会重新引入循环。
- **`bootstrap/`** —— 从配置构建 `core/` 组件的工厂函数（LLM、工具、IO、子 Agent、触发器）。它会导入 `core/` 和 `builtins/`。
- **`builtins/`** —— 具体的工具、子 Agent、输入、输出、TUI、用户命令。内部 catalog（`tool_catalog`、`subagent_catalog`）是带延迟加载器的叶模块。
- **`terrarium/`** —— Creature graph runtime。导入 `core/`、`bootstrap/`、`builtins/`，但这些模块都不会反向导入 `terrarium/`。
- **`studio/`** —— catalog、identity、active sessions、saved-session persistence、attach policy 与 editor 的管理 facade。依赖 `terrarium/` 以及更低层。
- **`serving/`** —— Web/desktop launch helper 加旧版兼容 wrapper。新的管理代码应放在 `studio/`。
- **`cli/`、`api/`** —— 最上层。前者是 argparse 入口点，后者是 FastAPI 应用。它们把管理工作交给 `studio/`，把运行时机制交给 `terrarium/`。

请参阅 [`src/kohakuterrarium/README.md`](../../src/kohakuterrarium/README.md)，其中的 ASCII 依赖流程图是唯一可信来源。

## 为什么需要这些规则

这些规则服务于三个目标：

1. **没有循环。** 循环会导致初始化顺序脆弱、部分导入错误，以及启动时容易出问题的导入期副作用。
2. **可测试性。** 如果 `core/` 永远不导入 `terrarium/`，你就可以在不启动多 Agent 运行时的情况下对 controller 做单元测试。如果 `modules/` 只放协议，也能很容易替换实现。
3. **清晰的变更影响面。** 修改 `utils/` 时，所有内容都会重建；修改 `cli/` 时，其他部分都不会。分层让你能够预估一次改动的影响范围。

历史注记：以前曾存在一个循环 `builtins.tools.registry → terrarium.runtime → core.agent → builtins.tools.registry`。后来通过引入 catalog/helper 模块，并把 Terrarium root-tool 实现移到 `terrarium/` 下拆解。`core/__init__.py` 仍使用模块级 `__getattr__` 做延迟 public export；新的函数内部导入应通过 dep-graph allowlist 说明理由，而不是当成循环 workaround。

## 工具：`scripts/dep_graph.py`

这是一个静态 AST 分析器。它会以 UTF-8 读取并遍历 `src/kohakuterrarium/` 下的每个 `.py`，解析 `import` / `from ... import`，并将每条边分类为：

- **runtime** —— 模块加载时在顶层执行的导入。
- **TYPE_CHECKING** —— 受 `if TYPE_CHECKING:` 保护，不会进入运行时图。
- **in-function** —— 函数内部导入。默认/循环视图会包含这些边，以便发现隐藏循环；`--module-only` 可恢复旧的仅顶层导入图。

import hygiene lint 会根据 stdlib、必需依赖、可选依赖、平台限定模块以及 `scripts/dep_graph_allowlist.json` 来分类函数内部导入。每个 allowlist 条目都需要写明 reason。

### 命令

```bash
python scripts/dep_graph.py
python scripts/dep_graph.py --cycles
python scripts/dep_graph.py --lint-imports
python scripts/dep_graph.py --json
python scripts/dep_graph.py --fail
python scripts/dep_graph.py --dot > deps.dot
python scripts/dep_graph.py --plot
```

### CI / 测试入口

- `tests/unit/test_dep_graph_lint.py` 跑脚本级验证。
- `python scripts/dep_graph.py --fail` 在本地可作为快速 smoke test。
- 若新增函数内部导入，先判断是否能改成顶层导入；如果不能，更新 `scripts/dep_graph_allowlist.json` 并解释原因。
