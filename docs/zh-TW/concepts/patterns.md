---
title: 模式
summary: 把既有模組組合起來就會自然長出的做法——群組聊天、智慧守門員、自適應監看器、輸出接線。
tags:
  - concepts
  - patterns
---

# 模式

這一頁上的每個模式，都不需要新增任何框架功能。
它們只是把已經存在的模組組起來而已。這裡每一種形狀，你今天都能用六個模組、channels、plugins，以及 Python 原生基底做出來。

把這一頁當成一份型錄，或者把它當成一個證明：這些抽象之所以值得保持精簡，就是因為它們真的能自然組出有用的東西。

## 1. 用 tool + trigger 做群組聊天

**形狀。** 一隻生物有 `send_message` tool。另一隻生物有 `ChannelTrigger`，監聽同一個 channel 名稱。當前者送出訊息時，後者就會用 `channel_message` event 被喚醒。

**為什麼可行。** channel 本質上就是具名佇列。tool 往裡寫；trigger 從裡讀。兩個模組彼此都不知道對方的存在。

**適合用在。** 你想要橫向多代理系統，但又不想引入 `terrarium.yaml` 那套 machinery；或者發送端是否要送出訊息本身就是條件式決策（批准 vs. 修改、保留 vs. 丟棄）。

**最小設定。**

```yaml
# creature_a
tools:
  - name: send_message

# creature_b
triggers:
  - type: channel
    options:
      channel: chat
```

## 1b. 用 output wiring 做確定性的 pipeline 邊

**形狀。** 一隻生物在設定中宣告 `output_wiring:`，指定一個或多個目標生物。每次 turn 結束時，框架都會往每個目標的事件佇列送出一個 `creature_output` `TriggerEvent`——攜帶該生物最後一輪 assistant 產生的文字（如果 `with_content: false`，則只送 lifecycle ping）。

**為什麼可行。** 這個接線存在於框架層，不需要 sender 呼叫 tool，也不需要 receiver 訂閱 trigger，中間也沒有 channel。目標端透過原本就拿來處理使用者輸入、timer 觸發、channel 訊息的同一條 `agent._process_event` 路徑看到這個事件。

**適合用在。** 這條 pipeline 邊是確定性的——也就是「每次 A 完成一輪，B 都會收到輸出」。如果是 reviewer / navigator 這類角色，或 analyzer 需要依內容決定分支，還是比較適合留在模式 1（channels），因為 wiring 不能條件式觸發。

**最小設定。**

```yaml
# terrarium.yaml creature block
- name: coder
  base_config: "@kt-biome/creatures/swe"
  output_wiring:
    - runner                              # shorthand
    - { to: root, with_content: false }   # lifecycle ping
```

**對照。** Channels 需要 LLM 記得去送；wiring 不管 LLM 做什麼都一定會觸發。兩種機制可以在同一個 terrarium 裡自由共存——kt-biome 的 `auto_research` 就是用 wiring 來處理棘輪式邊（ideator → coder → runner → analyzer），再用 channels 處理 analyzer 的保留 / 丟棄決策，以及團隊聊天狀態。

## 2. 用 agent-in-plugin 做智慧守門員

**形狀。** 一個 lifecycle plugin 掛在 `pre_tool_execute`。它的實作會跑一個巢狀的小 `Agent`，審查即將執行的 tool call，並回傳 `allow` / `deny` / `rewrite`。plugin 再依此回傳改寫後的參數，或拋出 `PluginBlockError`。

**為什麼可行。** Plugins 是 Python；agents 也是 Python。plugin 呼叫 agent，就和呼叫任何 async 函式沒有差別。

**適合用在。** 你需要基於策略的工具守門機制，而這個判斷本身並不簡單——太複雜，不能靠靜態規則；又太偏領域，不適合用通用解法。

## 3. 用 agent-in-plugin 做無縫記憶

**形狀。** 一個 `pre_llm_call` plugin 會跑一個小型 retrieval agent。retrieval agent 會搜尋 session store（或外部向量資料庫）中和目前上下文相關的事件，整理命中結果，再把它們以前置 system messages 的方式插入。外層生物不用呼叫任何 tool，prompt 就會悄悄變得更豐富。

