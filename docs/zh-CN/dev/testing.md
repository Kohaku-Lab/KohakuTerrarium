---
title: 测试
summary: 测试目录结构、ScriptedLLM 与 TestAgentBuilder 辅助工具，以及如何编写确定性的代理测试。
tags:
  - dev
  - testing
---
# 测试

测试都在 `tests/` 下，分为单元测试 `tests/unit/` 和集成测试 `tests/integration/`。此外，`src/kohakuterrarium/testing/` 里还提供了一套现成的测试工具，方便你用假的 LLM 搭建 Agent。

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

测试要运行在完整的 asyncio 环境中。异步测试函数使用 `pytest-asyncio`（`@pytest.mark.asyncio`）。不要在测试里手写 `asyncio.run()`，让插件自行管理 event loop。

## 测试 harness

`src/kohakuterrarium/testing/` 中常用的内容如下，可直接从包根导入：

```python
from kohakuterrarium.testing import (
    ScriptedLLM, ScriptEntry,
    OutputRecorder,
    EventRecorder, RecordedEvent,
    TestAgentBuilder,
)
```

### ScriptedLLM —— 可控的 LLM mock

位于 `testing/llm.py`。它实现了 `LLMProvider` 协议，但不会真的调用 API。你给它一组响应，它就按顺序输出。

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

- `response: str` — 完整文本，可以带框架格式的 tool call。
- `match: str | None` — 设置后，只有最后一条用户消息中包含该子串时才会使用这条。
- `delay_per_chunk: float` — 每个 chunk 之间等待多久。
- `chunk_size: int` — 每次 yield 多少个字符，默认 10。

运行结束后，常看的字段有：

- `llm.call_count`
- `llm.call_log` —— 每次调用时看到的 message 列表
- `llm.last_user_message` —— 最后一条用户消息

如果你只需要一个非流式响应，直接调用：
`await llm.chat_complete(messages)`，它会返回 `ChatResponse`。

### TestAgentBuilder —— 轻量搭一个 Agent

位于 `testing/agent.py`。它会直接组装一个 `Controller`、`Executor`、`OutputRouter`，无需加载 YAML，也不用跑完整的 `Agent.start()`。用它单测 controller loop 和 tool dispatch 很方便。

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

`env` 是一个 `TestAgentEnv`，其中包含 `llm`、`output`、`controller`、`executor`、`registry`、`router`、`session`。`env.inject(text)` 会完整跑一轮：塞入一个 user-input event，读取 scripted LLM 的流式输出，解析 tool/command event，把 tool 交给 executor，其他内容交给 `OutputRouter`。如果你想直接喂原始事件，可以用 `env.inject_event(TriggerEvent(...))`。

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

位于 `testing/output.py`。它是 `BaseOutputModule` 的子类，会记录每次 write、stream chunk 和 activity 通知。

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

它会分别记录这些状态：`writes`、`streams`、`activities`、`processing_starts`、`processing_ends`。`reset()` 会在每轮之间清掉 writes 和 streams（`OutputRouter` 会调用它）；`clear_all()` 会把 activities 和生命周期计数一并清掉。

断言辅助方法有：`assert_no_text`、`assert_text_contains`、`assert_activity_count`。

### EventRecorder —— 记录时序

位于 `testing/events.py`。它会带着单调递增的时间戳和 source label 记录事件。

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

- **用 `ScriptedLLM`，不要去 mock provider 底层。** 不要 monkey-patch `httpx` 或 OpenAI SDK。`ScriptedLLM` 就卡在 `LLMProvider` 这一层，controller 也是在这里与 LLM 交互。
- **除非你就是在测持久化，否则不要带 session store。** harness 默认会跳过 `SessionStore`。如果是 `kt run` 的 CLI 集成测试，就传 `--no-session` 或等价参数。
- **记得清理。** Pytest fixture 最好一测一个 Agent，用完就拆。`TestAgentBuilder.build()` 会调用 `set_session`，会往模块级 registry 写入内容。如果 session key 泄漏了，就给 `with_session(...)` 换个 key，或者在 `yield` fixture 中清掉。
- **不要走真实网络。** 只要有东西要发 HTTP，就在传输层 mock，或者直接跳过。
- **不要漏掉 Async 标记。** 异步测试记得加 `@pytest.mark.asyncio`。如果想省事，也可以在 `pyproject.toml` 中设置 `asyncio_mode = "auto"`。

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

如果某个子系统还没有现成的测试文件，就新建一个，命名风格与现有文件保持一致。

## 快速测试和集成测试

- **快速单元测试** 使用 `TestAgentBuilder`，不要碰文件 I/O，不要走真实 LLM，最好在一秒内跑完。默认测试集应以这类测试为主。
- **集成测试** 会把两个或更多子系统一起跑起来，比如 controller 的反馈循环配合真实 executor 和真实 tools。可以访问文件系统，也可以使用真正的 session store，但最好仍控制在几秒内。
- **手动 / 慢测试**，例如真实 LLM 调用、长时间运行的 Agent，不应进入默认测试集。给它们加上 `@pytest.mark.slow`，或者放进 `tests/manual/`。

## Lint 和格式化

提交前运行一遍：

```bash
python -m black src/ tests/
python -m ruff check src/ tests/
python -m isort src/ tests/
```

Ruff 配置在 `pyproject.toml`。`[dev]` extra 会安装这三个工具。import 排序与 [CLAUDE.md](https://github.com/Kohaku-Lab/KohakuTerrarium/blob/main/CLAUDE.md) 一致：先内建，再第三方，再 `kohakuterrarium.*`；组内按字母序；先 `import` 后 `from`；点路径短的排前面。

## 实现后检查清单

对照 [CLAUDE.md](https://github.com/Kohaku-Lab/KohakuTerrarium/blob/main/CLAUDE.md) 中的 §Post-impl tasks：

1. 不要在函数里 import（可选依赖，或者为了处理初始化顺序而故意做的 lazy loading 除外）。
2. Black、ruff、isort 都要保持干净。
3. 新行为要配套测试。
4. commit 按逻辑拆分。没有要求的话，不要推送草稿。
