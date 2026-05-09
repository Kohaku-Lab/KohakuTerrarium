---
title: 第一個生態瓶
summary: 用頻道與輸出接線組合兩隻生物，然後加入 root 以提供互動介面。
tags:
  - tutorials
  - terrarium
  - multi-agent
---

# 第一個生態瓶

**問題：**你想讓兩隻生物彼此合作——一隻 writer 產出內容，另一隻 reviewer 負責評論——而且你想看見訊息如何在它們之間流動。

**完成狀態：**你會有一份包含兩隻生物與兩個頻道的 terrarium 設定，並在 TUI 下執行，清楚看見訊息如何從一方傳到另一方。

**前置需求：**[第一隻生物](first-creature.md)。你應該已安裝好
`kt-biome`，並且能用 `kt run` 執行單一生物。

生態瓶是**執行期引擎**：它擁有頻道圖、生物生命週期、輸出接線、以及在圖變化時跟著走的拓樸 + session 記帳。它本身不執行 LLM、也沒有推理迴圈 —— LLM 與推理都活在它內部的生物裡。完整契約請參見
[terrarium 概念](../concepts/multi-agent/terrarium.md)。

## 步驟 1 —— 建立資料夾

```bash
mkdir -p terrariums
```

你可以把 terrarium 設定放在任何地方；慣例上會在生物旁邊放一個
`terrariums/` 資料夾。

## 步驟 2 —— 撰寫 terrarium 設定

`terrariums/writer-team.yaml`：

```yaml
# Writer + reviewer team.
#   tasks    -> writer  -> review  -> reviewer
#                       <- feedback <- reviewer

terrarium:
  name: writer_team

  creatures:
    - name: writer
      base_config: "@kt-biome/creatures/general"
      system_prompt: |
        You are a concise writer. When you receive a message on
        `tasks`, write a short draft and send it to `review` using
        send_message. When you receive feedback, revise and resend.
      channels:
        listen:    [tasks, feedback]
        can_send:  [review]

    - name: reviewer
      base_config: "@kt-biome/creatures/general"
      system_prompt: |
        You critique drafts. When you receive a message on `review`,
        reply with one or two concrete improvement suggestions on
        `feedback` using send_message. If the draft is good, say so.
      channels:
        listen:    [review]
        can_send:  [feedback]

  channels:
    tasks:    { type: queue, description: "Incoming work for the writer" }
    review:   { type: queue, description: "Drafts sent to the reviewer" }
    feedback: { type: queue, description: "Review notes sent back" }
```

這段接線做了什麼：

- `listen` 會在生物上註冊 `ChannelTrigger` —— 當訊息落到其中一個頻道時，生物就會被喚醒並看見該訊息。
- `can_send` 列舉出生物的 `send_message` 工具允許寫入哪些頻道。生物無法觸及不在這份清單中的頻道。
- 頻道只在 `channels:` 中宣告一次（帶可選的一行描述）。所有頻道都是廣播：每個 listener 都會收到每一次 send。

內嵌的 `system_prompt:` 會附加到繼承而來的基底提示詞後面。這裡這樣做是為了讓教學保持自成一體；實際使用時更建議用
`system_prompt_file:`。

## 步驟 3 —— 檢查拓樸（可選）

```bash
kt terrarium info terrariums/writer-team.yaml
```

這會印出生物、它們的 listen/send 頻道集合，以及頻道定義。在執行前做一次 sanity check 很有幫助。

## 步驟 4 —— 執行它

```bash
kt terrarium run terrariums/writer-team.yaml --mode tui --seed "write a one-paragraph product description for a smart kettle" --seed-channel tasks
```

TUI 會為每隻生物開一個分頁，也會為每個頻道開一個分頁。`--seed`
會在啟動時把你的提示注入到 `seed-channel`（預設為 `seed`；這裡我們改成
`tasks`）。writer 會被喚醒、產出草稿，並送到
`review`。reviewer 會被喚醒、進行審查，然後送到 `feedback`。接著 writer 會再次被喚醒並修訂。

你可以在頻道分頁中觀察原始訊息流，也可以在生物分頁中看見每一隻生物各自的推理。

## 步驟 5 —— 用輸出接線讓交接更可靠

