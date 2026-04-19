---
title: 生態瓶
summary: 橫向接線層——頻道處理選用流量、輸出接線處理確定性邊，再疊上熱插拔與觀察。
tags:
  - concepts
  - multi-agent
  - terrarium
---

# 生態瓶

## 它是什麼

**生態瓶 (terrarium)** 是一個把多隻生物一起執行的純接線層。它自己沒有 LLM、沒有智慧，也不做決策。它只做兩件事：

1. 它是一個管理生物生命週期的**執行期**。
2. 它擁有一組生物之間可用來互相對話的**共用頻道**。

這就是它全部的契約。

```
  +---------+       +---------------------------+
  |  User   |<----->|        Root Agent         |
  +---------+       |  (terrarium tools, TUI)   |
                    +---------------------------+
                          |               ^
            sends tasks   |               |  observes
                          v               |
                    +---------------------------+
                    |     Terrarium Layer       |
                    |   (pure wiring, no LLM)   |
                    +-------+----------+--------+
                    |  swe  | reviewer |  ....  |
                    +-------+----------+--------+
```

## 為什麼它存在

當生物變得可攜——一隻生物能單獨執行，同一份設定也能獨立運作——你就需要一種方法把它們組合起來，同時又不強迫它們彼此知道對方的存在。生態瓶就是這個方法。

它維持的核心不變條件是：生物永遠不知道自己在生態瓶裡。它只知道要監聽哪些頻道名稱、往哪些頻道名稱送訊息，就這樣而已。把它從生態瓶拿出來，它仍然可以作為獨立生物執行。

## 我們怎麼定義它

生態瓶設定：

```yaml
terrarium:
  name: my-team
  root:                         # 可選；位於團隊外、面向使用者的 agent
    base_config: "@pkg/creatures/general"
    system_prompt_file: prompts/root.md   # 團隊專用的委派提示詞
  creatures:
    - name: swe
      base_config: "@pkg/creatures/swe"
      output_wiring: [reviewer]           # 確定性邊 → reviewer
      channels:
        listen:    [tasks, feedback]
        can_send:  [status]
    - name: reviewer
      base_config: "@pkg/creatures/swe"   # reviewer 角色來自 prompt，而不是專用生物
      system_prompt_file: prompts/reviewer.md
      channels:
        listen:    [status]
        can_send:  [feedback, status]     # 條件式：approve vs. revise 仍走頻道
  channels:
    tasks:    { type: queue }
    feedback: { type: queue }
    status:   { type: broadcast }
```

執行期會自動為每隻生物建立一個佇列（名稱就是它自己的名字，方便其他成員私訊它），而如果存在 root，還會建立一個 `report_to_root` 頻道。

## 我們怎麼實作它

- `terrarium/runtime.py` —— `TerrariumRuntime` 以固定順序協調啟動（建立共用頻道 → 建立生物 → 接好 triggers → 最後建立 root，但先不啟動）。
- `terrarium/factory.py` —— `build_creature` 載入生物設定（支援 `@pkg/...` 解析），用共用 environment + 私有 session 建立 `Agent`，為每個 listen 頻道註冊一個 `ChannelTrigger`，並在 system prompt 中注入一段頻道拓樸說明。
- `terrarium/hotplug.py` —— 執行期的 `add_creature`、`remove_creature`、`add_channel`、`remove_channel`。
- `terrarium/observer.py` —— 用於非破壞式監看的 `ChannelObserver`（讓 dashboard 可以旁觀而不消耗訊息）。
- `terrarium/api.py` —— `TerrariumAPI` 是程式介面的 façade；內建的生態瓶管理工具（`terrarium_create`、`creature_start`、`terrarium_send`、…）都透過它路由。

## 因此你可以做什麼

- **明確分工的專家團隊。** 兩隻 `swe` 生物透過 `tasks` / `review` / `feedback` 頻道拓樸協作，而 reviewer 角色則由 prompt 驅動。
- **面向使用者的 root agent。** 見 [root-agent](root-agent.md)。它讓使用者只和一隻 agent 對話，再由那隻 agent 去編排整個團隊。
- **透過輸出接線建立確定性的 pipeline 邊。** 在生物設定裡宣告它的回合結束輸出要自動流向下一階段——不需要依賴 LLM 記得呼叫 `send_message`。
- **熱插拔專家。** 不需重啟，就能在工作階段中途加入新生物；現有頻道會直接接上。
- **非破壞式監看。** 掛上一個 `ChannelObserver`，就能看見 queue 頻道中的每則訊息，而不會和真正的 consumer 搶訊息。

## 與頻道並存的輸出接線

頻道是原本的答案，而且現在仍然是正確答案，適合處理**條件性與選用流量**：會批准*或*要求修改的 critic、任何人都可讀的狀態廣播、群聊式側通道。這些都依賴生物自己呼叫 `send_message`。

輸出接線則是另一條框架層級的路徑：生物在設定裡宣告 `output_wiring`，執行期就會在回合結束時，把 `creature_output` TriggerEvent 直接送進目標的事件佇列。沒有頻道、沒有工具呼叫——這個事件走的是和其他 trigger 相同的路徑。

把接線用在**確定性的 pipeline 邊**（「下一步一定要交給 runner」）。把頻道留給接線無法表達的條件式 / 廣播 / 觀察情境。兩者可以在同一個生態瓶裡自然組合——kt-biome 的 `auto_research` 與 `deep_research` 生態瓶正是這樣做的。

接線的設定形狀與混合模式，請見 [生態瓶指南](../../guides/terrariums.md#output-wiring)。

## 說實話，我們的定位

我們把生態瓶視為橫向多代理的**一種提案架構**，而不是已經完全定案的唯一答案。各個部件今天已經可以一起工作（接線 + 頻道 + 熱插拔 + 觀察 + 對 root 的生命週期回報），而且 kt-biome 的生態瓶也把這整套從頭到尾跑通了。我們仍在學習的是慣用法：什麼時候該優先用接線、什麼時候該用頻道；要怎麼在不手刻頻道 plumbing 的前提下表達條件分支；要怎麼讓 UI 對接線活動的呈現能和頻道流量並列。

當工作流本質上就是多生物協作，而且你希望生物保持可攜時，就用它。當任務比較自然地在一隻生物內部拆解時，就用子代理（縱向）——對多數「我需要上下文隔離」的直覺來說，縱向通常更簡單。兩種都合理；框架不替你做決定。

至於我們正在探索的完整改進方向（UI 中接線事件的呈現、條件式接線、內容模式、接線熱插拔），請參見 [ROADMAP](../../../ROADMAP.md)。

## 不要被它框住

沒有 root 的生態瓶是合理的（無頭協作工作）。沒有生物的 root，則是一隻附帶特殊工具的獨立 agent。一隻生物在不同執行中，可以屬於零個、一個或多個生態瓶——生態瓶不會污染生物本身。

## 另見

- [多代理概覽](README.md) —— 縱向與橫向。
- [Root 代理](root-agent.md) —— 位於團隊外、面向使用者的生物。
- [頻道](../modules/channel.md) —— 生態瓶所由之構成的原語。
- [ROADMAP](../../../ROADMAP.md) —— 生態瓶接下來的方向。
