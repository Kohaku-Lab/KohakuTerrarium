---
title: 工作階段與恢復
summary: .kohakutr 工作階段檔怎麼運作、怎麼恢復一隻生物、以及怎麼重播對話歷程。
tags:
  - guides
  - session
  - persistence
---

# 工作階段

給需要持久化、恢復、或把代理執行封存起來的讀者。

一個工作階段把一次執行的運作狀態抓下來 — 對話、事件、子代理對話、頻道歷程、草稿區、job、可恢復的觸發器、config metadata — 寫進一個 `.kohakutr` 檔。你可以隨時停掉一隻生物，之後再從同一個地方接回去。

觀念預備：[記憶與壓縮](../concepts/modules/memory-and-compaction.md)、[工作階段與環境](../concepts/modules/session-and-environment.md)。

## `.kohakutr` 檔

`.kohakutr` 是一個 SQLite 資料庫 (走 KohakuVault)，裡面九張表：

| Table | 用途 |
|---|---|
| `meta` | 工作階段 metadata、config 快照、生態瓶拓樸 |
| `state` | 每隻代理的草稿區、回合數、累積 token 用量、可恢復觸發器 |
| `events` | Append-only 日誌，記下每個文字 chunk、工具呼叫、觸發器、token 用量事件 |
| `channels` | 以頻道名為 key 的頻道訊息歷程 |
| `subagents` | 子代理對話快照，key 是 parent + name + run |
| `jobs` | 工具與子代理 job 紀錄 |
| `conversation` | 每隻代理最新的對話快照 (用來快速 resume) |
| `fts` | 事件上的 FTS5 索引 (給 `kt search`) |
| `vectors` | 選用的 embedding 欄位 (由 `kt embedding` 填入) |

事件資料是 append-only，並透過 KohakuVault 的 auto-pack 做版本管理。你可以安全地複製、封存、寄 email 傳 `.kohakutr` 檔；它不相依任何外部東西。

## 工作階段放在哪

```
~/.kohakuterrarium/sessions/<name>.kohakutr
```

`<name>` 由生物或生態瓶的名字加上時間戳自動生成。用 `--session <path>` 覆寫，或用 `--no-session` 完全跳過。

## 哪些東西會留下來

每回合 KohakuTerrarium 會記錄：

- **對話快照** — 原始 message dict，用 msgpack 存。保留 `tool_calls`、多模態內容、metadata。
- **事件日誌** — 每個 chunk、工具呼叫、子代理輸出、觸發器觸發、頻道訊息、壓縮、interrupt、錯誤都各一筆。這是歷程的正本。
- **子代理對話** — 在子代理被銷毀前存起來，事後你可以檢視它做了什麼。
- **草稿區與頻道訊息** — 每隻代理與每條頻道分開存。
- **Job 紀錄** — 長時間工具與子代理的輸出。
- **可恢復觸發器** — 任何設 `resumable: True` 的 `BaseTrigger` 子類會序列化到 `state`，resume 時再建回來。
- **Config 快照** — 執行期完整解析過的 config，所以就算磁碟上的 config 之後改了，resume 一樣能把代理建回來。

## 恢復

```bash
kt resume --last            # 最近一個
kt resume                   # 互動式挑選 (顯示最近 10 個)
kt resume my-agent_20240101 # 用名字前綴
kt resume ~/backup/run.kohakutr
```

會自動偵測類型：agent 工作階段掛一隻生物；terrarium 工作階段掛完整接線並強制走 TUI 模式。

旗標跟 `kt run` 一樣：`--mode`、`--llm`、`--log-level`，另外有 `--pwd <dir>` 可以覆寫工作目錄。

Resume 會做這些事：

1. 從 `meta` 讀 config 快照。
2. 重新載入目前磁碟上的 config (你之後改的 prompt/工具會生效)。
3. 合併：config 快照給身份，現行 config 給執行邏輯。
4. 重建代理、接上同一個 `SessionStore`、重新灌回對話快照、重播草稿區/頻道/觸發器狀態。
5. 控制器從頭起跑；先前的事件都在 context 裡。

