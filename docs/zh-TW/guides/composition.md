---
title: 組合代數
summary: 用 sequence / parallel / fallback / retry 運算子，在純 Python 裡把 agent 與 async callable 串接起來。
tags:
  - guides
  - python
  - composition
---

# 組合

寫給想在純 Python 裡做多代理編排、又不想先架一個生態瓶 (terrarium) 的讀者。

組合代數把 agent 與 async callable 都當成可組合的單元。四個運算子 (`>>`、`&`、`|`、`*`) 涵蓋序列、平行、fallback 與重試。所有結果都回傳一個可以繼續組合的 `BaseRunnable`。

概念入門：[組合代數](../concepts/python-native/composition-algebra.md)、[Agent 作為 Python 物件](../concepts/python-native/agent-as-python-object.md)。

當你想把迴圈放在生物 (creature) 外面時，用這份指南，例如 writer ↔ reviewer 跑到通過為止、平行 ensemble、由便宜到昂貴的 fallback 鏈。要的是有共享頻道的橫向多代理系統時，改用[生態瓶](terrariums.md)。

## 運算子

| 運算子 | 意義 |
|---|---|
| `a >> b` | 序列：`b(a(x))`。自動攤平。右邊放 dict 會變成 `Router`。 |
| `a & b` | 平行：兩邊並行執行，回傳結果的 **tuple**。第一個失敗發生時，其餘還活著的兄弟會先被取消並等待完成，例外才往上傳。 |
| `a \| b` | Fallback：`a` 拋錯時，用原始輸入跑 `b`。`b` 也失敗時，`a` 的例外會以 `__cause__` 鏈上去。 |
| `a * N` | 重試：遇到例外最多嘗試 `N` 次 (立即重試、不延遲)。 |

優先順序遵循 Python 的運算子：`*` 綁最緊，再來是 `>>`、
然後 `&`、最後 `|`。所以 `a >> b & c` 是 `(a >> b) & c`、
`a & b | c` 是 `(a & b) | c`。不確定就加括號。

組合子與方法：

- `Pure(fn)` / `pure(fn)`：包裝普通的同步或非同步 callable。
- `.retry(max_attempts, *, backoff=0.0, max_backoff=30.0)`：像 `* N`
  但帶指數退避：第一次失敗後睡 `backoff` 秒，每次加倍，
  上限 `max_backoff`。
- `.map(fn)`：後置轉換輸出 (`self >> pure(fn)`)。
- `.contramap(fn)`：前置轉換輸入 (`pure(fn) >> self`)。
- `.fails_when(pred)`：輸出符合 predicate 時拋 `ValueError`
  (可以跟 `|` 組合)。
- `pipeline.iterate(initial_input)`：async iterator，把每次輸出
  回灌成下一次輸入；`it.feed(value)` 可覆寫下一次輸入。

## `agent` vs `factory`

兩個 agent 包裝器，吃同樣的關鍵字參數：

```python
await agent(config, *, engine=None, pwd=None, llm=None)   # -> AgentRunnable
factory(config, *, engine=None, pwd=None, llm=None)       # -> AgentFactory
```

- `config`：`AgentConfig`、檔案系統路徑，或
  `@pkg/creatures/<name>` 參照。
- `engine`：要生成進去的共用 `Terrarium`。省略時，每個包裝器
  自己架一個私有引擎，並隨 runnable 一起收掉；傳共用引擎可以
  把啟動成本攤平到多個 compose agent 上 (此時關閉 runnable
  只會移除它的生物，不會動到你的引擎)。
- `pwd`：生物的工作目錄 (不動全域 chdir)。
- `llm`：profile 名稱、`LLMProfile` 或 provider 實例，文法同
  `Agent.build` / `Terrarium.add_creature`。

`agent(...)` 是**持久的**：立即啟動、對話跨呼叫累積、必須關閉
(用 `async with`)。`factory(...)` 是**逐呼叫的**：每次呼叫一個
全新的 agent，沒有狀態殘留，也不用管生命週期。

```python
from kohakuterrarium.compose import agent, factory

async with await agent("@kt-biome/creatures/swe", llm="fast") as swe:
    r1 = await swe("Read the repo.")
    r2 = await swe("Now fix the auth bug.")   # 同一段對話

coder = factory(some_config)
r1 = await coder("Task 1")                    # 全新的 agent
r2 = await coder("Task 2")                    # 又是另一個全新的 agent
```

