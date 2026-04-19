---
title: 測試
summary: 測試目錄結構、ScriptedLLM 與 TestAgentBuilder helper，以及怎麼寫出具決定性的代理測試。
tags:
  - dev
  - testing
---

# 測試

測試套件在 `tests/` 底下，分成 unit tests (`tests/unit/`) 與 integration tests (`tests/integration/`)。`src/kohakuterrarium/testing/` 下有一套可重用的測試 harness，用來搭配假的 LLM 組出代理。

## 跑測試

```bash
pytest                                    # 整套
pytest tests/unit                         # 只跑 unit
pytest tests/integration                  # 只跑 integration
pytest -k channel                         # 名字含 "channel" 的
pytest tests/unit/test_phase3_4.py::test_executor_parallel
pytest -x                                 # 第一個失敗就停
pytest --no-header -q                     # 安靜一點
```

測試要用 full asyncio 跑。async 測試函式請加 `pytest-asyncio` 的 `@pytest.mark.asyncio`。別在測試裡自己呼叫 `asyncio.run()` — 讓 plugin 管 event loop。

## 測試 harness

`src/kohakuterrarium/testing/` 匯出四個原語，直接從套件根 import：

```python
from kohakuterrarium.testing import (
    ScriptedLLM, ScriptEntry,
    OutputRecorder,
    EventRecorder, RecordedEvent,
    TestAgentBuilder,
)
```

### ScriptedLLM — 具決定性的 LLM mock

`testing/llm.py`。實作 `LLMProvider` 協定，不需要真的打 API。餵它一串回應，它會照順序吐出來。

```python
# 最簡單：直接給字串
llm = ScriptedLLM(["Hello.", "I'll use a tool.", "Done."])

# 進階：用 ScriptEntry 做條件選擇與串流控制。
# 工具呼叫語法必須符合 parser 的 tool_format — 預設 bracket 格式：
# [/name]@@arg=value\nbody[name/]
llm = ScriptedLLM([
    ScriptEntry("I'll search.", match="find"),   # 上一個 user message 含 "find" 時才觸發
    ScriptEntry("Sorry, can't.", match="help"),
    ScriptEntry("[/bash]@@command=echo hi\n[bash/]", chunk_size=5),
])
```

`ScriptEntry` (`testing/llm.py:12`) 的欄位：

- `response: str` — 完整文字，可以放框架格式的工具呼叫。
- `match: str | None` — 有設的話，只有最後一則 user message 含這個 substring 時才選用；否則跳過。
- `delay_per_chunk: float` — 每個 chunk 之間的延遲秒數。
- `chunk_size: int` — 每次 yield 的字元數 (預設 10)。

跑完後可以檢查：

- `llm.call_count`
- `llm.call_log` — 每次呼叫看到的 message list
- `llm.last_user_message` — 方便抽取

如果只需要一個非串流的回應，呼叫 `await llm.chat_complete(messages)` (回傳 `ChatResponse`)。

### TestAgentBuilder — 輕量的代理組裝

`testing/agent.py`。建一組 `Controller` + `Executor` + `OutputRouter`，不需要載 YAML config、也不跑完整的 `Agent.start()` bootstrap。用來單獨測控制器迴圈與工具派發很方便。

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

`env` 是一個 `TestAgentEnv`，暴露 `llm`、`output`、`controller`、`executor`、`registry`、`router`、`session`。`env.inject(text)` 會跑一回合：推一個 user-input 事件進去、從 scripted LLM 串流、剖析 tool/command 事件、把工具透過 executor 派發、其餘的路由到 `OutputRouter`。要用原始事件就用 `env.inject_event(TriggerEvent(...))`。

Builder 方法 (見 `testing/agent.py:19`)：

- `with_llm_script(list)` / `with_llm(ScriptedLLM)`
- `with_output(OutputRecorder)`
- `with_system_prompt(str)`
- `with_session(key)`
- `with_builtin_tools(list[str])` — 透過 `get_builtin_tool` 解析
- `with_tool(instance)` — 註冊自訂工具
- `with_named_output(name, output)`
- `with_ephemeral(bool)`

### OutputRecorder — 蒐集輸出做 assertion

`testing/output.py`。一個 `BaseOutputModule` 子類別，會記下每一次 write、stream chunk、activity 通知。

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

狀態分開存：`writes`、`streams`、`activities`、`processing_starts`、`processing_ends`。`reset()` 在回合之間清掉 writes 與 streams (`OutputRouter` 會呼叫這個)；`clear_all()` 連同 activities 與 lifecycle 計數一起清掉。

