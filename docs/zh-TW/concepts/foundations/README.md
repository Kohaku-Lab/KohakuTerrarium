---
title: 基礎
summary: 為什麼這個框架存在、它模型裡的 agent 是什麼、六個模組如何在執行期組合。
tags:
  - concepts
  - foundations
---

# 基礎

這一組文件回答三個問題：

1. **為什麼這個框架存在？** 看 [Why KohakuTerrarium](why-kohakuterrarium.md) — 每一個 agent 產品都會重做同一套底層機制；這個框架把那套機制獨立出來一次做好。
2. **在這個框架的模型裡，一隻 agent 是什麼？** 看 [什麼是 agent](what-is-an-agent.md) — 從聊天機器人出發分四個階段，推導出六模組的生物結構。
3. **這六個模組實際上怎麼組合？** 看 [組合一隻 agent](composing-an-agent.md) — 六個模組如何透過一個統一的 `TriggerEvent` envelope 在執行期互動。

讀完這三份文件，後面每一份核心概念文件都會有熟悉的語境。
