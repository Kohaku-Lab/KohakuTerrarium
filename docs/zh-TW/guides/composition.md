---
title: 組合代數
summary: 在純 Python 中使用序列／平行／後備／重試運算子，將 agent 與非同步 callable 串接在一起。
tags:
  - guides
  - python
  - composition
---

# 組合

給想直接從純 Python 進行多 agent 編排、而不想先建立生態瓶的讀者。

組合代數將 agent 與非同步 callable 視為可組合的單元。四個運算子（`>>`、`&`、`|`、`*`）分別涵蓋序列、平行、後備與重試。所有結果都會回傳一個你可以繼續組合的 `BaseRunnable`。

概念預習：[組合代數](../concepts/python-native/composition-algebra.md)、[作為 Python 物件的 agent](../concepts/python-native/agent-as-python-object.md)。

當你想把迴圈放在 creature 外面時，請使用本指南——例如 writer ↔ reviewer 直到通過、平行 ensemble、由便宜到昂貴的後備鏈。若你要的是具有共享頻道的橫向多 agent 系統，請改用[生態瓶](terrariums.md)。

## 運算子

| Op | 意義 |
|---|---|
| `a >> b` | 序列：`b(a(x))`。會自動攤平。右側若為 dict，會轉成 `Router`。 |
| `a & b` | 平行：`asyncio.gather(a(x), b(x))`。回傳 list。 |
| `a \| b` | 後備：若 `a` 丟出例外，則改試 `b`。 |
| `a * N` | 若發生例外，最多額外重試 `a` `N` 次。 |

優先順序：`*` > `|` > `&` > `>>`。

組合器：

- `Pure(fn_or_value)` — 包裝一般 callable。
- `.map(fn)` — 對輸出做後置轉換。
- `.contramap(fn)` — 對輸入做前置轉換。
- `.fails_when(pred)` — 當 predicate 命中時丟出例外（可與 `|` 組合）。
- `pipeline.iterate(stream)` — 將 pipeline 套用到 async iterable 的每個元素。

## `agent` 與 `factory`

兩種 agent 包裝器：

- `agent(config_or_path)` — **持久型** agent（async context manager）。對話上下文會在多次呼叫間累積。適合單次較長的互動。
- `factory(config)` — **逐次呼叫** agent。每次呼叫都建立全新的 agent；不會承接狀態。適合無狀態 worker。

```python
from kohakuterrarium.compose import agent, factory

async with await agent("@kt-biome/creatures/swe") as swe:
    r1 = await swe("Read the repo.")
    r2 = await swe("Now fix the auth bug.")   # same conversation

coder = factory(some_config)
r1 = await coder("Task 1")                    # fresh agent
r2 = await coder("Task 2")                    # another fresh agent
```

## Writer ↔ reviewer 迴圈

反覆執行一條雙 agent pipeline，直到 reviewer 核准：

```python
import asyncio
from kohakuterrarium.compose import agent
from kohakuterrarium.core.config import load_agent_config

def make(name, prompt):
    c = load_agent_config("@kt-biome/creatures/general")
    c.name, c.system_prompt = name, prompt
    c.tools, c.subagents = [], []
    return c

async def main():
    async with await agent(make("writer", "You are a writer.")) as writer, \
               await agent(make("reviewer", "Strict reviewer. Say APPROVED when good.")) as reviewer:

        pipeline = writer >> (lambda text: f"Review this:\n{text}") >> reviewer

        async for feedback in pipeline.iterate("Write a haiku about coding."):
            print(f"Reviewer: {feedback[:120]}")
            if "APPROVED" in feedback:
                break

asyncio.run(main())
```

`.iterate()` 會將 pipeline 的輸出回灌為下一次輸入，產生一個可用原生 `async for` 迴圈處理的 async stream。

## 平行 ensemble 與挑選最佳結果

平行執行三個 agent，保留最長的答案：

```python
from kohakuterrarium.compose import factory

fast = factory(make("fast", "Answer concisely."))
deep = factory(make("deep", "Answer thoroughly."))
creative = factory(make("creative", "Answer imaginatively."))

ensemble = (fast & deep & creative) >> (lambda results: max(results, key=len))
best = await ensemble("What is recursion?")
```

`&` 會派發到 `asyncio.gather`，因此三者會並行執行，你付出的會是最大延遲，而不是總和。

## 重試 + 後備鏈

先讓昂貴的 expert 試兩次，再後備到便宜的 generalist：

```python
safe = (expert * 2) | generalist
result = await safe("Explain JSON-RPC.")
```

也可搭配基於錯誤條件的後備：

```python
cheap = fast.fails_when(lambda r: len(r) < 50)
pipeline = cheap | deep            # if fast returns < 50 chars, try deep
```

## 路由

`>>` 右手邊若是 dict，會變成 `Router`：

```python
router = classifier >> {
    "code":   coder,
    "math":   solver,
    "prose":  writer,
}
```

上游步驟應輸出一個 dict `{classifier_key: payload}`；router 會挑選對應的分支。很適合「先分類，再派發」這類模式。

## 混用 agent 與函式

一般 callable 會自動以 `Pure` 包裝：

```python
pipeline = (
    writer
    >> str.strip                      # zero-arg callable on the output
    >> (lambda t: {"text": t})        # lambda
    >> reviewer
    >> json.loads                     # parse reviewer's JSON response
)
```

同步與非同步 callable 都能使用；若為 async，會自動 await。

## Side-effect logging

```python
from kohakuterrarium.compose.effects import Effects

effects = Effects()
logged = effects.wrap(pipeline, on_call=lambda step, x, y: print(f"{step}: {x!r} -> {y!r}"))
result = await logged("input")
```

這對於除錯 pipeline 流程很有用，而且不需要改動 pipeline 本身。

## 何時應改用 terrarium

以下情況適合選 terrarium：

- Creatures 需要*持續*執行，並依自己的排程對訊息作出反應。
- 你需要熱插拔 creatures，或需要外部可觀測性。
- 多個 creatures 共用同一個工作空間（scratchpad、頻道），且需要 `Environment` 隔離。

以下情況適合選 composition：

- 你的應用程式本身就是協調者，並按需呼叫 agents。
- pipeline 生命週期很短（以 request 為範圍，而非長時間執行）。
- 你想使用原生 Python 控制流程（`for`、`if`、`try`、`gather`）。

## 疑難排解

- **持久型 `agent()` 在重複使用時丟出例外。** 它是 async context manager——請放在 `async with` 內使用。
- **Pipeline 意外回傳 list。** 你在某處用了 `&`；結果會是 list。加上 `>> (lambda results: ...)` 將其收斂。
- **Retry 沒有重試。** `* N` 只會在發生例外時觸發。請用 `.fails_when(pred)` 將「看起來像失敗的成功」轉成例外。
- **步驟之間型別不相容。** 每一步的輸出都會成為下一步的輸入。插入一個 `Pure` 函式（或 lambda）來轉接。

## 另請參見

- [程式化使用](programmatic-usage.md) — `Terrarium`、`Creature`、`Studio` 與底層 `Agent` API。
- [概念 / 組合代數](../concepts/python-native/composition-algebra.md) — 設計理由。
- [參考 / Python API](../reference/python.md) — `compose.core`、`compose.agent`、運算子簽章。
- [examples/code/](../../examples/code/) — `review_loop.py`、`ensemble_voting.py`、`debate_arena.py`、`smart_router.py`、`pipeline_transforms.py`。
