---
title: Goal
summary: /goal 作為 Drive 執行期之上的選用組合——兩個獨立開關（一個 goal 註冊項與 GoalPlugin）、命令集、所有權、user-confirm 完成、只會暫停而不會完成的預算，以及誠實的復原。
tags:
  - guides
  - goal
  - drive
---

# Goal

`/goal` **不是** 一個框架功能。它是完全建在通用
[Drive](../concepts/multi-agent/drive.md) 執行期之上的一個選用組合，作為
**內建** 外掛 + 註冊項發布：`GoalPlugin`
（`kohakuterrarium.builtins.plugins.goal`）與 `GoalDriveRegistration`
（`kohakuterrarium.terrarium.drive.goal`）。兩者都內建於程式碼樹，但
**預設停用**——你按 agent 逐一啟用它們，就和其它內建外掛（sandbox /
budget / permgate / compact）一樣。Drive 做持久的工作；Goal 加上一個
人類友善的斜線命令和一個叫 `goal` 的 kind。因為它只是一個組合，`/goal`
所做的一切也都能透過 [programmatic 指南](programmatic-drive.md)裡的通用
Drive 工具和 API 觸達。

## 兩個獨立開關

要理解的最重要一點：Goal 是 **兩個分離的開關**，而且任一個都不蘊含
另一個。

| 開關 | 它是什麼 | 你如何啟用它 |
|---|---|---|
| `goal` **註冊項** | 確定性的 `kind="goal"` 策略：schema、感知 autonomy 的就緒、投影、終態驗證器。 | 在 [Drive 設定](../reference/configuration.md)裡啟用內建的 `goal` 註冊項，或把 `GoalDriveRegistration()` 傳給 `Terrarium(drive_registrations=[...])`。 |
| `GoalPlugin` | 選用的內建外掛，貢獻 `/goal` 命令和一段簡短的 Goal 語義 prompt。 | 在外掛面板（`/plugin`、web/TUI 的 Plugins 分頁）裡啟用它，或把它列進某隻生物的 `plugins:` 設定，或 `agent.add_plugin(GoalPlugin())`。 |

- **啟用 `GoalPlugin` 不會啟用 `goal` 註冊項**，反之亦然。它們是兩個獨立的
  內建開關——一個在外掛面板，另一個在 Drive 設定。
- **註冊項被停用** → `drive_create(kind="goal")` 和 `/goal set` 會帶著
  清晰的訊息 fail closed；store 裡已有的任何 Goal 記錄保持可檢視。
- **外掛被停用** → `/goal` 從每份命令清單裡消失；先前建立的 Goal Drive
  在（仍啟用的）註冊項下繼續執行，可透過通用 Drive 介面管理。

這個分離是刻意的：執行期可執行的策略（註冊項）是一個 operator/設定的
決定，而 `/goal` 的 UX 是一個按生物的外掛決定。一個是*這個節點可以
執行哪些 kind*；另一個是*這隻生物是否提供斜線命令*。

## 啟用

兩個開關都內建於程式碼樹；**沒有安裝步驟**。為執行期啟用註冊項——透過被
托管的設定介面（它寫 `drive-settings.yaml`）：

```yaml
# ~/.kohakuterrarium/drive-settings.yaml
runtime:
  enabled: true
registrations:
  goal:
    enabled: true
```

然後透過啟用內建外掛來讓某隻生物啟用 `/goal`——在外掛面板（`/plugin`、
web/TUI 的 Plugins 分頁），或把它列進生物設定：

```yaml
# 一隻 creature 的 config.yaml
plugins:
  - name: goal          # 解析到內建的 GoalPlugin
```

或者在 Python 裡顯式地兩者都做，完全不用設定檔：

```python
from kohakuterrarium import Terrarium
from kohakuterrarium.builtins.plugins.goal import GoalPlugin
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.drive.goal import GoalDriveRegistration

async with Terrarium(
    drive_config=DriveRuntimeConfig(enabled=True),
    drive_registrations=[GoalDriveRegistration()],   # 註冊項開關
) as engine:
    creature = await engine.add_creature(
        "@kt-biome/creatures/general",
        plugins=[GoalPlugin()],                       # 外掛開關
        start=True,
    )
```

## `/goal` 命令集