頻道很適合處理條件式 / 選擇性 / 廣播型的流量——reviewer 的「approve 還是 revise」決策就是一個真正的分支，應該活在頻道上。但 writer → reviewer 這條邊是**確定性的**：每次 writer 完成一輪時，reviewer 都應該看見它。若依賴 writer 的 LLM 去記得呼叫
`send_message("review", ...)`，那就是舊有的失敗模式。

框架提供了一個直接替代方案：**輸出接線**。在生物設定中宣告這條管線邊，runtime 就會在回合結束時把 `creature_output` 事件直接送進目標的事件佇列——雙方都不需要呼叫 `send_message`。

更新 `terrariums/writer-team.yaml`：

```yaml
terrarium:
  name: writer_team
  creatures:
    - name: writer
      base_config: "@kt-biome/creatures/general"
      system_prompt: |
        You write short product copy. You receive a brief on `tasks`
        and a critique on `feedback`. When you receive feedback, revise
        your draft based on it.
      output_wiring:
        - reviewer                # every writer turn-end → reviewer
      channels:
        listen: [tasks, feedback]
        can_send: []              # no longer needs to send on `review`
    - name: reviewer
      base_config: "@kt-biome/creatures/general"
      system_prompt: |
        You are a strict reviewer. The writer's draft will arrive as a
        creature_output event. If the draft is good, send "APPROVED:
        <draft>" on `feedback`. If not, send specific revision requests
        on `feedback`.
      channels:
        listen: []                # receives writer's output via wiring
        can_send: [feedback]      # reviewer's decision is conditional — keep on channel
  channels:
    tasks:    { type: queue }
    feedback: { type: queue }
```

有哪些變化：

- Writer 的 `output_wiring: [reviewer]` 取代了 writer 需要往 `review` 頻道送訊息的做法。
- `review` 頻道本身消失了——這條邊改由接線承載。
- Reviewer 仍然使用 `feedback`（頻道），因為「approve 還是 revise」是輸出接線無法表達的條件分支。

重新執行後，即使 writer 完全不需要記得呼叫 `send_message`，整個往返流程仍會完成——接線無論如何都會觸發。

## 步驟 6 —— 為互動式使用加入 root（可選）

頻道 + 接線可以給你一個無頭（headless）的協作團隊。如果你想要一個單一對話介面——使用者只和一個 agent 對話，再由那個 agent 帶動整個團隊——就加入一個**root**：

```yaml
terrarium:
  name: writer_team
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md   # team-specific delegation prompt
  creatures:
    - ...
```

在 terrarium yaml 旁邊建立 `prompts/root.md` —— 它只需要承載委派風格即可；框架會自動產生拓樸感知區塊，列出團隊中的生物與頻道，並強制注入[群組工具](../concepts/glossary.md#group-tools--群組工具)（`group_add_node`、`group_status`、`group_channel`、`group_wire` 等），讓 root 可以從圖內管理團隊。

TUI 會把 root 掛在主分頁上；你和 root 對話，root 再和團隊對話。更多內容請見 [特權節點概念](../concepts/multi-agent/privileged-node.md)。

## 你學到了什麼

- 生態瓶是執行期引擎。它擁有結構（頻道圖、拓樸、session、輸出接線），自己不執行 LLM。
- 生物保持獨立；引擎只告訴它們誰能聽到什麼、誰能傳到哪裡，以及它們在回合結束時的輸出會流向何處。
- 兩種協作機制可以自由組合：
  - **頻道** —— 條件式、選擇性、廣播。由生物自己決定是否傳送，以及傳到哪裡。
  - **輸出接線** —— 確定性的管線邊。每次回合結束都會觸發，不受生物實際做了什麼影響。
- Root 是可選的。無頭工作流程可以略過；想要單一對話介面時再加入。

## 接下來讀什麼

- [生態瓶概念](../concepts/multi-agent/terrarium.md) —— 它的契約與邊界。
- [特權節點概念](../concepts/multi-agent/privileged-node.md) —— 由 `root:` 指定、面向使用者的特權生物。
- [Terrariums 指南](../guides/terrariums.md) —— 實用導向的操作參考。
- [Channel 概念](../concepts/modules/channel.md) —— 廣播語意、observers，以及頻道如何跨越模組邊界。