**為什麼可行。** 生物本身不需要決定「我現在要不要檢索某些東西」——plugin 會固定替它做，而 LLM 每一輪都看得到結果。

**適合用在。** RAG 風格記憶對你有幫助，但你不想讓主 agent 為此消耗 tool 預算。

## 4. 用 agent-in-trigger 做自適應監看器

**形狀。** 一個自訂 trigger，其 `fire()` 內容會定時跑一個小型 judge agent。這個 agent 檢查目前世界狀態，回傳 `fire / don't fire`。若決定觸發，就向外層生物送出一個 event。

**為什麼可行。** Trigger 本質上只是非同步的事件產生器。這個產生器要看什麼，完全由你決定，而「內嵌一個迷你 agent」就是其中一種合法選項。

**適合用在。** 固定時間間隔太粗糙，固定規則太脆弱，但每個 tick 跑一次完整 LLM turn 的成本你還負擔得起。

## 5. 沉默 controller + 外部 sub-agent

**形狀。** 某隻生物的 controller 不產生任何對使用者可見的文字——只做 tool calls，最後派發一個 sub-agent。這個 sub-agent 設定為 `output_to: external`，因此真正串流給使用者看的是**它**的文字，而父層自己保持隱形。

**為什麼可行。** Output routing 會把 sub-agent 的串流和 controller 本身的串流視為平行地位。你可以決定要讓使用者看到哪一條。

**適合用在。** 你希望使用者面前呈現的是某個專家角色的聲音（人格、格式、限制條件），而 orchestration 則留在幕後。kt-biome 很多聊天生物都用了這種做法。

## 6. Tool-as-state-bus

**形狀。** 在同一個 terrarium 內合作的兩隻生物，都把共享 environment 裡像 scratchpad 一樣的 channels 當作會合點：一方寫入 `tasks_done: 3` 這種記錄；另一方輪詢它。或者，它們用共享 session key 搭配 `scratchpad` tool。

**為什麼可行。** Sessions 和 environments 本來就有 KV 儲存。tools 只是把它們暴露給 LLM 使用。

**適合用在。** 你需要粗粒度的協調，但不想為此設計一整套訊息傳遞協定。

## 7. 混合軸多代理系統

**形狀。** 一個 terrarium，它的 root（或其中的 creatures）本身又在內部使用 sub-agents。頂層是橫向；每一隻生物內部則是縱向。

**為什麼可行。** Sub-agents 和 terrariums 是正交的。框架裡沒有任何地方禁止你兩者一起用。

**適合用在。** 團隊本身有角色分工，而某些角色內部又適合進一步拆解（規劃 → 實作 → 審查），但你不需要把那層拆解顯示成獨立生物。

## 8. 用 framework commands 做 inline control

**形狀。** 在同一輪內，controller 可以送出一些直接跟框架對話的小型 inline 指令：`info` 可按需載入某個 tool 的完整文件，`read_job` 可讀取執行中背景工具的部分輸出，`jobs` 可列出待處理工作，`wait` 可等待某個 stateful sub-agent。這些都是 inline 執行——不需要新的 LLM round-trip。

語法取決於生物設定的 `tool_format`；在預設的 bracket 形式下，一個 command 呼叫會長成 `[/info]tool_name[info/]`。

**為什麼可行。** Framework commands 是 parser 層級的 affordance，不是 tools，所以呼叫它們本身幾乎沒有成本。

**適合用在。** 你希望 LLM 在同一輪中檢查自己的狀態，而不必為此消耗一個 tool slot。

## 不是封閉清單

這一頁的重點不是這些模式本身，而是：小而可組合的模組，會自然產出有用的形狀，你不需要把它們硬編進框架裡。如果這裡某個模式和你的需求很接近，那個 tweak 多半仍能落在同一組 building blocks 之內。如果你發明了新的模式，歡迎對這個檔案開 PR。

## 延伸閱讀

- [Agent 作為 Python 物件](python-native/agent-as-python-object.md)
  — 讓第 2–4 種模式成立的關鍵性質。
- [Tool](modules/tool.md)、[Trigger](modules/trigger.md)、
  [Channel](modules/channel.md)、[Plugin](modules/plugin.md) —
  這些模式所組合的基本元件。
- [邊界](boundaries.md) — 抽象是預設值，不是法律；有些模式就是刻意跨越預設邊界。