```text
/goal set [autonomy=continue_when_ready] [policy=user_confirm] [criteria=a;b] <objective>
/goal show [id]
/goal list
/goal pause  [id]
/goal resume [id]
/goal cancel [id]
/goal complete [id]         # 使用者權威的完成
/goal assign <id> <creature>
```

`/goal` 總是以 **已認證使用者** 這個 actor 行動——絕不以外掛或生物
的身份——並從受信的命令上下文解析出 `TerrariumService` 和聚焦的生物。它
從不從命令文字接受一個 actor 字串，也不存自己的任何狀態：每次 `show` /
`list` 都讀取即時 Drive 狀態。`/goal set` 不帶顯式 id 時作用於該生物最近
一個活躍的 Goal；給一個 id 來消歧。

`/goal set` 預設 `autonomy=manual`：生物現在推進一個回合，之後由
`/goal resume` 喚醒下一個回合。傳 `autonomy=continue_when_ready` 得到一個
自行持續的 Goal。`/goal list` 列出聚焦生物所在圖裡的所有存活 Goal 並標出
各自的受派者，所以派給其他生物的 Goal 也看得見。`/goal resume` 重新啟用
一個 paused / waiting / blocked 的 Goal 然後喚醒它；對一個活躍的 manual
Goal 它就只是喚醒。在仍有其他存活 Goal 時設定新 Goal 會把那些 Goal 報告
出來；每個生物的活躍 Goal 上限由執行期強制執行，到達上限時命令會提示你
先暫停或取消一個。

命令只是 UI。它呼叫 Python、HTTP、web 面板和通用 Drive 工具所呼叫的同一批
`TerrariumService` 方法，所以一個透過 `/goal` 建立的 Goal 和用任何其他
方式建立的都完全相同。

## GoalSpec

`goal` 是一個 Drive kind，不是對 Drive 的重新定義。它的 `spec` 是：

```python
{
    "objective": str,                       # 必填
    "success_criteria": list[str],
    "constraints": list[str],
    "completion_policy": "self_propose" | "user_confirm" | "verifier",
    "autonomy": "manual" | "continue_when_ready",
    "budgets": {
        "max_turns": int | None,
        "max_tool_calls": int | None,
        "max_walltime_s": int | None,
    },
}
```

只有 `objective` 是必填的；其他一切都保守地取預設（`manual` autonomy、
`self_propose` 完成、無預算）。Drive 核心從不解析目標或判斷標準——那是
生物的活。生物每回合收到的投影告訴它這是一份*持續的承諾，而不是讓它
發明一個新目標*，要帶證據報告實質進度，並帶證據*提議*完成而不是斷言它。

### autonomy 驅動續跑（並沒有 GoalRunner）

- `manual` —— Goal 每次喚醒被推進一次然後等待；一個被授權的 actor 必須
  再次喚醒它（`/goal resume`，或某個依賴變就緒）。
- `continue_when_ready` —— 每次回合落定後註冊項的就緒重新武裝，於是通用
  Drive 分發器發出下一個普通 Drive 事件。續跑是分發器對就緒的反應，
  **而不是** 一個特殊的代理迴圈。

## 所有權

誰擁有一個 Goal（以及誰可以完全管理它）取決於建立路徑。所有權不是指派：
受指派者推進工作；owner 控制記錄。

| 建立路徑 | 預設 owner | 預設受指派者 | 誰可以完全管理它 |
|---|---|---|---|
| 人類 `/goal set ...` | 已認證使用者 | 聚焦的 creature | 使用者 / 管理員；受指派者可報告 + 提議 |
| Web / TUI Goal 表單 | 已認證使用者 | 所選 creature | 使用者 / 管理員；受指派者可報告 + 提議 |
| Creature 呼叫 `drive_create(kind="goal")` | 那隻 creature | 那隻 creature | 那隻 creature / 管理員 |
| 特權 `group_drive` 建立 | 圖或所選 actor | 所選圖成員 | 特權圖權限 / 管理員 |
| 應用 Python / API | 提供的 service / user actor | 顯式 | owner / 能力策略 |

