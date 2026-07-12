---
title: Drive
summary: 引擎以普通事件投遞的持久、可指派執行期承諾。它是與 session、頻道並列的生態瓶資源，選用性質，絕不是一個推理迴圈。
tags:
  - concepts
  - multi-agent
  - drive
---

# Drive

## 它是什麼

一個 **Drive** 是一份持久、可定址、可指派的執行期承諾，它可以為某隻
生物產生普通事件。「持續調查這個事故，直到解決或被卡住」「盯著這次
遷移，程序重啟後繼續」「跨多個回合完成這個研究目標」。Drive 存下這份
承諾，決定它*何時*可以被推進，向持有它的生物投遞一個喚醒事件，能挺過
重啟，並在圖在它腳下變化時自我校正。

Drive 是一種 **選用的、由生態瓶管理的執行期資源**，與
[session](../modules/session-and-environment.md) 或
[頻道](../modules/channel.md) 同屬一族。引擎擁有這套設施；每個圖就像擁有
它的 session store 一樣擁有自己的 Drive 記錄。一隻生物在零個 Drive 下也能
正常執行，一個不帶 Drive 設定建構的生態瓶根本沒有任何 Drive 機制。

Drive 刻意 **不是**：

- 生物的第七個元件（Controller / Input / Trigger / Tool / Output /
  子代理 / 外掛才是全部）；
- 由生物或 session 擁有的目標迴圈；
- 一個 LLM、規劃器、評估器或動機官能；
- trigger、tool、外掛、session 或頻道的替代品；
- 保證某個外部副作用*恰好執行一次*的承諾。

## 它為什麼存在

應用程式已經用外掛狀態、scratchpad、計時器、命令和手工編排來近似持久的
追求。這些拼塊證明了這個想法可以組合，但每個應用都重造了一套微妙不同、
通常也不完整的外層生命週期：持久身份、指派、resume 校正、帶版本的變更、
重試、以及管理性檢視。缺的那塊是 **執行期協調，而非推理**，它和生態瓶
已經擁有的資源形狀相同：

| 資源 | 生態瓶在機制上擁有 | 生物 / 應用提供含義 |
|---|---|---|
| Session | 持久歷史、附著、合併/分裂譜系 | 記住的內容意味著什麼 |
| 頻道 | 身份、接線、廣播投遞 | 訊息意味著什麼、是否行動 |
| **Drive** | 身份、指派、就緒、持久投遞、復原 | 這份承諾意味著什麼、如何追求 |

Drive 還需要生物本地模組拿不到的生命週期知識：生物的
啟動/停止/移除、圖成員關係、圖合併/分裂、session 附著、遠端歸屬、引擎
關停。生態瓶已經擁有這些事實，而且能在*從不呼叫 LLM* 的前提下協調它們。

## 所有權邊界

這是承重的規則，也是 Drive 活在引擎裡的理由：

- **生態瓶擁有機制。** 全域穩定的 `drive_id` 和單調的 `revision`；
  scope 與指派；確定性的生命週期轉換校驗；就緒/依賴計算；持久化與
  交易性 outbox；物理投遞、重試、確認與死信狀態；陳舊 revision 與陳舊
  epoch 的抑制；在啟動/停止/移除/重新指派/resume/拓樸變化時的校正；
  actor 身份、能力檢查與稽核；本地/遠端/多節點的一致性。
- **生物擁有含義。** 解釋 Drive 的 `kind`、`title` 與 `spec`；
  規劃與工具選擇；執行副作用；評估進度與蒐集證據；決定何時*提議*
  等待、卡住、完成或失敗；在被打斷的嘗試之後的復原推理。

### 非智力的執行期規則

生態瓶只可以回答 **確定性** 的問題：Drive 是否存在、這個 revision
是否當前？它的狀態可投遞嗎？受指派者在場且執行中嗎？它的依賴是否到達了
設定的狀態？`not_before` 過去了嗎？actor 被授權了嗎？註冊的校驗器接受了
提議嗎？這個投遞是陳舊、重複、超預算還是在等退避？

生態瓶 **絕不能** 回答語義問題：目標真的達成了嗎？這個計畫好嗎？
生物接下來該做什麼？進度有意義嗎？可以由生物、人類、外部服務或確定性的
註冊驗證器來*提議*這些結論；生態瓶只套用有效的狀態轉換。`COMPLETED`
意味著一個被授權的提議通過了設定的策略——而不是引擎對世界做了推理。

## 生命週期狀態

一個 Drive 每次只有一個 **執行期控制狀態**（這些是引擎控制狀態，不是引擎
對目標的看法）：

