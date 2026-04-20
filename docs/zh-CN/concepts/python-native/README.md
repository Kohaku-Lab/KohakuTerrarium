---
title: Python 原生集成
summary: Agent 作为一等公民的 async Python 值，以及把它们串成 pipeline 的代数。
tags:
  - concepts
  - python
  - overview
---

# Python 原生集成

这一节回答一个问题：「我想直接在 Python 里跑 Agent，而不是跑 CLI 或连 HTTP — 要怎么做？」

- [Agent 作为 Python 对象](agent-as-python-object.md) — 为什么每一个 Agent 都是一个 Python 对象，这个特性解锁了什么，以及嵌入和跑 CLI 有什么不一样。
- [Compose 代数](composition-algebra.md) — 四个运算子 (`>>`、`&`、`|`、`*`) 加上几个 combinator，把 Agent 与 async callable 当成可组合的单元。

这两份文件不是互斥的 — 它们是同一件事的两个层次：Agent 对象是低阶单元，compose 代数是把多个对象串起来的高阶语法。
