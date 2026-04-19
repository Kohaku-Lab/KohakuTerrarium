---
title: Serving
summary: `kt serve` 提供 HTTP API + WebSocket + Web dashboard，另外還有 `kt app` 提供原生桌面版。
tags:
  - guides
  - serving
  - http
---

# Serving

給想執行 KohakuTerrarium Web UI、桌面 app，或長時間常駐 daemon 的讀者。

共有三個指令：`kt web`（前景 Web 伺服器）、`kt app`（透過 pywebview 開桌面視窗）、`kt serve`（分離式 daemon）。它們共用同一個 FastAPI 後端與 Vue 前端；差別在生命週期與傳輸方式。

概念先讀：[agent 作為 Python 物件](../concepts/python-native/agent-as-python-object.md) —— serving 這層本質上只是核心 runtime 的另一個 consumer。

## 我該用哪一個？

| Surface | Lifecycle | 使用時機 |
|---|---|---|
| `kt web` | 前景執行；Ctrl+C 即結束 | 你想在本機瀏覽器打開 `http://127.0.0.1:8001`。 |
| `kt app` | 前景執行；關閉視窗即結束 | 想要有原生桌面感的 app。需要 `pywebview`。 |
| `kt serve` | 分離式 daemon；關掉終端機後仍存活 | 長時間運作的 agent、SSH 工作、遠端主機、持久化流程。 |

三者都使用相同的 API 與前端。請依生命週期需求選擇。

## `kt web`

```bash
kt web
kt web --host 0.0.0.0 --port 9000
kt web --dev
kt web --log-level DEBUG
```

- 預設 host 是 `127.0.0.1`，port 是 `8001`（若被佔用會自動遞增）。
- `--dev` 只提供 API；前端 HMR 請另外執行 `npm run dev --prefix src/kohakuterrarium-frontend`。
- 會一直跑到你按 Ctrl+C。

如果前端還沒 build，你會看到 placeholder——從原始碼建一次即可：

```bash
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

若是從 PyPI 安裝，通常已經內含 build 好的資產。

## `kt app`

```bash
kt app
kt app --port 8002
```

它會透過 pywebview 開一個原生桌面視窗，背後連的是內嵌 API 伺服器。需要安裝 desktop extra：

```bash
pip install 'kohakuterrarium[full]'
```

關閉視窗後，伺服器也會一起停止。

## `kt serve`

```bash
kt serve start                  # 分離式 daemon
kt serve start --host 0.0.0.0 --port 8001 --dev --log-level INFO
kt serve status                 # running/stopped/stale、PID、URL、uptime
kt serve logs --follow          # 持續 tail daemon log
kt serve logs --lines 200
kt serve stop                   # SIGTERM + grace（預設 5s）後再 SIGKILL
kt serve stop --timeout 30
kt serve restart                # 先 stop 再 start
```

狀態檔：

```
~/.kohakuterrarium/run/web.pid    # process id
~/.kohakuterrarium/run/web.json   # url、host、port、started_at、git commit、version
~/.kohakuterrarium/run/web.log    # stdout + stderr
```

如果 PID 檔存在，但程序已不存在，`kt serve status` 會回報 `stale`。你可以手動刪除 `rm ~/.kohakuterrarium/run/web.*`，或讓 `kt serve start` 自動清理。

### Dev daemon

```bash
kt serve start --dev
npm run dev --prefix src/kohakuterrarium-frontend
```

前端 HMR 會打到 daemon API，而 daemon 又能在終端機關閉後繼續存活；兩者就能同時兼得。

## 什麼時候適合用 daemon

- SSH session 常常斷線——用 `kt serve start` 跑著，再透過 `ssh -L 8001:localhost:8001` 重連。
- 遠端機器上，你不想一直保留一個開著的終端機。
- 長期監控型 agent，不該因為終端機消失就被殺掉。
- 多個使用者要連同一個實例（可綁 `--host 0.0.0.0`，但請搭配有驗證的 reverse proxy——API 本身沒有內建 auth）。

## API 本身

三種 surface 暴露的都是同一個 FastAPI app：

- REST endpoints：`/api/agents`、`/api/terrariums`、`/api/creatures`、`/api/channels`、`/api/configs`、`/api/sessions`
- WebSocket endpoints：用於串流聊天、觀察頻道、tail log

完整端點列表請看：[參考 / HTTP API](../reference/http.md)。

## 疑難排解

- **`kt web` 印出 "frontend not built"。** 請先做上面的 build，或用 `kt web --dev` 並另外跑 `vite dev`。
- **`kt serve status` 顯示 `stale`。** 通常是被 `kill -9` 後留下的 stale PID 檔。再跑一次 `kt serve start`（它會清理），或手動刪除 `~/.kohakuterrarium/run/web.*`。
- **兩個實例在搶 port 8001。** `kt web` 會自動遞增；`kt serve` 若設定的 port 被占用則會失敗。請改用 `--port`。
- **`kt web` 沒有自動開瀏覽器。** 它只會印出 URL，請自己打開。
- **從另一台主機連不到 daemon。** 你綁的是 `127.0.0.1`。請用 `--host 0.0.0.0` 重啟，並放在 proxy 後面。
- **`kt app` 一開就崩。** 通常是少了 `pywebview`。請安裝 `pip install 'kohakuterrarium[full]'`，或退回用 `kt web`。

## 延伸閱讀

- [前端版面](frontend-layout.md) — UI 裡有哪些 panel 與 preset。
- [參考 / HTTP API](../reference/http.md) — REST + WebSocket 端點。
- [參考 / CLI](../reference/cli.md) — `kt web`、`kt app`、`kt serve` 的旗標。
- [ROADMAP](../../ROADMAP.md) — 規劃中的 daemon 驅動工作流程。