Assertion helper：`assert_no_text`、`assert_text_contains`、`assert_activity_count`。

### EventRecorder — 時序與順序

`testing/events.py`。以 monotonic 時間戳加上 source 標籤追蹤事件。

```python
er = EventRecorder()
er.record("tool_complete", "bash ok", source="tool")
er.record("channel_message", "hello", source="channel")

assert er.count == 2
er.assert_order("tool_complete", "channel_message")
er.assert_before("tool_complete", "channel_message")
```

當你關心的是**什麼時候**發生、而不是文字內容時，特別好用。

## 慣例

- **用 `ScriptedLLM`，別用 provider 層的 mock。** 不要 monkey-patch `httpx` 或 OpenAI SDK。Scripted LLM 坐在 `LLMProvider` 協定的界線上，這正是控制器跟它互動的那個點。
- **測試裡不要用 session store，除非你就是在測持久化。** 預設 harness 會跳過 `SessionStore`。如果是 CLI integration test 要呼叫 `kt run`，加 `--no-session` (或對應旗標)。
- **清乾淨。** Pytest fixture 應該每個測試建一隻代理然後拆掉。`TestAgentBuilder.build()` 會呼叫 `set_session`，寫入一個 module-level registry — 如果測試間會漏 session key，請用不同的 `with_session(...)` key，或在 `yield` 風格的 fixture 裡清掉。
- **不碰真的網路。** 若某段程式想打 HTTP，就在 transport 層 mock 掉、或乾脆跳過這個測試。
- **Async mark。** async 測試要裝飾 `@pytest.mark.asyncio`；想要自動標記的話，在 `pyproject.toml` 設 `asyncio_mode = "auto"`。

## 測試放在哪

`tests/unit/` 底下的結構跟 `src/` 對映：

| 你改了                   | 加測試到                            |
|-------------------------|------------------------------------|
| `core/agent.py`         | `tests/unit/test_phase5.py` 或新檔 |
| `core/controller.py`    | `tests/unit/test_phase3_4.py`      |
| `core/executor.py`      | `tests/unit/test_phase3_4.py`      |
| `parsing/`              | `tests/unit/test_phase2.py`        |
| `modules/subagent/`     | `tests/unit/test_phase6.py`        |
| `modules/trigger/`      | `tests/unit/test_phase7.py`        |
| `core/environment.py`   | `tests/unit/test_environment.py`   |
| `session/store.py`      | `tests/unit/test_session_store.py` |
| `session/resume.py`     | `tests/unit/test_session_resume.py`|
| `bootstrap/`            | `tests/unit/test_bootstrap.py`     |
| `terrarium/`            | `tests/unit/test_terrarium_modules.py` |

跨模組的流程放在 `tests/integration/`：

- channels — `test_channels.py`
- output routing — `test_output_isolation.py`
- 完整 pipeline (controller → executor → output) — `test_pipeline.py`

如果某個子系統還沒有測試檔，就新增一個，並照命名慣例取名。

## Fast vs integration

- **Fast unit tests** 應該用 `TestAgentBuilder` (不碰檔案 I/O、不打真的 LLM)，每個都要遠低於一秒。大部分測試都應該長這樣。
- **Integration tests** 同時跑兩個以上的子系統 — 例如控制器的回饋迴圈配上真的 executor 與真的工具。可以碰檔案系統、用真的 session store，但還是該在個位數秒內跑完。
- **手動 / 慢測試** (真的打 LLM、跑很久的代理) 不該放進預設套件。請標 `@pytest.mark.slow` 或放到 `tests/manual/`。

## Lint 與格式化

Commit 之前：

```bash
python -m black src/ tests/
python -m ruff check src/ tests/
python -m isort src/ tests/
```

Ruff 設定在 `pyproject.toml`。`[dev]` extra 會一起裝這三個工具。Import 順序遵循 [CLAUDE.md](../../CLAUDE.md) — 內建 → 第三方 → `kohakuterrarium.*`，每組內按字母排序，`import` 在 `from` 前面，點數少的路徑排在前面。

## 實作後檢查清單

對照 [CLAUDE.md](../../CLAUDE.md) §Post-impl tasks：

1. 沒有 in-function import (除非是選用相依，或為了處理 init-order 故意延後)。
2. Black + ruff + isort 乾淨。
3. 新行為要有測試。
4. Commit 依邏輯切開。除非被要求，草稿別 push。
