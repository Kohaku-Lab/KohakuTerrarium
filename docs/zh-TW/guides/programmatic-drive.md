---
title: Programmatic Drive
summary: 從 Python 直接驅動持久承諾執行期——顯式引擎設定、自服務工具、propose/verify 完成、sidecar 持久化檔案與 resume，且不依賴 Studio。
tags:
  - guides
  - drive
  - programmatic
---

# Programmatic Drive

面向從 Python 直接驅動 [Drive 執行期](../concepts/multi-agent/drive.md)
的讀者。這是低層、不經 Studio 的路徑：你用顯式的 Drive 參數建構一個
`Terrarium`，並透過引擎的 `TerrariumService` 建立和管理 Drive。這裡沒有
任何東西會讀 `~/.kohakuterrarium` 或向 Studio 要什麼——被托管的介面
（[`kt`](../reference/cli.md)、web、TUI）坐在這條路徑*之上*，從
[Drive 設定](../reference/configuration.md)解析出同樣的顯式參數。

如果你只想要概念，先讀
[概念 / Drive](../concepts/multi-agent/drive.md)。如果你想要 `/goal`，那是
建在這裡一切之上的一個組合——見 [Goal](goal.md)。

## 啟用執行期

不傳 `drive_config`，一個生態瓶就沒有任何 Drive 機制。啟用是顯式的
相依注入：設定加上一份具體的、非空的註冊項清單。

```python
import asyncio

from kohakuterrarium import Terrarium
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)


async def main():
    async with Terrarium(
        session_dir="runs/",                       # autosession：Drive 會持久化
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),  # == [GenericDriveRegistration()]
    ) as engine:
        assert engine.drives is not None           # 執行期已啟用
        ...

asyncio.run(main())
```

建構函式強制的規則：

- **`drive_config=None`（預設）意味著沒有 Drive 執行期。** 引擎不建構
  manager、tools、prompt 或分發器，`engine.drives` 為 `None`。
- **`enabled=True` 卻不給註冊項會校驗失敗。** 低層引擎絕不掃描套件，也不
  憑空發明一套啟用集。你要麼顯式傳 `default_registrations()`（generic
  kind），要麼傳你自己的實例。
- 註冊項在任何生物啟動之前會被衝突檢查（`name` 重複或 `kind` 衝突都是
  硬錯誤）。
- 每個便捷建構函式都轉發這同樣的三個參數：

```python
engine = await Terrarium.from_recipe(
    "team.yaml",
    drive_config=drive_config,
    drive_registrations=registrations,
)
engine = await Terrarium.resume(
    "run.kohakutr",
    drive_config=drive_config,
    drive_registrations=registrations,
)
```

recipe 物件不變——recipe 從不攜帶 Drive 欄位（見
[recipe 只管圖結構](terrariums.md)）。把同一個 recipe 套用到兩個引擎上
可能得到不同的 Drive 能力，因為不同的是引擎參數，而不是 recipe。

`DriveRuntimeConfig` 是排程器 / 重試 / 保留 / 載荷限制所在之處；每個欄位
及其預設值都在 [設定參考](../reference/configuration.md)裡。

## 建立與管理 Drive

記錄透過一個 `TerrariumService` 建立與管理。對於嵌入式呼叫者，那就是
`LocalTerrariumService(engine)`。這個介面就是 Studio 所用的介面，所以你的
程式碼和被托管的 UI 共享同一套行為和同一套型別化錯誤。

每次變更都攜帶一個由你從自己受信上下文提供的 **actor**；manager 在每次
呼叫上重新檢查授權，所以持有一個 service 物件本身從來不是權限。

```python
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest

worker = await engine.add_creature("@kt-biome/creatures/swe", start=True)
service = LocalTerrariumService(engine)

operator = ActorRef("service", "deploy-bot")   # "<kind>:<identity>"

view = await service.create_drive(
    CreateDriveRequest(
        kind="generic",
        title="Watch the deployment",
        scope_type="graph",                     # 或 "creature"
        scope_id=worker.graph_id,
        owner=operator,
        owner_scope="service",
        created_by=operator,
        spec={"instruction": "Monitor until the rollout is stable"},
        assignee_creature_id=worker.creature_id,
    ),
    graph_id=worker.graph_id,
    actor=operator,
    operator=True,          # 經稽核的圖權限提升（見下）
)
drive_id = view.record.drive_id
```

`create_drive`（以及每次變更）回傳一個 **`DriveView`**：`record`、它的
`assignee_creature_id` / `assignment_state`、衍生的 `availability` 與
`durability`，以及一個按 actor 限定的 `allowed_actions` 元組，好讓 UI
不用猜就能渲染出感知權限的控制項。

### actor、所有權與 operator 旗標

- 由呼叫者擁有的 **creature scope** 的 Drive 不需要提升——那是任何生物
  （以及下文的自服務工具）的基線能力。