因為一個使用者擁有的 Goal 被指派給*另一個* actor（那隻生物），
`/goal set` 和 `/goal assign` 是圖權限操作。本地 operator 主控台為這兩個
動詞提供一次顯式的、經稽核的 operator 提升；其他每個動詞（`show` /
`list` / `pause` / `resume` / `cancel` / `complete`）都以普通使用者 owner
身份執行，不需要提升。

## 完成是權威的，依策略而定

`/goal complete`（以及任何完成）走一個 **提議**，而 `goal` 註冊項的
`completion_policy` 決定什麼使它定案：

- **`self_propose`** —— 一個被授權的提議被直接接受。生物判斷目標達成
  時可以完成它自己的 Goal。
- **`user_confirm`** —— 只有一個 **user actor** 的提議才能定案。一隻生物
  提議完成 *不* 被接受；完成留在人類 `/goal complete` 路徑上。這就是你
  如何把人類留在環裡。
- **`verifier`** —— 提議必須攜帶非空證據；一個無證據的完成被拒絕。

生態瓶從不判斷目標是否真的達成。它只套用一個被授權、滿足策略的提議
所贏得的轉換。

## 預算會暫停，絕不完成

一個 Goal 的 `budgets` 限定 `continue_when_ready` autonomy 在必須停下
報到之前跑多遠。當一個預算耗盡時：

- 就緒停止重新武裝，帶一個可觀察的原因，如 `turn budget exhausted (3/3)`；
- 生物被引導去 **提議一次 pause 或 block**；
- Goal **絕不** 因為某個預算跑光而被標為 `completed`。

預算耗盡是「停下來問」，不是成功。這是貫穿整個 Drive 執行期的硬規則，
不只 Goal。

## 中斷會暫停 Goal

停就是停。當使用者中斷一個由 Goal 投遞啟動的回合時，執行期以
`user_interrupted` 為原因確認該投遞，並把 Goal 移到 `paused`，狀態原因
相同。因此一個 `continue_when_ready` Goal **不會** 在下一次就緒掃描時
重新武裝，而排在被中斷回合後面、尚未執行的投遞也以同樣方式落定而不是
重試。Goal 只會透過顯式的 `/goal resume`（或通用 Drive 介面上的喚醒）
復原。每個介面都會在對話記錄裡標出由 Drive 觸發的回合的開始，帶上
Goal id 和投遞原因，所以一個並非由使用者輸入開始的回合從不會悄無聲息。

## 復原是誠實的

一個 Goal 是持久的，所以一隻生物可能在追求途中被打斷（一次停止、一次
崩潰）。重啟後，在通過 [還原屏障](../concepts/multi-agent/drive.md)
之後，仍然活躍的 Goal 會作為一個復原事件回來，其指導是明確的：

> 先前的一次嘗試可能已經執行了副作用。在重複任何副作用之前檢視當前的
> 世界。

框架從不告訴生物盲目重放，也從不軟化這個警告。如果一個 Goal 步驟有絕不能
重複施加的外部效果，做它的工具應當用它自己的冪等鍵——投遞上下文為此暴露了
`delivery_id`。投遞是
[至少一次，不是恰好一次](../concepts/multi-agent/drive.md)。

## 沒有 GoalPlugin 時

因為 `/goal` 只是一個便利，一個已啟用 `goal` 註冊項但沒啟用外掛的使用者
仍然可以：

- 透過 Python / API / CLI / web 建立一個 `goal`（或 `generic`）Drive；
- 讓生物自己呼叫 `drive_create(kind="goal")`；
- 讓生物透過通用 `drive_*` 工具管理它自己擁有的 Goal；
- 透過通用 Drive 介面檢視和管理被授權的 Goal。

`/goal` 是疊在上面的選用語法和 UX——不是能力邊界。

## 延伸閱讀

- [Drive 概念](../concepts/multi-agent/drive.md)：`/goal` 組合於其上的
  執行期。
- [Programmatic Drive](programmatic-drive.md)：`/goal` 在底層呼叫的通用
  工具和 service API。
- 內建實作：`kohakuterrarium.terrarium.drive.goal` 裡的
  `GoalDriveRegistration` 與 GoalSpec 輔助函式；
  `kohakuterrarium.builtins.plugins.goal` 裡的 `GoalPlugin` 與 `/goal` 命令。
- [設定參考](../reference/configuration.md)：在 `drive-settings.yaml` 裡
  啟用註冊項。
