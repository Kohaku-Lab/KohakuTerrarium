---
title: 非阻塞壓縮
summary: 說明當摘要器在背景重建壓縮後的對話時，控制器如何持續運作。
tags:
  - concepts
  - impl-notes
  - compaction
---

# 非阻塞壓縮

## 這要解決的問題

一個連續執行數小時的生物，會不斷累積對話內容。最終，
prompt 會超出模型的上下文預算。標準解法是進行壓縮：
把較早的輪次摘要成一段精簡筆記，近期輪次則保留原始內容。
但壓縮本身也是一次 LLM 呼叫——如果控制器在摘要器工作時
被阻塞，一個常駐型代理就會在重寫 50k tokens 的期間凍結
數十秒。

對於偏向程式編寫代理風格的生物，這或許還能接受；但對於
監控型或對話型生物，這就是產品缺陷。

## 曾考慮的方案

- **同步暫停。** 停止控制器、做摘要、再恢復。
  很簡單，但會造成長時間凍結。
- **交給獨立代理。** 對本質上只是
  「把舊輪次改寫成一段文字」這件事來說太過頭。
- **背景任務 + 原子拼接。** 在控制器運作的同時並行做摘要；
  並在輪次之間替換對話。這就是框架實際採用的做法。

## 我們實際怎麼做

對話在概念上會被分成兩個區域：

```
  [ ----- 壓縮區 ----- ][ --- 即時區（keep_recent_turns）--- ]
           可處理                           原始內容，永不摘要
```

流程如下：

1. 每一輪結束後，compact manager 會檢查
   `prompt_tokens >= threshold * max_tokens`。
2. 如果成立，就發出 `compact_start` 活動事件，並啟動一個
   背景 `asyncio.Task`。
3. 這個任務會：
   - 對壓縮區建立快照，
   - 執行摘要用的 LLM（主控制器的 LLM，或是若有設定則使用
     專用且更便宜的 `compact_model`），
   - 產生一份摘要，並原樣保留決策、檔案路徑、錯誤字串，以及
     其他高訊號 token。
4. 與此同時，控制器會持續處理事件——工具照常執行、
   子代理照常生成、使用者也可以繼續輸入。
5. 當摘要完成後，manager 會等待目前輪次結束，然後**以原子方式**
   重寫對話：
   - 將舊的壓縮區替換為 `{system prompt, 先前摘要,
     新摘要, 即時區原始訊息}`，
   - 並發出 `compact_complete` 事件。

## 維持不變的條件

- **不在輪次中途替換。** 對話只會在輪次之間被替換，
  因此控制器在一次 LLM 呼叫期間，不會看到訊息突然消失。
- **壓縮期間即時區不會縮小。** 在摘要進行中，新輪次會繼續累積到
  即時區；而拼接時會把這點計算進去。
- **摘要會層層累積。** 下一次壓縮會產生一份包含前一次摘要的摘要，
  因此歷史內容會逐步退化但不會直接遺失。
- **可針對個別生物停用。** `compact.enabled: false` 可完全關閉此功能。

## 程式碼中的位置

- `src/kohakuterrarium/core/compact.py` — 帶有
  start/pending/done 狀態機的 `CompactManager`。
- `src/kohakuterrarium/core/agent.py` — `_init_compact_manager()` 會在
  `start()` 時把 manager 接到 agent 上。
- `src/kohakuterrarium/core/controller.py` — 每輪結束後的 hook，
  會請 manager 評估是否需要壓縮。
- `src/kohakuterrarium/builtins/user_commands/compact.py` — 手動觸發的
  `/compact`。

## 另請參閱

- [記憶與壓縮](../modules/memory-and-compaction.md) — 概念層面的說明。
- [reference/configuration.md — `compact`](../../reference/configuration.md) —
  各生物可調整的設定項目。
