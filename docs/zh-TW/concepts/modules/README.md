---
title: 模組
summary: 一份文件介紹一個模組 — 控制器、輸入、觸發器、工具、子代理、輸出，外加橫跨多處的模組。
tags:
  - concepts
  - modules
  - overview
---

# 模組

一隻生物由六個「一等公民」模組組成，加上幾個橫跨多處、沒有乾淨塞進六模組分類的模組：頻道、工作階段 / 環境、記憶與壓縮、外掛。

## 六模組

- [控制器](controller.md) — 推理迴圈：接 LLM 串流、解析工具呼叫、派發回饋。
- [輸入](input.md) — 特殊的觸發器，把使用者訊息帶進事件佇列。
- [觸發器](trigger.md) — 任何不是使用者輸入的事件來源：計時器、idle、頻道、webhook、監控條件。
- [工具](tool.md) — 有名字的能力，LLM 可以帶參數呼叫：shell 指令、檔案編輯、網頁搜尋…
- [子代理](sub-agent.md) — 由父生物派生出來、上下文獨立、只持有父代理工具子集的巢狀生物。
- [輸出](output.md) — 路由器，接控制器產生的所有東西 (文字、工具活動、token 用量) 並分流到多個 sink。

## 橫跨多處的模組

- [頻道](channel.md) — 具名的廣播管道，支撐多代理與跨模組通訊。
- [工作階段與環境](session-and-environment.md) — 每隻生物的私有狀態 (session) vs. 整個生態瓶共用的狀態 (environment)。
- [記憶與壓縮](memory-and-compaction.md) — 工作階段 store 同時做為可搜尋的記憶；非阻塞的壓縮怎麼讓上下文保持在預算內。
- [外掛](plugin.md) — 修改模組之間**連接**的程式碼 — prompt 外掛與 lifecycle 外掛。
