---
title: 教學
summary: 循序漸進的引導流程，帶你從零到一個可運作的 agent。
tags:
  - tutorials
  - overview
---

# 教學

教學是任務導向的循序步驟。每一份教學都設定一個明確目標 (「做出你的第一隻生物」、「串起兩隻生物讓它們合作」)，把達成目標需要的所有步驟走一次，並解釋每一步的意圖。

讀教學的目的是**學會怎麼做**，不是弄清楚背後為什麼這樣設計。等你想知道「為什麼」，再去看[核心概念](../concepts/README.md)。

## 可用的教學

- [第一隻生物](first-creature.md) — 建立單一生物設定，在 CLI / TUI / 網頁模式中執行。
- [第一個自訂工具](first-custom-tool.md) — 寫一個 Python 工具、註冊它、接上生物的設定。
- [第一個外掛](first-plugin.md) — 建立 lifecycle 外掛，攔截工具執行的前後兩端。
- [第一個生態瓶](first-terrarium.md) — 用頻道與 output_wiring 把兩隻生物串起來，再加一個 root 提供對話介面。
- [第一次 Python 嵌入](first-python-embedding.md) — 用 Creature.chat、Studio 與 compose 代數在自己的程式碼裡跑 agent。

## 在伺服器上跑起來之後

- [給你的主機加鎖](locking-down-your-host.md) — 四個遞進的認證級別,
  每個 30 秒就能設好。從 Level 0（桌面什麼都不用做）開始,按需求
  爬到主機權杖 / 管理員密碼 / 多使用者。跳過
  [身份驗證](../guides/authentication.md) 裡的理論 — 這篇教學就是
  複製貼上的捷徑。

## 讀完教學之後

- **想理解背後原理** → 看[核心概念](../concepts/README.md)。
- **要查某個欄位或指令** → 看[參考](../reference/README.md)。
- **要處理某個具體任務** → 看[使用指南](../guides/README.md)。
