---
title: Rich CLI 多 creature
summary: 在 `kt run --mode cli` 中使用多 creature terrarium —— roster、焦點切換、@name 重導向、slash 指令、Ctrl+A 總覽。
tags:
  - guides
  - cli
  - terrarium
---

# Rich CLI 多 creature

`kt run --mode cli` 開啟 rich 行內 CLI。**單 creature 設定**與 1.4 行為
一致 —— 帶邊框的輸入框、live 區域、slash 指令、prompt-toolkit 歷史。
**多 creature terrarium** 則在輸入框上方顯示一行 **roster**，並提供
焦點切換、按 creature 保留的草稿、`@name` 重導向、以及拓樸感知的
slash 指令。

## Roster

載入多個 creature 時，輸入框上方會出現一行：

```
  ┌─ Creatures ─────────────────────────────────────────────────────┐
  │ ▸clawd ● Edit src/sprite.py    physics ○ idle 2m   power-up ⚠   │
  └─────────────────────────────────────────────────────────────────┘
  [clawd]> _
```

每個槽位是 `<焦點標記><名稱> <狀態符號> <活動摘要>`：

| 符號 | 狀態 | 含義 |
|---|---|---|
| `●` | working | LLM 正在生成，或工具 / 子 agent 正在執行 |
| `○` | idle | 沒有進行中的活動 |
| `⚠` | waiting | creature 正在等你回答（`ask_user`、permgate） |
| `✗` | failed | 上一輪發生例外 |
| `■` | stopped | 已明確停止 |

`▸` 標記目前**焦點 creature** —— 你的輸入會送到它。非焦點 creature 自
上次檢視以來有新活動時，名稱旁會出現 `●N` 標章。

### 終端較窄時

如果空間不夠，idle / stopped 的 creature 會折疊為計數：

```
  ┌─ Creatures ─────────────────────────────────────────────────────┐
  │ ▸power-up ⚠ needs choice   ●clawd Edit src...   ●collision...   │
  │   +2 idle  +1 stopped                                           │
  └─────────────────────────────────────────────────────────────────┘
```

working 與 waiting 的 creature 永遠以名稱可見（它們是需要你注意的）。

## 焦點切換

| 按鍵 | 作用 |
|---|---|
| `Tab` | 切到下一個 creature |
| `Shift+Tab` | 切到上一個 creature |
| `Ctrl+A` | 開啟 agent 總覽（見下） |

切換焦點時,rich CLI 會做**完整的上下文切換** —— 你只會看到新焦點
creature 的歷史,而不是所有 creature 的交錯日誌:

- 輸入提示前綴變化:`[clawd]> ` → `[physics]> `
- live 區域(進行中的串流 + 活躍工具)切到該 creature 的緩衝
- **底部列**會依照新 creature 的 agent 重繪 —— 模型名稱、上下文預算、
  token 累計都反映 `physics` 實際在跑的內容,而不是 `clawd` 之前的內容
- **未送出的草稿與你之前定位的 creature 一起保留** —— 切回來時半寫
  的訊息還在
- **終端捲動歷史會清空並重新回放**:新焦點 creature 的每一則已提交
  訊息、工具結果面板、提示都會重新送進捲動區。PgUp / 滑鼠向上捲動
  之後只會看到該 creature 的歷史。共享的交錯日誌不復存在 —— Tab 是
  真正的上下文切換,而不是「偷看」。

重繪由對話進行時在記憶體中捕捉的「每 creature commit 日誌」驅動。
尚未完成的串流文字不會被捕捉(它還在產生它的 creature 的 live 區裡),
但所有已提交到捲動區的內容 —— 使用者訊息、完成的助理回合、工具區塊、
子代理面板 —— 都會在每次切換時忠實重放。

一個自然的代價:長時間執行、creature 數量多時,每次 Tab 都會觸發
真正的螢幕重繪。幾百回合內你幾乎感覺不到;非常長的會話中,切換會是
CLI 裡最慢的互動動作。