| 狀態 | 可投遞？ | 執行期含義 |
|---|---|---|
| `draft` | 否 | 存在但未被准入追求。 |
| `active` | 是，就緒時 | 有資格投遞。 |
| `waiting` | 直到確定性喚醒條件才可 | 在等時間/依賴/外部信號。 |
| `blocked` | 否（預設） | 需要 actor 介入或策略定義的解卡。 |
| `paused` | 否 | 顯式掛起，但未宣告失敗。 |
| `completed` | 否 | 已接受的完成提議；終態。 |
| `failed` | 否 | 已接受的不可復原失敗；終態。 |
| `cancelled` | 否 | 顯式放棄；終態。 |
| `retired` | 否 | 歷史墓碑 / 保留終態。 |

通用轉換圖：

```text
 draft ------> active <------ paused
   |             |  ^            ^
   |             |  |            |
   +-> cancelled |  +-- waiting -+
                 |       |
                 +-----> blocked
                 |
                 +-----> completed
                 +-----> failed
                 +-----> cancelled

 completed / failed / cancelled ---> retired
```

超出這張通用圖的任何東西都需要一個已啟用註冊項的策略。**預設禁止重開
終態 Drive**；預期做法是建立一個帶 `metadata.parent_drive_id` 的後繼
Drive。如果某個註冊項顯式允許重開，倉庫會遞增該 Drive 的 `lifecycle_epoch`
（這會使先前的每次投遞失效）並寫一條稽核記錄。waiting 的 Drive 只攜帶
確定性的喚醒條件——一個時間戳、一個依賴述詞、一個具名外部信號、一個註冊
就緒函式，或一位被授權 actor 的手動喚醒。管理器絕不從自由文字推斷就緒。

## 投遞：至少一次，邏輯去重

一個 Drive 透過變成一個普通的 `TriggerEvent`（`drive_ready` /
`drive_resume` / `drive_recovery`）來成為工作，經由公共的生物入口投遞——
和任何 trigger 走同一條准入、序列化、外掛、controller、tool 與 output
路徑。分發器不呼叫任何私有代理方法，也不啟動第二個推理迴圈；生物仍是
單回合序列器。

誠實陳述的投遞保證：

> 物理 Drive 事件投遞是 **至少一次**。處理在邏輯上按 delivery ID、Drive
> revision、lifecycle epoch、assignment ID 和 readiness generation 去重。

**沒有恰好一次保證，框架也從不宣稱有。** 恰好一次的副作用無法跨越一次
模型回合、一次工具呼叫和一個可以各自獨立失敗的外部系統來承諾。引擎把
*物理分發* 和 *邏輯確認* 分開：當生物接受事件時投遞變為 `admitted`，
當那個回合落定時變為 `acknowledged`。`acknowledged` 意味著「回合落定
了」——**不** 意味著「Drive 完成了」，**也不** 意味著「外部副作用恰好
發生了一次」。在准入前，分發器會拒絕或作廢任何 Drive 已消失或終態、
revision 或 epoch 陳舊、指派已改變、或已被准入過的投遞。

執行副作用的工具應當攜帶自己的冪等鍵；Drive 投遞上下文正是為此暴露了
`delivery_id`，讓有副作用的工具有一個穩定的鍵去去重。

### 復原對不確定性誠實

如果生物在准入與確認之間停止（或程序崩潰），先前那次嘗試是 **不確定的**：
它的副作用可能跑了也可能沒跑。在生物重啟並通過下文的還原屏障之後，管理器
會透過 `drive_resume` 或 `drive_recovery` 事件重新引入仍然當前的 Drive，
生物看到的投影會說：

> 先前的一次嘗試可能已經執行了副作用。在重複動作之前檢視當前狀態並
> 校正。在支援之處用 delivery ID 作為冪等鍵。

復原事件絕不指示盲目重放，任何 UI 也絕不把復原或卡住狀態渲染成普通
成功。

## 還原屏障

Drive 絕不能對著一個半還原的執行期被投遞。某些建構路徑會在 session
store 附著之前就啟動生物；Drive 要求一個顯式的次序：

```text
建構 creature
-> 還原 conversation / scratchpad / 外掛 / session 狀態
-> 附著圖的 SessionStore 與 Drive 倉庫
-> 重放執行期拓樸
-> 啟動 creature
-> 完成啟動 trigger
-> 標記 creature 還原就緒
-> 校正 Drive
```

在這道屏障之前不投遞任何 Drive。正是這一點防止一個 Drive 對著空對話或
半還原的圖去追求目標。冷啟動時次序永遠是：先還原，然後啟動 trigger，
最後 Drive 校正。

## 註冊項：已安裝不等於已啟用

新的 Drive **實例** 在執行期動態建立。新的可執行 Drive **kind** 不是——
一個 Drive 的 `kind` 由一個 **Drive 註冊項** 服務，它是一個確定性的
執行期擴充，為那個 kind 提供 schema 校驗、就緒規則、事件投影、可選的
完成驗證器，以及一段有界的 prompt 貢獻。註冊項不執行 LLM、不寫倉庫、也
不分發事件；它只回答核心問過來的確定性問題。框架自帶一個內建的
`generic` 註冊項（不透明 spec、手動終態提議）；其他 kind——例如
`goal`——以已安裝的套件形式到來。