- 建立一個 **graph scope** 的 Drive、指派給另一隻生物、或轉移所有權都是
  圖權限操作。一個普通的 `user:` actor 不持有它；受信的嵌入式呼叫者傳
  `operator=True`，manager 把它當作一次顯式的、經稽核的提升——絕不當作
  生物特權。
- 指派 **不是** 所有權。被指派了一個由別人擁有的 Drive 的生物可以讀它、
  報告進度、並*提議*轉換，但不能改寫、取消、重新指派或退休它。

### 讀、更新、轉換

```python
from kohakuterrarium.terrarium.drive.requests import DrivePatch

# 讀
view = await service.get_drive(drive_id, actor=operator)
views = await service.list_drives(
    actor=operator,
    statuses=frozenset({DriveStatus.ACTIVE, DriveStatus.WAITING}),
)

# CAS 更新——expected_revision 必須相符，否則丟 DriveConflictError
view = await service.update_drive(
    drive_id,
    DrivePatch(priority=5),
    expected_revision=view.record.revision,
    actor=operator,
)

# 非終態的控制轉換
await service.transition_drive(
    drive_id, DriveStatus.PAUSED,
    expected_revision=view.record.revision, actor=operator,
)
await service.wake_drive(drive_id, actor=operator)     # 重新武裝一個 waiting 的 Drive

# 僅追加的進度（不遞增 revision）
await service.report_drive_progress(
    drive_id, summary="rollout at 40%", evidence={"pct": 40}, actor=operator,
)
```

每次正式變更都取 `expected_revision`（樂觀並行）和一個可選的
`idempotency_key`。陳舊的 revision 丟 `DriveConflictError`；用同一個冪等鍵
配不同的載荷丟 `DriveIdempotencyConflictError`。`report_drive_progress`
是僅追加的例外，不取 `expected_revision`。

## Propose / verify 完成

一隻生物（或一個 operator）**不**直接寫終態。完成與失敗走一個 **提議**，
好讓註冊項校驗器和任何必需的驗證器先跑：

```python
result = await service.propose_drive_transition(
    drive_id, DriveStatus.COMPLETED,
    evidence={"tests": "green", "run": "ci#4821"},
    expected_revision=view.record.revision,
    actor=operator,
)

if isinstance(result, dict) and result.get("pending"):
    # 一個必需的驗證器 / 兩方審批在等待
    final = await service.approve_drive_proposal(
        result["proposal_id"], actor=operator, operator=True,
    )
else:
    final = result          # 一個 DriveView：提議被直接接受了
```

驗證器模式是註冊項的決定。`generic` kind 直接接受一個被授權的提議。其他
kind 可以要求一個具名驗證器、一個特定的審批 actor 類別，或一個不同的
兩方審批者——而一個缺失的必需驗證器 **fail closed（失敗即拒）**，絕不
放行。生態瓶從不判斷目標是否真的達成；它只套用一個被授權、被驗證的提議
所贏得的轉換。

## 自服務工具（面向生物）

一個啟用了 Drive 的生態瓶向它托管的 **每隻** 生物注入五個通用 Drive
工具，外加一個特權工具。這些是 LLM 呼叫的；它們從工具上下文解析 actor，
並在每次呼叫上強制 owner / scope / ACL 與註冊可用性，所以它們在非特權
生物上也是安全的（工具存在不是授權）。

| 工具 | 作用範圍 | 它做什麼 |
|---|---|---|
| `drive_create` | 每隻 creature | 建立一個 **你擁有的** Drive，scope 到你並指派給你。 |
| `drive_status` | 每隻 creature | 列出你擁有 / 被指派的 Drive，或按 `drive_id` 取一個。 |
| `drive_update` | 每隻 creature | CAS 更新一個你擁有的 Drive。 |
| `drive_report` | 每隻 creature | 向一個擁有或被指派的 Drive 追加進度 / 證據。 |
| `drive_transition` | 每隻 creature | 管理一個你擁有的 Drive，或對一個別人擁有、指派給你的 Drive 提議一次被允許的轉換。 |
| `group_drive` | 僅特權節點 | 建立圖擁有的 Drive；在圖內 assign / reassign / unassign、轉移所有權、喚醒、退休、修復或重放死信。 |

`group_drive` 是 [特權節點](../concepts/multi-agent/privileged-node.md)的
圖管理介面——一個由特權節點生成的 worker 不會收到它。執行期啟用時，引擎
還注入一段有界的 prompt（通用 Drive 契約加上每個*已啟用*註冊項的散文，
按名稱排序）；它從不把當前 Drive 記錄塞進 prompt——那些透過事件和
`drive_status` 到來。

## 你能倚靠（和不能倚靠）的投遞

一個 Drive 作為普通的 `TriggerEvent`（`drive_ready` / `drive_resume` /
`drive_recovery`）成為工作，經由公共生物入口投遞，並像任何回合一樣落定。
保證是 **至少一次**，在邏輯上按 delivery ID、revision、lifecycle epoch、
assignment ID 和 readiness generation 去重。