## `@name` 重導向

要在不切換焦點的情況下向某個 creature 送出一則訊息，在輸入前加
`@<name>`：

```
  [clawd]> @physics 碰撞檢測回傳了什麼？
                ↑ 送到 physics，焦點仍在 clawd
```

`@all <msg>` 廣播到所有 creature,但只有焦點是**特權** creature
(recipe 根或使用者啟動的頂層)時才有效。

`@name` 訊息記錄在**接收方**的捲動歷史中,不在發送方 ——
之後切到 `physics` 你能看到問題和 physics 的回答在一起,不會孤立。
`@all` 廣播會寫入每個 creature 的歷史,這樣切到哪個 creature
都能看到同樣的可見上下文。

## Slash 指令

既有的 slash 指令（`/clear`、`/model`、`/status`、`/scratchpad` 等）
作用於**焦點** creature。1.5 新增的拓樸感知指令：

| 指令 | 作用 |
|---|---|
| `/stop` | 停止焦點 creature |
| `/stop <name>` | 停止指定 creature |
| `/start` / `/start <name>` | 啟動已停止的 creature |
| `/spawn <recipe>` | 啟動一個新 creature（僅特權焦點） |
| `/jobs` | 列出焦點 creature 正在執行的 jobs |
| `/channels` | 列出焦點 creature 參與的 channels |
| `/scratchpad` | 顯示焦點 creature 的草稿區 |

## Ctrl+A — agent 總覽

按 `Ctrl+A` 開啟按狀態分組的完整清單：

```
  ┌─ Agent view ───────────────────────────────────────────────────┐
  │ Filter: [           ]                              Esc to close│
  ├────────────────────────────────────────────────────────────────┤
  │ Needs input                                                    │
  │   ⚠ power-up        needs: double jump or wall climb?    15s   │
  │ Working                                                        │
  │   ● clawd           Edit src/sprite.py                    3s   │
  │   ● collision       bash: pytest tests/collision.py      12s   │
  │ Idle                                                           │
  │   ○ physics         idle 2m                                    │
  │ Stopped                                                        │
  │   ■ debug-helper    stopped 30m ago                            │
  ├────────────────────────────────────────────────────────────────┤
  │ ↑↓ select  Space peek  Enter focus  Esc close                  │
  └────────────────────────────────────────────────────────────────┘
```

| 按鍵 | 動作 |
|---|---|
| `↑` / `↓` | 移動選擇 |
| `Space` | peek 選中 creature（右側預覽，不切焦點） |
| `Enter` | 切到選中 creature 並關閉總覽 |
| `→` | （peek 時）將 peek 提升為焦點 |
| `Esc` | 關閉總覽 |
| _鍵入_ | 依名稱 / 活動過濾清單（不分大小寫） |

### Peek

`Space` 在選中列開啟右側面板，顯示該 creature 最近 30 秒的輸出。
**peek 開啟時鍵入的內容會送到 peek 的 creature** —— 用於不切焦點回答
`ask_user` 提示。

## 何時選用此前端

| 你想…… | 使用 |
|---|---|
| 單 creature 聊天，快速、寫入捲動歷史 | `kt run --mode cli`（本指南） |
| 視覺化圖形編輯、多分頁、web | `kt app` / `kt serve`（web UI） |
| creature 樹、channel 紀錄、滑鼠 | `kt run --mode tui`（Textual） |

rich CLI 的多 creature 介面偏向鍵盤優先；如需視覺化拓樸編輯或瀏覽器
attach，請使用 web UI。

## 另請參閱

- [`kt --help`](../../README.md) — 完整 CLI 參考
- [CLI 與 UI 對照](cli-and-ui-equivalents.md) — 每個 `kt` 指令對應的 UI 位置
- [設定指南](configuration.md) — terrarium recipe 的樣子
