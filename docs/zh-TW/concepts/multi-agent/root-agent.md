---
title: Root 代理
summary: 位於生態瓶之外、代表使用者的生物——面向使用者的介面、管理工具組、拓樸感知。
tags:
  - concepts
  - multi-agent
  - root
---

# Root 代理

## 它是什麼

**Root 代理 (root agent)** 是一隻坐落在生態瓶*外部*、在其中代表使用者的生物。從結構上說，它其實就只是另一隻生物：同樣的設定、同樣的模組、同樣的生命週期。讓它成為「root」的地方在於：

1. 它被放在團隊之外——使用者和 root 對話；root 再和生態瓶對話。
2. 它會自動獲得**生態瓶管理工具組**（`terrarium_create`、`terrarium_send`、`creature_start`、`creature_stop`、`creature_status`、`terrarium_status`、…）。
3. 它會自動監聽所有共用頻道，並接收專用的 `report_to_root` 佇列。

## 為什麼它存在

裸的生態瓶是無頭的——只有多隻生物透過頻道協作，沒有人在前面駕駛。這對某些環境式工作流是可行的；但若要互動式使用，人類需要一個單一對口。root 就是那個對口。

原則上，你也可以用一隻普通生物再加手動接線來做到這件事；但每次都要把工具組與監聽接線配好，實在很麻煩。把「root」做成生態瓶設定裡的一等位置，就能消掉這些樣板。

## 我們怎麼定義它

```yaml
terrarium:
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md     # 團隊專用的委派提示詞
    controller:
      reasoning_effort: high
  creatures:
    - ...
  channels:
    - ...
```

任何在 agent 設定中合法的東西，放進 `root:` 裡也都合法。繼承（`base_config`）的運作方式也一樣。

撰寫上的注意事項：kt-biome **不會**附帶一個通用的 `root` 生物。每個生態瓶都應該有自己的 `root:` 區塊，以及放在同一處的 `prompts/root.md`，內容要知道自己面對的是哪個團隊——像是「coding → send to `driver`」就會比「coding → send to the swe creature」來得自然。其他事情由框架處理。

無論你在 root 的設定裡寫了什麼，執行期都會對它做三件事：

- 把管理工具組（`terrarium_create`、`terrarium_send`、`creature_start`、`creature_stop`、`creature_status`、`terrarium_status`、…）注入它的 registry。
- 自動監聽每個生物頻道，讓它看見整個團隊的活動。
- 自動產生一段「生態瓶感知」提示詞區塊，列出所綁定生態瓶中的生物與頻道，並附加到 root 的 system prompt。
- 讓 root 成為使用者直接互動的那一個（TUI / CLI / web）。

你的 `prompts/root.md` 只需要負責委派風格 / 個性——拓樸感知由框架提供。

## 我們怎麼實作它

`terrarium/factory.py:build_root_agent` 會在*所有生物建立完成之後*被呼叫。它會用共用環境建立 root（這樣管理工具才能看見生物與頻道）、把 `TerrariumToolManager` 註冊進它的 registry，並把輸出接回使用者 transport。

root 會先被建立，但不會立刻啟動，直到使用者真的開始和生態瓶互動為止——這讓 `kt terrarium run` 可以在 root 醒來前先顯示團隊狀態。

## 因此你可以做什麼

- **面向使用者的指揮者。** 使用者對 root 說：「叫 SWE 修 auth bug，然後再叫 reviewer 批准它。」root 會透過頻道送訊息，並監看 `report_to_root` 以得知完成情況。
- **動態團隊建構。** root 可以根據當前任務 `creature_start` 新的專家，再在完成後 `creature_stop` 它們。
- **生態瓶啟動器。** 一個 root agent 本身也可以透過 `terrarium_create` 建立並管理*其他*生態瓶。
- **可觀測性的樞紐。** 因為 root 會自動監聽所有東西，它自然就是執行摘要外掛、告警規則等工作的最佳位置。

## 不要被它框住

沒有 root 的生態瓶完全合理——像是無頭 pipeline、cron 驅動的協調、批次作業。root 只是為互動式使用提供的便利。而且 root 依然「只是一隻生物」——任何能套用在普通生物上的模式（互動型子代理、外掛、自訂工具），一樣都能套用到 root 身上。

## 另見

- [生態瓶](terrarium.md) —— root 所疊加其上的那一層。
- [多代理概覽](README.md) —— root 在整個模型中的位置。
- [reference/builtins.md — terrarium_* tools](../../reference/builtins.md) —— 生態瓶管理工具組。
