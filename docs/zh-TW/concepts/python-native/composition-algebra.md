---
title: 組合代數
summary: 四個操作子與一組 combinators，把 agents 與 async callables 當成可組合單元。
tags:
  - concepts
  - python
  - composition
---

# 組合代數

## 它是什麼

一旦 agent 變成 Python value，你就會想把它們接起來。**組合代數（compose algebra）** 是一小組操作子與 combinators，用來把 agents（以及任何 async callable）視為可組合的單元：

- `a >> b` — 串接（`a` 的輸出變成 `b` 的輸入）
- `a & b` — 平行（兩者一起跑，回傳 `[result_a, result_b]`）
- `a | b` — 後備（如果 `a` 丟例外，就改試 `b`）
- `a * N` — 重試（失敗時額外最多重試 `N` 次）
- `pipeline.iterate(stream)` — 對 async iterable 的每個元素套用整條 pipeline；如果想形成迴圈，也可以把輸出回灌成輸入

所有結果都會回傳一個 `BaseRunnable`，所以你可以繼續往下組。

## 為什麼它存在

生物內部的控制器本來就是一個迴圈。但有時候你想要的是一個 *在生物外面* 的迴圈——例如 writer ↔ reviewer 一直來回直到核准、平行 ensemble 挑出最佳答案、跨 provider 做 retry-with-fallback。這些事情用裸的 `asyncio.gather` 和 `try/except` 當然做得到，但會把呼叫端程式碼弄得很雜。

這些操作子本質上只是包在 asyncio 外面的語法糖。它們沒有引入新的執行模型；只是讓「組合兩隻 agent」讀起來更像「把兩個數字相加」。

## 我們怎麼定義它

`BaseRunnable.run(input) -> Any`（async）是這套協定。任何實作了它的東西都可以被組合。

這些操作子分別是：

- `__rshift__`：把兩側包進 `Sequence`（會自動攤平巢狀 sequence；如果右側是 dict，則會變成 `Router`）
- `__and__`：包進 `Product`；`run(x)` 會對所有分支做 `asyncio.gather`，並把 `x` 廣播成共同輸入
- `__or__`：包進 `Fallback`；發生例外時就往下掉
- `__mul__`：包進 `Retry`；發生例外時最多重跑 N 次

再加上一些 combinators：

- `Pure(value)` — 包住一個普通 value 或 callable；忽略輸入。
- `Router(routes)` — 輸入 `{key: value}` 時，派發到對應的 runnable。
- `.map(fn)` — 先轉換輸入（`contramap`）。
- `.contramap(fn)` — 再轉換輸出。
- `.fails_when(pred)` — 當 predicate 命中時丟出例外；搭配 `|` 很有用。

Agent factories：

- `agent(config)` — 把持久型 agent 包成 runnable。對話上下文會跨呼叫累積。
- `factory(config)` — 每次呼叫都新建 agent。每次 invocation 都 spawn 一隻新的 agent；不保留持久狀態。

## 我們怎麼實作它

`compose/core.py` 放的是基礎協定與 combinator classes。`compose/agent.py` 把 agent 包成 runnable。`compose/effects.py` 則是可選的 instrumentation，用來記錄 pipeline 上的 side-effects。

agent-factory wrappers 會處理生命週期樣板——進入／離開時 start / stop 底層的 `Agent`，並透過 `inject_input` 加上輸入、收集輸出。

## 一個真實範例

```python
import asyncio
from kohakuterrarium.compose import agent, factory
from kohakuterrarium.core.config import load_agent_config

def make_agent(name, prompt):
    c = load_agent_config("@kt-biome/creatures/general")
    c.name, c.system_prompt, c.tools, c.subagents = name, prompt, [], []
    return c

async def main():
    async with await agent(make_agent("writer", "You are a writer.")) as writer, \
               await agent(make_agent("reviewer", "You are a strict reviewer. Say APPROVED if good.")) as reviewer:

        pipeline = writer >> (lambda text: f"Review this:\n{text}") >> reviewer

        async for feedback in pipeline.iterate("Write a haiku about coding"):
            print(f"Reviewer: {feedback[:100]}")
            if "APPROVED" in feedback:
                break

    fast = factory(make_agent("fast", "Answer concisely."))
    deep = factory(make_agent("deep", "Answer thoroughly."))
    safe = (fast & deep) >> (lambda results: max(results, key=len))
    safe_with_retry = (safe * 2) | fast
    print(await safe_with_retry("What is recursion?"))

asyncio.run(main())
```

兩隻 agent、持久對話、回饋迴圈、帶有 fallback 與 retry 的平行 ensemble——全部都在一般 Python 裡完成。

## 因此你可以做什麼

- **Review loops。** Writer `>>` reviewer `.iterate(...)` 直到某個 predicate 成立，不需要再寫新的 orchestration code。
- **Ensembles。** `(fast & deep) >> pick_best` —— 平行跑兩隻 agent，再把結果合併。
- **Fallback chains。** 先試便宜的 provider；失敗再退到更強的。
- **暫時性錯誤的重試。** 任何 runnable 都可以用 `* N` 包起來。
- **串流 pipeline。** `.iterate(async_generator)` 會把每個元素都走完整條 pipeline。

## 不要被邊界綁住

組合代數是可選的。對大多數嵌入式使用情境來說，Creature 設定加上 `Creature.chat()` / `Studio.sessions.chat` 就已經夠了。這些操作子存在的理由，是當你 *真的* 想直接從 Python 做輕量 pipeline、又不想管理長生命週期 terrarium graph 的時候。

狀態說明：這套代數很有用，但仍在演化中——操作子的精確集合未來可能會根據回饋而增加或簡化。可以放心用在內部 pipeline，但如果是 production 用途，建議視為 early-stable。

## 延伸閱讀

- [Agent as a Python object](agent-as-python-object.md) — 這份內容建立其上的基礎。
- [Patterns](../patterns.md) — 混合組合代數與嵌入式 agent 的用法。
- [guides/composition.md](../../guides/composition.md) — 任務導向的使用方式。
- [reference/python.md — kohakuterrarium.compose](../../reference/python.md) — 完整 API。