建構是嚴格的：壞路徑拋 `ConfigNotFoundError`、未安裝的套件拋
`PackageNotInstalledError`、壞的 `llm` 選擇器拋
`LLMNotConfiguredError`，在 `agent()` / 第一次呼叫 `factory`
的當下就拋，而不是之後變成一個空回覆。

## Writer ↔ reviewer 迴圈

迭代一條雙 agent 的 pipeline，直到 reviewer 通過：

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

`.iterate()` 把 pipeline 的輸出回灌成下一次輸入，產生一條可以用原生 `async for` 迭代的 async 串流。

## 平行 ensemble + 挑最好的

三個 agent 平行跑，留最長的答案：

```python
from kohakuterrarium.compose import factory

fast = factory(make("fast", "Answer concisely."))
deep = factory(make("deep", "Answer thoroughly."))
creative = factory(make("creative", "Answer imaginatively."))

ensemble = (fast & deep & creative) >> (lambda results: max(results, key=len))
best = await ensemble("What is recursion?")
```

三者並行執行，所以你付的是最大延遲，不是總和。
Product 結果是一個 tuple，按分支順序排列。某個分支拋錯時，
其他分支會先被取消 (並等待完成)，例外才往上傳，
不會留下脫韁的 agent 繼續燒 LLM 輪次。

## 重試 + fallback 鏈

昂貴的專家試兩次，然後退回便宜的通才：

```python
safe = (expert * 2) | generalist
result = await safe("Explain JSON-RPC.")
```

嘗試之間帶退避：

```python
safe = expert.retry(3, backoff=2.0, max_backoff=30.0) | generalist
```

搭配錯誤判定的 fallback：

```python
cheap = fast.fails_when(lambda r: len(r) < 50)
pipeline = cheap | deep            # fast 回覆少於 50 字元就試 deep
```

整條鏈都失敗時，你接到的例外會以 `__cause__` 帶著最初的失敗，
除錯時保留原始錯誤。

## 路由

`>>` 右邊放 dict 會變成 `Router`：

```python
router = classifier >> {
    "code":   coder,
    "math":   solver,
    "prose":  writer,
    "_default": generalist,       # 選用的 catch-all
}
```

Router 用上游輸出決定分支：2-tuple `(key, payload)` 會把
`payload` 路由到名為 `key` 的分支；其他值則同時當 key 和
payload。沒有符合的分支、也沒有 `_default` 時，拋 `KeyError`。

## 混用 agent 與函式

普通 callable 會自動包成 `Pure`：

```python
pipeline = (
    writer
    >> str.strip                      # 對輸出套用普通 callable
    >> (lambda t: f"Review:\n{t}")    # lambda
    >> reviewer
    >> json.loads                     # 解析 reviewer 的 JSON 回覆
)
```

同步、非同步的 callable 都行；async 會自動 await。

## 什麼時候改用生態瓶

選生態瓶，當：

- 生物需要*持續*運行，按自己的節奏對訊息做出反應。
- 你需要熱插拔生物或外部可觀測性。
- 多隻生物共享工作空間 (scratchpad、頻道)，需要 `Environment` 隔離。

選組合，當：

- 你的應用程式才是 orchestrator，按需呼叫 agent。
- Pipeline 是短命的 (請求範圍，不是長駐)。
- 你想要原生的 Python 控制流 (`for`、`if`、`try`、`gather`)。

兩者可以混搭：給 `agent()` / `factory()` 傳 `engine=`，
你的 compose pipeline 就會把生物生成到長駐生態瓶用的同一個引擎裡。

## 疑難排解

- **持久的 `agent()` 關閉後重用會拋錯。** 它是 async context
  manager，所有呼叫都放在 `async with` 裡。
- **Pipeline 意外回傳 tuple。** 你某處用了 `&`；結果就是 tuple。
  加 `>> (lambda results: ...)` 收斂。
- **重試沒有重試。** `* N` 是由例外觸發的。用
  `.fails_when(pred)` 把長得不對的成功轉成例外。
- **步驟之間型別不合。** 每一步的輸出就是下一步的輸入。插一個
  `pure` 函式 (或 lambda) 來轉接。

## 另見

- [程式化使用](programmatic-usage.md)：底層的 `Agent` / `Terrarium` / `Creature` API。
- [概念 / 組合代數](../concepts/python-native/composition-algebra.md)：設計理路。
- [參考 / Python API](../reference/python.md#compose)：匯出符號與運算子簽名。
- [`examples/code/`](../../../examples/code/)：`review_loop.py`、`ensemble_voting.py`、`debate_arena.py`、`smart_router.py`、`pipeline_transforms.py`。