**沒有恰好一次保證。** 如果一個有副作用的工具絕不能重複施加，就給它自己
的冪等鍵——投遞上下文正是為此暴露了 `delivery_id`。在一次被打斷的嘗試
之後，生物會收到一個復原事件，說先前的一次嘗試*可能*已經跑了它的副作用，
要在重複之前校正；它絕不會被告知盲目重放。見
[概念 / Drive 的投遞一節](../concepts/multi-agent/drive.md)。

## 持久化與 sidecar 檔案

持久性取決於圖的設定，建立結果會用 `view.durability`（`"persistent"` 或
`"ephemeral"`）報告它：

| 設定 | 持久性 |
|---|---|
| `Terrarium(session_dir=...)` / 附著了 session store | **persistent** —— 程序重啟後可 resume |
| 無 session 且無 `drive_store` | **ephemeral** —— 挺過生物停止，挺不過引擎關停 |
| `Terrarium(drive_store=...)` | **persistent**，獨立於對話 session |

當附著了 session 時，Drive 倉庫 **不** 寫進 `.kohakutr` 檔案。它活在一個
與 session 配對的 **sidecar** 裡：

```text
runs/run.kohakutr          <- 對話、事件、scratchpad（KohakuVault）
runs/run.kohakutr.drives   <- Drive 倉庫（它自己的 SQLite 資料庫）
```

兩個要規劃的後果：

- **一個持久 Drive 隨它的 sidecar 一起走。** 如果你複製或移動一個 session
  並想要它的 Drive，就把 `<name>.kohakutr.drives` 和 `<name>.kohakutr`
  一起複製。裸的 `.kohakutr` 不攜帶任何 Drive 狀態。
- **fork 一個 session 不會 fork 它的 Drive。** fork 只複製 `.kohakutr`，
  所以它在建構上就帶著零個 Drive 出生——這避免兩個分支變更同一份承諾。
  合併與分裂透過行拷貝掛鉤顯式攜帶 Drive。

在無法解析出持久後端時請求持久化會在引擎建構時失敗
（`DrivePersistenceRequiredError`），而不是在一個 Drive 已經活躍之後。

## Resume 與校正

`Terrarium.resume(...)` 取和建構函式 **相同的** 顯式 Drive 參數，開啟
已持久化的 Drive 狀態（從 sidecar），並校正它。它從不重新套用「recipe
Drive 種子」，因為根本沒有那種東西——recipe 從不建立 Drive。

```python
engine = await Terrarium.resume(
    "runs/run.kohakutr",
    drive_config=DriveRuntimeConfig(enabled=True),
    drive_registrations=default_registrations(),
)
```

resume 時，在生物通過 [還原屏障](../concepts/multi-agent/drive.md)
之前不投遞任何 Drive：conversation / scratchpad / 外掛 / session 狀態
已還原、Drive 倉庫已附著、拓樸已重放、生物已啟動、其啟動 trigger
已落定。只有到那時管理器才校正指派並重新引入仍然當前的 Drive——乾淨停止
用 `drive_resume`，半途被打斷的嘗試用 `drive_recovery`（帶「可能已執行
副作用」的警告）。

如果一個被 resume 的 Drive 的註冊項在這個引擎上未啟用，記錄 **不** 被
刪除或降級：它變為不可投遞，帶一個衍生可用性 `registration_disabled` /
`registration_unavailable` / `registration_incompatible`，保持可檢視，並在
一個相容的註冊項被啟用的那一刻校正。見
[概念 / Drive：當註冊項被停用](../concepts/multi-agent/drive.md)。

## 生態瓶不會做什麼

- **對一個 Drive 推理。** 引擎回答確定性問題（revision 是否當前？受指派者
  執行中嗎？`not_before` 過去了嗎？）。它從不判斷目標是否達成、計畫好不好、
  或生物接下來該做什麼。
- **自己完成一個 Drive。** 預算和錯誤可以 pause 或 block 一個 Drive；沒有
  東西會悄悄把它標為完成。`completed` 需要一個被授權、被驗證的提議。
- **回滾副作用。** 取消一個 Drive 阻止未來的投遞；它不會撤銷先前回合已
  執行的效果。

## 延伸閱讀

- [Drive 概念](../concepts/multi-agent/drive.md)：這套 API 背後的模型。
- [Goal](goal.md)：`/goal` 作為 Drive 之上的選用組合。
- [設定參考](../reference/configuration.md)：`DriveRuntimeConfig` 欄位、
  `drive-settings.yaml` 與 `drive_registrations:` manifest 槽位。
- [Terrariums](terrariums.md)：托管 Drive 執行期的引擎。
- 內建的 Goal 組合（`kohakuterrarium.terrarium.drive.goal` +
  `kohakuterrarium.builtins.plugins.goal`）：一個完全建在這套公共介面上的
  Goal 註冊項 + 外掛。
