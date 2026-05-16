---
title: 測試
summary: 測試版面、ScriptedLLM 與 TestAgentBuilder helper，以及如何寫出具決定性的代理測試。
tags:
  - dev
  - testing
---

# 測試

測試套件在 `tests/` 底下，分成**三個層級**
（`tests/unit/`、`tests/integration/`、`tests/e2e/`）。每個層級
都是*不同形狀的測試*，而不只是不同大小 —— 見下方
[三層級紀律](#三層級紀律)。`src/kohakuterrarium/testing/`（與
`tests/e2e/_lab_harness.py`）底下有一套可重用的 harness，
涵蓋假 LLM、真實 lab worker 與 journey scaffolding。

## 三層級紀律

層級劃分由 [CLAUDE.md](../../../CLAUDE.md) 的測試慣例強制。
完整規格見 `tests/README.md`；摘要：

### `tests/unit/` —— 一個原始檔 → 一個測試（或 test-class）

對單一 class / method 跑測試，依賴使用真實的依賴
（真的有 I/O 時才用具決定性的 stub）。**形狀檢查**
（`isinstance`、`key in dict`、`is not None`）在**這裡而且只在
這裡**是合理的。目標：每個核心庫檔案 95–100% 行覆蓋率；任何
低於 95% 的檔案需要在測試或追蹤 issue 中寫下書面理由。

### `tests/integration/` —— 一個核心庫資料夾 → 一個 test-class

每個 test method 在**單一函式內**跑一個完整的功能 workflow
end-to-end（init → drive → 讀回 → resume → 驗證），對映實際
消費者驅動該資料夾的方式。把一個 workflow 切成各自獨立的
「init」/「read」/「resume」測試是 unit-tier 的思維，沒辦法
抓出跨步驟的 bug。一個資料夾的 integration 測試*就是*該資料夾
最完整的使用範例。

### `tests/e2e/` —— 整個專案 → 少數幾個厚重的 journey 測試

每個都是單一函式，模擬一整個使用者 session（chat → 切模型 →
toggle plugin → interrupt → resume → branch …）。約 10 個
journey 覆蓋 `{programmatic, HTTP+WS} × {creature, terrarium,
studio}` 加上多節點。e2e 回答一個問題：*系統從頭到尾能跑嗎？*

### 層級規則

- **行為斷言，不是形狀斷言。** 每個 mutation 測試都觀察 side
  effect，不只是回傳的形狀。
- **真實協作者，不是 mock。** 唯一的接縫是 LLM ——
  使用 `kohakuterrarium.testing.llm.ScriptedLLM`，在
  **兩處** `bootstrap.llm.create_llm_provider` 與
  `bootstrap.agent_init.create_llm_provider` 都做 monkeypatch。
  其他一切都是真的（真的 session store、真的引擎、真的 lab
  client）。
- **要提升 integration / e2e 的覆蓋率，請把現有的 workflow
  函式加厚 —— 不要新增測試函式。** 這是給新貢獻者最常見的
  review 評語。新場景透過
  [`_BugLog`](../../../tests/e2e/test_multinode_journey.py)
  fail-accumulator pattern 加進現有 journey 裡，不要做成新的
  最上層測試。
- **三個層級都會在 CI 上完整的 OS × Python 矩陣裡執行。**

### 排除項

有些檔案被刻意排除在 95% per-file 覆蓋率目標之外 —— 第三方
provider（`llm/codex_provider.py`）、平台 PTY
（`api/ws/pty.py`）、使用者端 CLI/UI（`builtins/cli_rich/*`、
`builtins/tui/*`）、pywebview 啟動路徑。完整清單在
`tests/README.md`。

## Audit loop（多步驟實作必做）

對任何大於一個檔案變更的任務，**不要**停在「測試通過」。執行
這個迴圈直到收斂：

1. **實作**該切片。
2. **寫新測試**把你加入的行為釘下來。負面案例（你會不小心
   引入的 bug）比正面案例更重要。
3. **執行受影響層級的完整測試套件**（unit/integration/e2e +
   前端 vitest）。也跑 lint（`black`、`ruff`、`prettier`）。
4. **以批判眼光稽核** diff —— 三個類別：
   - **明顯 bug：** 拼錯、欄位名錯、off-by-one、async 呼叫
     漏 `await`、死分支。
   - **完整性 bug：** 你打破的不變式 —— 本來應該同步的狀態
     現在會漂移、兩個 writer 競爭同一個 dict、cache 的壽命
     超過它快取的對象。
   - **行為 bug：** 程式碼做了字面上寫的事，但對規格而言是
     錯事 —— 預設值錯、錯誤被悄悄吃掉、條件 gate 到錯的
     分支。
5. **如果發現任何測試沒抓到的 bug：** 先增強測試讓它*本來
   應該*抓到，確認增強後的測試在未修復的程式碼上失敗，再修
   bug。漏掉真 bug 的測試是測試套件本身就是 bug 的證據；
   先修測試可避免下一次出現同樣的盲點。
6. **回到步驟 3 重新繞**。直到稽核找不到東西**且**每個測試都
   綠才停。

這個迴圈就是「我寫了程式碼且測試通過」與「我交付了能運作的
程式碼」的差別。把這個迴圈當成 definition-of-done 的一部分，
不是選用的拋光。

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

測試要用 full asyncio 跑。async 測試函式請加 `pytest-asyncio` 的
`@pytest.mark.asyncio`。別在測試裡自己呼叫 `asyncio.run()` ——
讓 plugin 管 event loop。

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

### ScriptedLLM —— 具決定性的 LLM mock

`testing/llm.py`。實作 `LLMProvider` 協定，不需要真的打 API。
餵它一串回應，它會照順序吐出來。

```python
# 最簡單：直接給字串
llm = ScriptedLLM(["Hello.", "I'll use a tool.", "Done."])

# 進階：用 ScriptEntry 做條件選擇與串流控制。
# 工具呼叫語法必須符合 parser 的 tool_format —— 預設 bracket 格式：
# [/name]@@arg=value\nbody[name/]
llm = ScriptedLLM([
    ScriptEntry("I'll search.", match="find"),   # 上一個 user message 含 "find" 時才觸發
    ScriptEntry("Sorry, can't.", match="help"),
    ScriptEntry("[/bash]@@command=echo hi\n[bash/]", chunk_size=5),
])
```

`ScriptEntry` (`testing/llm.py:12`) 的欄位：

- `response: str` —— 完整文字，可以放框架格式的工具呼叫。
- `match: str | None` —— 有設的話，只有最後一則 user message
  含這個 substring 時才選用；否則跳過。
- `delay_per_chunk: float` —— 每個 chunk 之間的延遲秒數。
- `chunk_size: int` —— 每次 yield 的字元數（預設 10）。

跑完後可以檢查：

- `llm.call_count`
- `llm.call_log` —— 每次呼叫看到的 message list
- `llm.last_user_message` —— 方便抽取

如果只需要一個非串流的回應，呼叫
`await llm.chat_complete(messages)`（回傳 `ChatResponse`）。

### TestAgentBuilder —— 輕量的代理組裝

`testing/agent.py`。建一組 `Controller` + `Executor` +
`OutputRouter`，不需要載 YAML config、也不跑完整的
`Agent.start()` bootstrap。用來單獨測控制器迴圈與工具派發很
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

`env` 是一個 `TestAgentEnv`，暴露 `llm`、`output`、`controller`、
`executor`、`registry`、`router`、`session`。`env.inject(text)`
會跑一回合：推一個 user-input 事件進去、從 scripted LLM
串流、剖析 tool/command 事件、把工具透過 executor 派發、
其餘的路由到 `OutputRouter`。要用原始事件就用
`env.inject_event(TriggerEvent(...))`。

Builder 方法（見 `testing/agent.py:19`）：

- `with_llm_script(list)` / `with_llm(ScriptedLLM)`
- `with_output(OutputRecorder)`
- `with_system_prompt(str)`
- `with_session(key)`
- `with_builtin_tools(list[str])` —— 透過 `get_builtin_tool` 解析
- `with_tool(instance)` —— 註冊自訂工具
- `with_named_output(name, output)`
- `with_ephemeral(bool)`

### OutputRecorder —— 蒐集輸出做 assertion

`testing/output.py`。一個 `BaseOutputModule` 子類別，會記下
每一次 write、stream chunk、activity 通知。

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

狀態分開存：`writes`、`streams`、`activities`、
`processing_starts`、`processing_ends`。`reset()` 在回合之間
清掉 writes 與 streams（`OutputRouter` 會呼叫這個）；
`clear_all()` 連同 activities 與 lifecycle 計數一起清掉。

Assertion helper：`assert_no_text`、`assert_text_contains`、
`assert_activity_count`。

### EventRecorder —— 時序與順序

`testing/events.py`。以 monotonic 時間戳加上 source 標籤
追蹤事件。

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

- **用 `ScriptedLLM`，別用 provider 層的 mock。** 不要
  monkey-patch `httpx` 或 OpenAI SDK。Scripted LLM 坐在
  `LLMProvider` 協定的界線上，這正是控制器跟它互動的那個點。
- **測試裡不要用 session store，除非你就是在測持久化。**
  預設 harness 會跳過 `SessionStore`。如果是 CLI integration
  test 要呼叫 `kt run`，加 `--no-session`（或對應旗標）。
- **清乾淨。** Pytest fixture 應該每個測試建一隻代理然後拆掉。
  `TestAgentBuilder.build()` 會呼叫 `set_session`，寫入一個
  module-level registry —— 如果測試間會漏 session key，請用
  不同的 `with_session(...)` key，或在 `yield` 風格的 fixture
  裡清掉。
- **不碰真的網路。** 若某段程式想打 HTTP，就在 transport 層
  mock 掉、或乾脆跳過這個測試。
- **Async mark。** async 測試要裝飾 `@pytest.mark.asyncio`；
  想要自動標記的話，在 `pyproject.toml` 設
  `asyncio_mode = "auto"`。

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

- channels —— `test_channels.py`
- output routing —— `test_output_isolation.py`
- 完整 pipeline（controller → executor → output）—— `test_pipeline.py`

如果某個子系統還沒有測試檔，就新增一個，並照命名慣例取名。

完整的使用者 journey 放在 `tests/e2e/` —— 一個 journey 一個厚重
的函式。範例：

- `test_multinode_journey.py` —— `{programmatic, HTTP+WS} ×
  多節點`，驅動兩個真實的 lab worker（透過 `RealLabWorker`
  在行程內）跑完整個 dashboard 表面：spawn、chat、跨節點
  connect、熱插拔、close、列舉 saved、resume、cluster resume。
- `test_prog_studio.py`、`test_prog_terrarium.py` —— 直接驅動
  Studio + Terrarium API 的程式化 journey。
- `test_api_creature.py` —— 單一生物的 dashboard HTTP+WS 表面。

## 測試多節點程式碼

多節點程式碼（Lab adapter、`MultiNodeTerrariumService`、
session 同步、cluster fold）至少需要一個 worker 才有意義。
三種 pattern：

### Unit：`_FakeNode` / `_RecordingNode`

要測 worker 側 adapter 或 `IdentityCache` 時，用實作了
`LabSender` / `LabRegistrar` 的小 fake。範例：
`tests/unit/laboratory/test_worker_session.py` 建一個
`_FakeEngine` + `_RecordingNode` 並直接驅動 attacher。完全
不啟動 Lab transport —— 跑起來在毫秒以下。

### Integration：`InProcTransport`

對於橫跨實際 Lab dispatch 邏輯的 workflow（handshake → APP
request → response），用 `laboratory/_internal/transport_inproc.py`
的 `InProcTransport`。它實作和 WebSocket transport 同一個
`LabTransport` Protocol，但所有東西都留在同一個 event loop
裡。標準設定 helper 見
`tests/unit/laboratory/test_client_host.py::_start_host`。

### E2E：`RealLabWorker`

Journey 層級使用 `tests/e2e/_lab_harness.RealLabWorker` ——
對著真正的 `HostEngine` 在真正的 WebSocket transport 上
拉起一個真實的 `ClientConnector`，搭配完整的十個 adapter
（runtime、events、attach、pty、broadcast、output-wire、
files、deploy、session、identity-cache、catalog、identity）。
雖說「真的 lab」，但它共用測試的 event loop，所以 breakpoint
仍然會中。

要做完全生產級的隔離（獨立行程），`_lab_harness.py` 也有
subprocess 啟動的變體 —— multinode journey 用它來驗證 Win32
process 邊界與 signal handling。

慣例：

- 啟動 worker 時，`--home-dir` 指向 `tmp_path` 的子目錄，
  讓每個測試都有自己的憑證儲存。
- 對應該在一次執行中回報多個失敗、而不是在第一個 red assertion
  就放棄的 journey，使用 `_BugLog` fail-accumulator pattern
  （見 `test_multinode_journey.py`）。
- 多節點測試與單節點測試放在一起 —— 沒有獨立的 `tests/multinode/`
  目錄。用具描述性的測試函式名標記
  （`test_full_creature_session_on_subprocess_worker`）。

## Fast vs integration

- **Fast unit tests** 應該用 `TestAgentBuilder`（不碰檔案 I/O、
  不打真的 LLM），每個都要遠低於一秒。大部分測試都應該長這樣。
- **Integration tests** 同時跑兩個以上的子系統 —— 例如控制器
  的回饋迴圈配上真的 executor 與真的工具。可以碰檔案系統、用
  真的 session store，但還是該在個位數秒內跑完。
- **手動 / 慢測試**（真的打 LLM、跑很久的代理）不該放進預設
  套件。請標 `@pytest.mark.slow` 或放到 `tests/manual/`。

## Lint 與格式化

Commit 之前：

```bash
python -m black src/ tests/
python -m ruff check src/ tests/
python -m isort src/ tests/
```

Ruff 設定在 `pyproject.toml`。`[dev]` extra 會一起裝這三個工具。
Import 順序遵循 [CLAUDE.md](../../CLAUDE.md) —— 內建 → 第三方 →
`kohakuterrarium.*`，每組內按字母排序，`import` 在 `from`
前面，點數少的路徑排在前面。

## 實作後檢查清單

對照 [CLAUDE.md](../../CLAUDE.md) §Post-impl tasks：

1. 沒有 in-function import（除非是選用相依，或為了處理
   init-order 故意延後）。
2. Black + ruff + isort 乾淨。
3. 新行為要有測試。
4. Commit 依邏輯切開。除非被要求，草稿別 push。