所以小幅度的 config 漂移沒問題 (換 LLM、改 prompt 都 OK)。結構性的漂移 (改生物名字、拿掉一個正在用的工具) 會讓重播出錯 — 如果要完美還原，把工作階段釘在原本的 config 上。

## 中斷與恢復流程

```bash
kt run @kt-biome/creatures/swe
# 工作一下... 然後 Ctrl+C
# 之後：
kt resume --last
```

代理在 Ctrl+C 時會乾淨離開：把進行中的工具跑完、flush session store、印出 resume 提示。強制砍掉 (SIGKILL) 會跳過最後的 flush，但因為寫入是 append-only，最近的狀態大部分還是在磁碟上。

## 複製或封存工作階段

```bash
# 備份
cp ~/.kohakuterrarium/sessions/swe_20240101.kohakutr ~/backups/

# 從搬過的位置 resume
kt resume ~/backups/swe_20240101.kohakutr

# 不做完整 resume 只檢視 (純讀的 CLI 之後會上；目前先用 Python)
```

用 Python 檢視：

```python
from kohakuterrarium.session.store import SessionStore
store = SessionStore("~/backups/swe_20240101.kohakutr")
print(store.load_meta())
for agent, event in store.get_all_events():
    print(agent, event["type"])
store.close()
```

## 壓縮

上下文塞滿時，壓縮會把對話縮短。每隻生物自己設：

```yaml
compact:
  enabled: true
  threshold: 0.8              # context 到 window 的 80% 就壓縮
  target: 0.5                 # 壓完目標剩 50%
  keep_recent_turns: 5        # 最後 N 回合一定保留原樣
  compact_model: gpt-4o-mini  # 摘要用的便宜模型
```

壓縮在背景跑 (見 [concepts/modules/memory-and-compaction](../concepts/modules/memory-and-compaction.md)) — 控制器照常運作；新摘要好了再把對話替換掉。每次壓縮都會記成一個事件。

手動壓縮：從 CLI/TUI 的 prompt 下

```
/compact
```

要把長工作階段交給人接手、或把它當成下一次執行的 context 時很實用。

## 記憶搜尋

工作階段本身也是一個可搜尋的知識庫。建好索引後：

```bash
kt embedding ~/.kohakuterrarium/sessions/swe.kohakutr
kt search swe "auth bug"
```

代理自己可以用 `search_memory` 工具搜尋。完整走一遍：[記憶](memory.md)。

## 關掉持久化

有時候就只想跑一次不留痕跡：

```bash
kt run @kt-biome/creatures/swe --no-session
```

不會產生 `.kohakutr`。這也會讓壓縮無法從磁碟回收之前的回合 (但記憶體裡還是會壓)。

## 疑難排解

- **壓縮跑不完 / OOM。** Compact model 預設是跟控制器一樣的重模型。把 `compact_model` 設成便宜的 (`gpt-4o-mini`、`claude-haiku`)。
- **Resume 出現 `tool not registered`。** 生物 config 改了 (某個工具被拿掉)，但對話還在參照它。手動把 `config.yaml` 裡的工具加回來，或開新工作階段。
- **`kt resume` 找不到我剛剛看到的工作階段。** 工作階段是用檔名前綴去比對 `~/.kohakuterrarium/sessions/` 的。如果你改名或搬過，就傳完整路徑。
- **`.kohakutr` 很大。** 事件日誌是 append-only；長工作階段會膨脹。封存舊的、或把工作切到不同工作階段。壓縮縮的是活動對話，完整事件歷程還是留著給搜尋用。
- **Resume 看不到子代理輸出。** 子代理對話是在它完成時才存的。如果父代理在子代理跑到一半時被打斷，最新快照就只到上一個 checkpoint 為止。

## 延伸閱讀

- [記憶](memory.md) — 在工作階段歷程上做 FTS、語意、混合搜尋。
- [設定](configuration.md) — 壓縮 recipe 與工作階段旗標。
- [程式化使用](programmatic-usage.md) — 給自訂檢視用的 `SessionStore` API。
- [概念 / 記憶與壓縮](../concepts/modules/memory-and-compaction.md) — 壓縮怎麼運作。
