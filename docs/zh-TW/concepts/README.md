---
title: 核心概念
summary: 生物、生態瓶、頻道、觸發器、外掛與 compose 代數的心智模型。
tags:
  - concepts
  - overview
---

# 核心概念

概念文件教的是心智模型。它不是參考 — 欄位名稱、函式簽名、指令都放在[參考](../reference/README.md)裡。
它也不是使用指南 — 一步一步的做法放在[使用指南](../guides/README.md)裡。

概念文件的目的是讓你理解**這個框架為什麼是現在這個樣子**。讀完之後，你應該能夠看著一份從來沒看過的設定檔，大致猜到它想幹嘛，而不必先回頭查所有欄位。

## 讀的順序

這份文件有明確的閱讀順序：

1. [基礎](foundations/README.md) — 為什麼這個框架存在，一隻生物到底是什麼，以及六個模組如何在執行期組合。
2. [模組](modules/README.md) — 每個生物模組一份文件：控制器、輸入、觸發器、工具、子代理、輸出，以及橫跨多處的 channel / session / memory / plugin。
3. [多代理系統](multi-agent/README.md) — 縱向 (子代理) 與橫向 (生態瓶 + 頻道 + 輸出接線) 兩個軸向，何時挑哪一個。
4. [Python 原生整合](python-native/README.md) — agent 作為一等公民的 async Python 值，以及把它們串成 pipeline 的代數。
5. [模式](patterns.md) — 組合現有模組所得到的典型用法。
6. [邊界](boundaries.md) — 生物抽象是預設值而不是鐵律；框架何時可以彎曲自己的抽象；框架何時根本不適合你。
7. [詞彙表](glossary.md) — 文件中用到的術語的白話解釋。

## 跨機器部署

如果你想讓生物執行在和 dashboard 不同的機器上（GPU 伺服器、
sandbox VM、雲端節點），請依序讀：

1. [生態瓶](multi-agent/terrarium.md) — Lab 包覆的引擎。
2. [Studio](studio.md) — Lab 在單機與多節點之間保持一致的管理表面。
3. [Laboratory](laboratory.md) — 線路協定、session 同步、resume、identity 模型。
4. 操作員 playbook：[guides/laboratory.md](../guides/laboratory.md)。

## 結構

```
concepts/
├── foundations/         為什麼這個框架存在；什麼是 agent；如何組合一隻。
├── modules/             每個生物模組一份文件。
├── python-native/       Agent 作為 Python 值；compose 代數。
├── multi-agent/         Terrarium 引擎 + 特權節點 + 動態圖。
├── studio.md            Terrarium 之上的管理層。
├── laboratory.md        跨多台機器的網路層。
├── impl-notes/          值得教學的特定實作選擇。
├── patterns.md          模組組合後浮現的典型用法。
├── boundaries.md        抽象是預設，不是鐵律。
└── glossary.md          白話的一段式定義。
```

## 實作筆記

不是必讀，但對想理解系統實際怎麼運作的人 (通常是貢獻者) 很有幫助：

- [實作筆記](impl-notes/README.md) — 特定子系統的深度剖析。
