---
title: Session 與 environment
summary: 每個生物的私有狀態（session）與生態瓶共用狀態（environment）之間的差異，以及它們如何互動。
tags:
  - concepts
  - module
  - session
  - environment
---

# Session 與 environment

## 它是什麼

狀態分成兩個層級：

- **Session** — 屬於單一生物的私有狀態。包含該生物的
  scratchpad、私有 channel、TUI 參照、job store，以及任何
  自訂 extras。
- **Environment** — 整個執行過程共用的狀態（更精確地說，是整個
  terrarium 共用）。包含共用的 channel registry，以及一個小型的
  自訂 context dict。

獨立執行的生物會有一個 session。terrarium 則會有一個 environment，
並且每個生物各自有一個 session。

## 為什麼會有它

在多代理系統裡，錯誤的預設是「所有東西都共用」。
如果每個生物都能寫入其他生物的 scratchpad，
那你其實只是繞了一大圈做出「全域可變狀態（Global Mutable State）」。
除錯會變得不可能。

這個框架的預設剛好相反：**預設私有，必須明確 opt-in 才共享**。
生物會保留自己的狀態，除非它明確把資料送到共用 channel。
terrarium 是唯一能看見所有生物的東西；
生物只能看見自己的 session，以及那些它主動要求監聽的共用 channel。

## 我們如何定義它

```
Environment（可選，每個 terrarium 一個）
├── shared_channels  （ChannelRegistry）
├── context          （dict，由使用者定義）
└── <這裡沒有私有狀態>

Session（每個生物一個）
├── scratchpad       （key-value，私有）
├── channels         （私有 ChannelRegistry；可別名到共用 registry）
├── tui              （TUI 參照，適用時）
├── extras           （dict，由使用者定義）
└── key              （session 識別鍵）
```

規則：

- 一個生物只會有一個 session。
- environment 會在生物之間共享。獨立生物可以不使用它。
- 共用 channel 存在於 environment 上。生物透過為特定 channel 名稱
  加上一個 `ChannelTrigger` 來 opt-in。
- scratchpad 永遠是 session 私有的。

## 我們如何實作它

`core/session.py` 定義了 `Session`，以及依照 key 取得／建立 session 的輔助函式。
`core/environment.py` 定義 `Environment`。
`Terrarium` 會建立 graph/environment 語境，並將 session 掛到每個 Creature 上。

內建的 `scratchpad` 工具會讀寫目前生物的 session scratchpad。
`send_message` 工具則會選擇正確的 channel registry
（先私有，再共用）。

## 因此你可以做什麼

- **跨回合的私有記憶。** 每個生物都可以把 scratchpad 當成工作筆記本使用；
  不會有資料外漏。
- **共用會合點。** 兩個都在監聽同一個共用 channel 的生物，
  可以在不了解彼此內部實作的情況下協調工作。
- **把 session 當成單一生物的狀態匯流排。** 同一個生物內彼此協作的工具，
  可以把 scratchpad 當成 KV 會合點。
- **以 environment 為範圍的自訂 context。** 驅動 terrarium 的 HTTP 應用程式，
  可以把使用者識別／request-id 放進 environment 的 `context` dict，
  讓 plugins 自行取用。

## 不要被框住

獨立生物不需要 environment。只靠 trigger 的生物也不一定需要 scratchpad。
框架只會在真正重要的地方強制區分私有／共用；
如果只有單一生物，它也很樂意把 session 當成唯一的狀態來源。

## 另請參考

- [Channel](channel.md) — 需要明確 opt-in 的共享原語。
- [多代理 / terrarium](../multi-agent/terrarium.md) — environment 真正重要的地方。
- [impl-notes/session-persistence](../impl-notes/session-persistence.md) — session 狀態實際如何落在磁碟上。