兩個容易混淆的獨立概念：

- **發現** —— 一個套件宣告 `drive_registrations:` manifest 槽位使某註冊項
  *可用*。Studio 目錄可以列出它而無需 import 它的程式碼。
- **啟用** —— 一個註冊項只有被顯式啟用（在 Drive 設定裡，或把實例傳給
  `Terrarium(drive_registrations=[...])`）才變得可用。**已安裝絕不會被
  自動啟用。** 只有已啟用的註冊項才能為它的 kind 建立、校驗、投影、
  排程或貢獻 prompt 文字。

註冊項 `name` 重複以及 `kind` 歸屬衝突都是硬校驗錯誤，會在任何套用之前
被暴露出來。

### 當註冊項被停用或不可用

已持久化的 Drive 記錄 **絕不會** 僅因其註冊項被關掉就被刪除或改寫。
可用性是一個*衍生的*執行期條件（`DriveAvailability`），不是新狀態，也不是
消耗一個 revision 的理由：

- 記錄仍可列出，仍可被管理性地 pause / cancel / retire；
- 衍生條件是 `registration_disabled`、`registration_unavailable` 或
  `registration_incompatible`，只要它成立就 **不准入任何投遞**；
- 任何需要該註冊項語義的操作——spec 編輯、就緒評估、投影、終態驗證——都
  **fail closed（失敗即拒）**；
- 重新啟用一個相容的註冊項會清除該條件並校正仍活躍的記錄；不相容的
  schema 版本需要先做一次顯式遷移；
- 通用的讀取/狀態檢視與已保存 session 檢視器全程可用。

## 持久化

一個 Drive 的持久性取決於它的圖如何設定：

| 引擎 / 圖設定 | Drive 行為 |
|---|---|
| 附著了 session store / autosession | **持久**；程序重啟後可 resume。 |
| 無 session 且無單獨 Drive store | **僅記憶體**；能挺過生物停止，挺不過引擎關停。 |
| 顯式 `drive_store=` | **持久**，獨立於對話 session（用於服務/常駐型應用）。 |

當附著了 session 時，Drive 倉庫活在一個 **與 session 配對的專用 sidecar
檔案** 裡——在 `<name>.kohakutr` 旁邊的 `<name>.kohakutr.drives`——這樣
Drive 的寫和對話的寫就絕不在同一個資料庫上爭搶。複製一個帶持久 Drive 的
session 意味著也要複製那個 sidecar。機制細節見
[programmatic 指南](../../guides/programmatic-drive.md)。

## 它與 `/goal` 的關係

`/goal` 功能是在通用 Drive 設施之上的 **一種選用組合**，而不是 Drive 的
定義。它是兩個獨立的開關：一個 `goal` Drive *註冊項*（確定性的 kind
語義）和一個 `GoalPlugin`（`/goal` 斜線命令及其 prompt 指導）。任一個都
可以在沒有另一個的情況下啟用。見
[Goal：Drive 之上的組合](../../guides/goal.md)。

## 因此你能建構什麼

- **持久的事故追求。** 一隻生物跨重啟保持一個 `blocked`/`active` 的
  Drive；復原事件告訴它在行動前重新檢視。
- **排程/等待型工作。** 一個 `waiting` 的 Drive 在某個時間戳或某個依賴
  Drive 到達終態時重新武裝。
- **operator 可見的承諾。** 因為 Drive 是一等執行期資源，它的狀態、
  owner、受指派者以及復原/卡住警告在任何操作生態瓶的地方都可檢視——
  與是否安裝了 `/goal` 無關。

## 別被框住

一隻生物在沒有 Drive 時也完全有效，而且大多數生物永遠不需要它。只有當
承諾確實比單個回合更持久 *而且* 需要引擎的協調（身份、指派、resume、
復原）時，才伸手去拿 Drive。一次性任務是一個回合；週期性檢查是一個
trigger；一個持久、可指派、可復原的目標才是一個 Drive。

## 延伸閱讀

- [Session 與環境](../modules/session-and-environment.md)：持久 Drive 在
  旁邊持久化的每圖狀態。
- [頻道](../modules/channel.md)：另一個廣播投遞的執行期資源。
- [Programmatic Drive](../../guides/programmatic-drive.md)：從 Python 直接
  驅動 Drive 執行期。
- [Goal](../../guides/goal.md)：`/goal` 作為 Drive 之上的選用組合。
- [設定參考](../../reference/configuration.md)：`drive-settings.yaml`
  schema 與 `drive_registrations:` manifest 槽位。
