---
title: 在 Python 中嵌入
summary: 透過 AgentSession 與 compose algebra，在你自己的 Python 程式碼中執行代理。
tags:
  - tutorials
  - python
  - embedding
---

# 第一個 Python 嵌入

**問題：**你想要從自己的 Python 應用程式內執行一個生物 —— 擷取它的輸出、用程式碼驅動它的輸入，並把它和其他程式碼組合起來。

**完成狀態：**你會先完成一支最小腳本：啟動生物、注入輸入、透過自訂 handler 擷取輸出，並乾淨地關閉。接著再用 `AgentSession` 做一次事件串流版本。最後，再用相同方式嵌入一個 terrarium。

**先決條件：**[第一個生物](first-creature.md)。你需要以能 `import kohakuterrarium` 的方式安裝套件。

在這個框架裡，代理不是設定檔 —— 它是一個 Python 物件。設定檔描述的是代理；`Agent.from_path(...)` 會建出一個代理；而這個物件由你持有。Sub-agents、terrariums 與 sessions 也是同樣的形狀。完整心智模型請參考 [agent-as-python-object](../concepts/python-native/agent-as-python-object.md)。

## 步驟 1 —— 以 editable 方式安裝

目標：讓你的 venv 可以 `import kohakuterrarium`。

在 repo 根目錄執行：

```bash
uv pip install -e .[dev]
```

`[dev]` extras 也會帶入你之後可能會用到的測試輔助工具。

## 步驟 2 —— 最小嵌入範例

目標：建立一個代理、啟動它、餵它一筆輸入，再停止它。

`demo.py`：

```python
import asyncio

from kohakuterrarium.core.agent import Agent


async def main() -> None:
    agent = Agent.from_path("@kt-biome/creatures/general")

    await agent.start()
    try:
        await agent.inject_input(
            "In one sentence, what is a creature in KohakuTerrarium?"
        )
    finally:
        await agent.stop()


asyncio.run(main())
```

執行它：

```bash
python demo.py
```

預設的 stdout output 模組會印出回應。這裡有三件事值得注意：

1. `Agent.from_path` 解析 `@kt-biome/...` 的方式和 CLI 完全相同。
2. `start()` 會初始化 controller + tools + triggers + plugins。
3. `inject_input(...)` 就是使用者在 CLI input 模組中輸入訊息的程式化對應形式。

## 步驟 3 —— 自己接管輸出

目標：不要把輸出送到 stdout，而是導入你自己的程式碼。

```python
import asyncio

from kohakuterrarium.core.agent import Agent


async def main() -> None:
    parts: list[str] = []

    agent = Agent.from_path("@kt-biome/creatures/general")
    agent.set_output_handler(
        lambda text: parts.append(text),
        replace_default=True,
    )

    await agent.start()
    try:
        await agent.inject_input(
            "Explain the difference between a creature and a terrarium."
        )
    finally:
        await agent.stop()

    print("".join(parts))


asyncio.run(main())
```

`replace_default=True` 會停用 stdout，讓你的 handler 成為唯一的輸出 sink。這種形狀很適合 web backend、bot，或任何想自行掌控渲染的場景。

## 步驟 4 —— 用 `AgentSession` 做串流

目標：取得一個 chunks 的 async iterator，而不是 push handler。當你想用 `async for` 迴圈處理回應時，這會很有用。

```python
import asyncio

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.serving.agent_session import AgentSession


async def main() -> None:
    agent = Agent.from_path("@kt-biome/creatures/general")
    session = AgentSession(agent)

    await session.start()
    try:
        async for chunk in session.chat(
            "Describe three practical uses of a terrarium."
        ):
            print(chunk, end="", flush=True)
        print()
    finally:
        await session.stop()


asyncio.run(main())
```

`AgentSession` 是 HTTP 與 WebSocket 層所使用、較適合 transport 的包裝器。底下仍然是同一個 agent；它只是讓你在每次 `chat(...)` 呼叫時取得 `AsyncIterator[str]`。

## 步驟 5 —— 嵌入整個 terrarium

目標：從 Python 驅動一套多代理配置，而不是透過 CLI。

```python
import asyncio

from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.runtime import TerrariumRuntime


async def main() -> None:
    config = load_terrarium_config("@kt-biome/terrariums/swe_team")
    runtime = TerrariumRuntime(config)

    await runtime.start()
    try:
        # runtime.run() drives the main loop until a stop signal.
        # For a script, you can interact through runtime's API or
        # just let the creatures run to quiescence.
        await runtime.run()
    finally:
        await runtime.stop()


asyncio.run(main())
```

如果你想用程式方式去**控制**正在執行的 terrarium（向 channel 傳送訊息、啟動 creature、觀察訊息），請使用 `TerrariumAPI`（`kohakuterrarium.terrarium.api`）。這也是 terrarium 管理工具在底層所走的同一個 facade。

## 步驟 6 —— 把代理當成值來組合

「代理是 Python 物件」真正有威力的地方，在於你可以把一個代理放進任何其他東西裡：外掛裡、trigger 裡、工具裡、另一個代理的 output 模組裡。[composition algebra](../concepts/python-native/composition-algebra.md) 提供了一組運算子（`>>`、`|`、`&`、`*`）來表示常見形狀 —— sequence、fallback、parallel、retry。當一串普通函式的 pipeline 看起來開始很自然時，就可以考慮改用這些運算子。

## 你學到了什麼

- `Agent` 就是一般的 Python 物件 —— 建立、啟動、注入、停止。
- `set_output_handler` 可以替換輸出 sink。`AgentSession.chat()` 則把它變成 async iterator。
- `TerrariumRuntime` 也能以相同形狀執行整套多代理設定。
- CLI 只是這些物件的一個使用者；你的應用程式也可以是另一個。

## 接下來讀什麼

- [Agent as a Python object](../concepts/python-native/agent-as-python-object.md) —— 這個概念本身，以及它解鎖的模式。
- [Programmatic usage guide](../guides/programmatic-usage.md) —— 面向任務的 Python 介面參考。
- [Composition algebra](../concepts/python-native/composition-algebra.md) —— 用於把代理接進 Python pipeline 的運算子。
- [Python API reference](../reference/python.md) —— 精確簽章。