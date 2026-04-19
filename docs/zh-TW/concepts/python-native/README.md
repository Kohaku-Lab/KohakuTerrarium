---
title: Python 原生整合
summary: Agent 作為一等公民的 async Python 值，以及把它們串成 pipeline 的代數。
tags:
  - concepts
  - python
  - overview
---

# Python 原生整合

這一節回答一個問題：「我想直接在 Python 裡跑 agent，而不是跑 CLI 或連 HTTP — 要怎麼做？」

- [Agent 作為 Python 物件](agent-as-python-object.md) — 為什麼每一個 agent 都是一個 Python 物件，這個特性解鎖了什麼，以及嵌入和跑 CLI 有什麼不一樣。
- [Compose 代數](composition-algebra.md) — 四個運算子 (`>>`、`&`、`|`、`*`) 加上幾個 combinator，把 agent 與 async callable 當成可組合的單元。

這兩份文件不是互斥的 — 它們是同一件事的兩個層次：agent 物件是低階單元，compose 代數是把多個物件串起來的高階語法。
