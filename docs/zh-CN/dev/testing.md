---
title: 测试
summary: 测试目录结构、ScriptedLLM 与 TestAgentBuilder 辅助工具，以及如何编写确定性的代理测试。
tags:
  - dev
  - testing
---

# 测试

测试都在 `tests/` 下，分为 **三层**（`tests/unit/`、
`tests/integration/`、`tests/e2e/`）。每一层都是 *不同形状的
测试*，不只是体量不同 —— 详见下文的
[三层纪律](#三层纪律)。`src/kohakuterrarium/testing/`（以及
`tests/e2e/_lab_harness.py`）中提供了一套可复用的 harness，
覆盖假 LLM、真实 lab 工作节点和 journey 脚手架。

## 三层纪律

这种分层由 [CLAUDE.md](../../../CLAUDE.md) 的测试约定强制
执行。完整规格请见 `tests/README.md`，这里是要点：

### `tests/unit/` —— 一个源文件 → 一个测试（或测试类）

针对真实依赖测试单个类 / 方法（只在真正涉及 I/O 时用确定性
stub）。**形状断言**（`isinstance`、`key in dict`、
`is not None`）合法 **仅且仅在这一层**。目标：每个 core-lib
文件 95–100% 行覆盖；任何低于 95% 的文件都需要在测试或追踪
问题中给出书面理由。

### `tests/integration/` —— 一个 core-lib 文件夹 → 一个测试类

每个测试方法在 **单个函数中端到端跑完一个完整功能工作流**
（init → drive → read back → resume → verify），镜像真实消费
者驱动该文件夹的方式。把工作流拆成独立的 "init" / "read" /
"resume" 测试是单元层的思维方式，无法捕捉跨步 bug。一个文件
夹的集成测试 *就是* 该文件夹最完整的使用示例。

### `tests/e2e/` —— 整个项目 → 一小撮厚 journey 测试

每个都是单个函数，模拟一次完整的用户 session（chat → 切换
model → 切换 plugin → 中断 → resume → 分支 …）。约 10 个
journey 覆盖 `{programmatic, HTTP+WS} × {creature, terrarium,
studio}` 加上多节点。e2e 回答一个问题：*系统端到端能跑起来
吗？*

### 分层规则

- **行为断言，不是形状断言。** 每个变更测试都观察副作用，
  不只是返回值的形状。
- **真实协作者，不是 mock。** 唯一的接缝是 LLM —— 使用
  `kohakuterrarium.testing.llm.ScriptedLLM`，**同时** 在
  `bootstrap.llm.create_llm_provider` 与
  `bootstrap.agent_init.create_llm_provider` 上 monkeypatch。
  其他一切都是真的（真的 session store、真的引擎、真的 lab
  client）。
- **要提高 integration / e2e 覆盖，把现有工作流函数加厚 ——
  不要新增更多测试函数。** 这是新贡献者最常收到的 review 评
  论。新场景应该通过
  [`_BugLog`](../../../tests/e2e/test_multinode_journey.py)
  累积失败的 pattern 加进现有 journey 里，而不是新的顶层测
  试。
- **三层都在 CI 中运行**，覆盖整个 OS × Python 矩阵。

### 例外

有些文件被故意排除在 95% 单文件覆盖目标之外 —— 第三方 provider
（`llm/codex_provider.py`）、平台 PTY（`api/ws/pty.py`）、终端
用户 CLI/UI（`builtins/cli_rich/*`、`builtins/tui/*`）、
pywebview 启动路径。完整列表在 `tests/README.md` 中。

## 审计循环（多步实现工作必做）

任何大于单文件改动的任务，都 **不要** 停在 "测试通过"。请反
复运行下面这个循环直到收敛：

1. **实现** 该切片。
2. **写新测试** 把你新增的行为钉住。负面用例（你本可能不小
   心引入的 bug）比正面用例更重要。
3. **跑受影响层的全套测试**（unit/integration/e2e + 前端
   vitest）。也跑 lint（`black`、`ruff`、`prettier`）。
4. **审计** diff，眼光放苛刻 —— 分三类：
   - **明显 bug：** 拼写错误、字段名错、off-by-one、async
     调用漏 `await`、死分支。
   - **完整性 bug：** 你破坏掉的不变量 —— 本应同步的状态现
     在漂移、两个写入者竞争同一个 dict、缓存比它缓存的对象
     活得久。
   - **行为 bug：** 代码做的就是字面意思，但对规格而言是错
     的 —— 默认值错、错误被默默吞掉、condition 卡到错的分
     支。
5. **如果你发现任何测试没抓到的 bug：** 先增强测试让它本应
   *会* 抓到，确认增强后的测试在未修复代码上会失败，然后再
   修 bug。测试漏掉真实 bug 是证据，说明测试集本身就是 bug；
   先打补丁到测试上，可以防止下次再有同样的盲点。
6. **回到第 3 步循环。** 只有在审计什么都找不到 AND 每个测试
   都绿的时候才停。

这个循环就是 "我写了代码、测试通过了" 与 "我交付了能工作的
代码" 之间的差别。请把这个循环视为 definition-of-done 的一
部分，而不是可选的修饰。

## 运行测试

```bash
pytest                                    # 全量测试
pytest tests/unit                         # 只跑单元测试
pytest tests/integration                  # 只跑集成测试
pytest -k channel                         # 运行名字里带 "channel" 的测试
pytest tests/unit/test_phase3_4.py::test_executor_parallel
pytest -x                                 # 遇到第一个失败就停止
pytest --no-header -q                     # 输出更精简
```

测试要运行在完整的 asyncio 环境中。异步测试函数使用
`pytest-asyncio`（`@pytest.mark.asyncio`）。不要在测试里手写
`asyncio.run()`，让插件自行管理 event loop。

## 测试 harness

`src/kohakuterrarium/testing/` 导出四个原语，可直接从包根
import：

```python
from kohakuterrarium.testing import (
    ScriptedLLM, ScriptEntry,
    OutputRecorder,
    EventRecorder, RecordedEvent,
    TestAgentBuilder,
)
```

### ScriptedLLM —— 可控的 LLM mock

位于 `testing/llm.py`。它实现了 `LLMProvider` 协议，但不会真
的调用 API。你给它一组响应，它就按顺序输出。

```python
# 最简单：直接给字符串
llm = ScriptedLLM(["Hello.", "I'll use a tool.", "Done."])

# 复杂一点：使用 ScriptEntry，支持按 match 选择，也能控制流式输出。
# tool call 语法必须与 parser 的 tool_format 一致，默认是
# bracket: [/name]@@arg=value\nbody[name/]
llm = ScriptedLLM([
    ScriptEntry("I'll search.", match="find"),   # 只有最后一条用户消息里包含 "find" 才会触发
    ScriptEntry("Sorry, can't.", match="help"),
    ScriptEntry("[/bash]@@command=echo hi\n[bash/]", chunk_size=5),
])
```

`ScriptEntry`（`testing/llm.py:12`）包含以下字段：

- `response: str` —— 完整文本，可以带框架格式的 tool call。
- `match: str | None` —— 设置后，只有最后一条用户消息中包含
  该子串时才会使用这条。
- `delay_per_chunk: float` —— 每个 chunk 之间等待多久。
- `chunk_size: int` —— 每次 yield 多少个字符，默认 10。

运行结束后，常看的字段有：

- `llm.call_count`
- `llm.call_log` —— 每次调用时看到的 message 列表
- `llm.last_user_message` —— 最后一条用户消息

如果你只需要一个非流式响应，直接调用：
`await llm.chat_complete(messages)`，它会返回 `ChatResponse`。

### TestAgentBuilder —— 轻量搭一个 Agent

位于 `testing/agent.py`。它会直接组装一个 `Controller`、
`Executor`、`OutputRouter`，无需加载 YAML，也不用跑完整的
`Agent.start()`。用它单测 controller loop 和 tool dispatch 很
方便。

```python
from kohakuterrarium.testing import TestAgentBuilder

env = (
    TestAgentBuilder()
    .with_llm_script(["[/bash]@@command=echo hi\n[bash/]", "Done."])
    .with_builtin_tools(["bash", "read"])
    .with_system_prompt("You are a test agent.")
    .with_session("test_session")
    .build()
)

await env.inject("please echo")

assert env.llm.call_count >= 1
env.output.assert_text_contains("Done")
```

`env` 是一个 `TestAgentEnv`，其中包含 `llm`、`output`、
`controller`、`executor`、`registry`、`router`、`session`。
`env.inject(text)` 会完整跑一轮：塞入一个 user-input event，
读取 scripted LLM 的流式输出，解析 tool/command event，把
tool 交给 executor，其他内容交给 `OutputRouter`。如果你想直
接喂原始事件，可以用 `env.inject_event(TriggerEvent(...))`。

Builder 方法见 `testing/agent.py:19`：

- `with_llm_script(list)` / `with_llm(ScriptedLLM)`
- `with_output(OutputRecorder)`
- `with_system_prompt(str)`
- `with_session(key)`
- `with_builtin_tools(list[str])` —— 通过 `get_builtin_tool` 解析
- `with_tool(instance)` —— 注册自定义 tool
- `with_named_output(name, output)`
- `with_ephemeral(bool)`

### OutputRecorder —— 捕获输出做断言

位于 `testing/output.py`。它是 `BaseOutputModule` 的子类，会
记录每次 write、stream chunk 和 activity 通知。

```python
recorder = OutputRecorder()
await recorder.write("final text")
await recorder.write_stream("chunk1")
await recorder.write_stream("chunk2")
recorder.on_activity("tool_start", "[bash] job_123")

assert recorder.all_text == "chunk1chunk2final text"
assert recorder.stream_text == "chunk1chunk2"
assert recorder.writes == ["final text"]
recorder.assert_text_contains("chunk1")
recorder.assert_activity_count("tool_start", 1)
```

它会分别记录这些状态：`writes`、`streams`、`activities`、
`processing_starts`、`processing_ends`。`reset()` 会在每轮之
间清掉 writes 和 streams（`OutputRouter` 会调用它）；
`clear_all()` 会把 activities 和生命周期计数一并清掉。

断言辅助方法有：`assert_no_text`、`assert_text_contains`、
`assert_activity_count`。

### EventRecorder —— 记录时序

位于 `testing/events.py`。它会带着单调递增的时间戳和 source
label 记录事件。

```python
er = EventRecorder()
er.record("tool_complete", "bash ok", source="tool")
er.record("channel_message", "hello", source="channel")

assert er.count == 2
er.assert_order("tool_complete", "channel_message")
er.assert_before("tool_complete", "channel_message")
```

适用于关注先后顺序而不是具体文本的场景。

## 约定

- **用 `ScriptedLLM`，不要去 mock provider 底层。** 不要
  monkey-patch `httpx` 或 OpenAI SDK。`ScriptedLLM` 就卡在
  `LLMProvider` 这一层，controller 也是在这里与 LLM 交互。
- **除非你就是在测持久化，否则不要带 session store。**
  harness 默认会跳过 `SessionStore`。如果是 `kt run` 的 CLI
  集成测试，就传 `--no-session` 或等价参数。
- **记得清理。** Pytest fixture 最好一测一个 Agent，用完就
  拆。`TestAgentBuilder.build()` 会调用 `set_session`，会往
  模块级 registry 写入内容。如果 session key 泄漏了，就给
  `with_session(...)` 换个 key，或者在 `yield` fixture 中清
  掉。
- **不要走真实网络。** 只要有东西要发 HTTP，就在传输层
  mock，或者直接跳过。
- **不要漏掉 Async 标记。** 异步测试记得加
  `@pytest.mark.asyncio`。如果想省事，也可以在
  `pyproject.toml` 中设置 `asyncio_mode = "auto"`。

## 测试该放哪

`tests/unit/` 基本按 `src/` 结构组织：

| 你改了什么 | 测试加到哪里 |
|---|---|
| `core/agent.py` | `tests/unit/test_phase5.py` 或新文件 |
| `core/controller.py` | `tests/unit/test_phase3_4.py` |
| `core/executor.py` | `tests/unit/test_phase3_4.py` |
| `parsing/` | `tests/unit/test_phase2.py` |
| `modules/subagent/` | `tests/unit/test_phase6.py` |
| `modules/trigger/` | `tests/unit/test_phase7.py` |
| `core/environment.py` | `tests/unit/test_environment.py` |
| `session/store.py` | `tests/unit/test_session_store.py` |
| `session/resume.py` | `tests/unit/test_session_resume.py` |
| `bootstrap/` | `tests/unit/test_bootstrap.py` |
| `terrarium/` | `tests/unit/test_terrarium_modules.py` |

跨组件流程放到 `tests/integration/`：

- channels — `test_channels.py`
- output routing — `test_output_isolation.py`
- 整条 pipeline（controller → executor → output）— `test_pipeline.py`

如果某个子系统还没有现成的测试文件，就新建一个，命名风格与
现有文件保持一致。

完整的用户 journey 放到 `tests/e2e/` —— 一个 journey 对应一
个胖函数。例子：

- `test_multinode_journey.py` —— `{programmatic, HTTP+WS} ×
  多节点`，通过 `RealLabWorker` 用进程内的方式驱动两个真实
  lab 工作节点，跑遍整个 dashboard 面：生成、聊天、跨节点
  connect、热插拔、关闭、列出已保存、resume、cluster resume。
- `test_prog_studio.py`、`test_prog_terrarium.py` —— 直接驱
  动 Studio + Terrarium API 的程序化 journey。
- `test_api_creature.py` —— 单生物的 dashboard HTTP+WS 面。

## 测试多节点代码

多节点代码（Lab 适配器、`MultiNodeTerrariumService`、
session 同步、cluster fold）至少需要一个工作节点才有意义。
有三种 pattern：

### 单元：`_FakeNode` / `_RecordingNode`

在测试工作节点侧的适配器或 `IdentityCache` 时，使用一个实现
了 `LabSender` / `LabRegistrar` 的小型 fake。例：
`tests/unit/laboratory/test_worker_session.py` 构造了一个
`_FakeEngine` + `_RecordingNode`，直接驱动 attacher。不启动
任何 Lab 传输 —— 这类测试是亚毫秒级的。

### 集成：`InProcTransport`

对于横跨真实 Lab 派发逻辑的工作流（握手 → APP request →
response），使用
`laboratory/_internal/transport_inproc.py` 中的
`InProcTransport`。它实现了与 WebSocket 传输相同的
`LabTransport` Protocol，但把一切都保留在同一个 event loop
里。规范的 setup helper 请见
`tests/unit/laboratory/test_client_host.py::_start_host`。

### E2E：`RealLabWorker`

journey 层使用 `tests/e2e/_lab_harness.RealLabWorker` ——
它针对运行在真实 WebSocket 传输上的真实 `HostEngine`，启动
一个带完整十适配器栈（runtime、events、attach、pty、
broadcast、output-wire、files、deploy、session、
identity-cache、catalog、identity）的真实 `ClientConnector`。
虽然是 "真实 lab"，它共享测试的 event loop，所以断点能正常
工作。

如果要做完整的类生产隔离（独立进程），`_lab_harness.py` 也
有一个子进程启动的变体 —— 在多节点 journey 中用来验证
Win32 进程边界与信号处理。

约定：

- 用 `--home-dir` 指向某个 `tmp_path` 子目录来生成工作节
  点，让每个测试都有自己独立的凭据存储。
- 用 `_BugLog` 累积失败的 pattern（见
  `test_multinode_journey.py`）来跑那些应该在一次运行中报告
  多个失败、而不是在第一个红色断言就退出的 journey。
- 多节点测试与单节点测试放在一起 —— 没有单独的
  `tests/multinode/` 目录。用描述性的测试函数名打标签（例如
  `test_full_creature_session_on_subprocess_worker`）。

## 快速测试和集成测试

- **快速单元测试** 使用 `TestAgentBuilder`，不要碰文件 I/O，
  不要走真实 LLM，最好在一秒内跑完。默认测试集应以这类测试
  为主。
- **集成测试** 会把两个或更多子系统一起跑起来，比如
  controller 的反馈循环配合真实 executor 和真实 tools。可以
  访问文件系统，也可以使用真正的 session store，但最好仍控
  制在几秒内。
- **手动 / 慢测试**，例如真实 LLM 调用、长时间运行的
  Agent，不应进入默认测试集。给它们加上
  `@pytest.mark.slow`，或者放进 `tests/manual/`。

## Lint 和格式化

提交前运行一遍：

```bash
python -m black src/ tests/
python -m ruff check src/ tests/
python -m isort src/ tests/
```

Ruff 配置在 `pyproject.toml`。`[dev]` extra 会安装这三个工
具。import 排序与 [CLAUDE.md](../../CLAUDE.md) 一致：先内
建，再第三方，再 `kohakuterrarium.*`；组内按字母序；先
`import` 后 `from`；点路径短的排前面。

## 实现后检查清单

对照 [CLAUDE.md](../../CLAUDE.md) 中的 §Post-impl tasks：

1. 不要在函数里 import（可选依赖，或者为了处理初始化顺序而
   故意做的 lazy loading 除外）。
2. Black、ruff、isort 都要保持干净。
3. 新行为要配套测试。
4. commit 按逻辑拆分。没有要求的话，不要推送草稿。
