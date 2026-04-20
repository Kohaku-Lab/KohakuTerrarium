---
title: 服务化部署 (Serving)
summary: " `kt serve` 提供 HTTP API + WebSocket + Web dashboard，另外还有 `kt app` 提供原生桌面版。"
tags:
 - guides
 - serving
 - http
---

# 服务化部署 (Serving)

给想执行 KohakuTerrarium Web UI、桌面 app，或长时间常驻 daemon 的读者。

共有三个指令：`kt web`（前景 Web 服务器）、`kt app`（通过 pywebview 开桌面视窗）、`kt serve`（分离式 daemon）。它们共用同一个 FastAPI 后端与 Vue 前端；差别在生命周期与传输方式。

概念先读：[agent 作为 Python 物件](../concepts/python-native/agent-as-python-object.md) —— serving 这层本质上只是核心 runtime 的另一个 consumer。

## 我该用哪一个？

| Surface | Lifecycle | 使用时机 |
|---|---|---|
| `kt web` | 前景执行；Ctrl+C 即结束 | 你想在本地浏览器打开 `http://127.0.0.1:8001`。 |
| `kt app` | 前景执行；关闭视窗即结束 | 想要有原生桌面感的 app。需要 `pywebview`。 |
| `kt serve` | 分离式 daemon；关掉终端机后仍存活 | 长时间工作的 agent、SSH 工作、远端主机、持久化流程。 |

三者都使用相同的 API 与前端。请依生命周期需求选择。

## `kt web`

```bash
kt web
kt web --host 0.0.0.0 --port 9000
kt web --dev
kt web --log-level DEBUG
```

- 默认 host 是 `127.0.0.1`，port 是 `8001`（若被占用会自动递增）。
- `--dev` 只提供 API；前端 HMR 请另外执行 `npm run dev --prefix src/kohakuterrarium-frontend`。
- 会一直跑到你按 Ctrl+C。

如果前端还没 build，你会看到 placeholder——从原始码建一次即可：

```bash
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

若是从 PyPI 安装，通常已经内含 build 好的资产。

## `kt app`

```bash
kt app
kt app --port 8002
```

它会通过 pywebview 开一个原生桌面视窗，背后连的是内嵌 API 服务器。需要安装 desktop extra：

```bash
pip install 'kohakuterrarium[full]'
```

关闭视窗后，服务器也会一起停止。

## `kt serve`

```bash
kt serve start                  # 分离式 daemon
kt serve start --host 0.0.0.0 --port 8001 --dev --log-level INFO
kt serve status                 # running/stopped/stale、PID、URL、uptime
kt serve logs --follow          # 持续 tail daemon log
kt serve logs --lines 200
kt serve stop                   # SIGTERM + grace（默认 5s）后再 SIGKILL
kt serve stop --timeout 30
kt serve restart                # 先 stop 再 start
```

状态档：

```
~/.kohakuterrarium/run/web.pid    # process id
~/.kohakuterrarium/run/web.json   # url、host、port、started_at、git commit、version
~/.kohakuterrarium/run/web.log    # stdout + stderr
```

如果 PID 档存在，但程序已不存在，`kt serve status` 会回报 `stale`。你可以手动删除 `rm ~/.kohakuterrarium/run/web.*`，或让 `kt serve start` 自动清理。

### Dev daemon

```bash
kt serve start --dev
npm run dev --prefix src/kohakuterrarium-frontend
```

前端 HMR 会打到 daemon API，而 daemon 又能在终端机关闭后继续存活；两者就能同时兼得。

## 什么时候适合用 daemon

- SSH session 常常断线——用 `kt serve start` 跑著，再通过 `ssh -L 8001:localhost:8001` 重连。
- 远端机器上，你不想一直保留一个开著的终端机。
- 长期监控型 agent，不该因为终端机消失就被杀掉。
- 多个用户要连同一个实例（可绑 `--host 0.0.0.0`，但请搭配有验证的 reverse proxy——API 本身没有内置 auth）。

## API 本身

三种 surface 暴露的都是同一个 FastAPI app：

- REST endpoints：`/api/agents`、`/api/terrariums`、`/api/creatures`、`/api/channels`、`/api/configs`、`/api/sessions`
- WebSocket endpoints：用于串流聊天、观察频道、tail log

完整端点列表请看：[参考 / HTTP API](../reference/http.md)。

## 疑难排解

- **`kt web` 打印 "frontend not built"**。 请先做上面的 build，或用 `kt web --dev` 并另外跑 `vite dev`。
- **`kt serve status` 显示 `stale`**。 通常是被 `kill -9` 后留下的 stale PID 档。再跑一次 `kt serve start`（它会清理），或手动删除 `~/.kohakuterrarium/run/web.*`。
- **两个实例在抢 port 8001**。 `kt web` 会自动递增；`kt serve` 若设置的 port 被占用则会失败。请改用 `--port`。
- **`kt web` 没有自动打开浏览器**。 它只会打印 URL，请自己打开。
- **从另一台主机连不到 daemon**。 你绑的是 `127.0.0.1`。请用 `--host 0.0.0.0` 重启，并放在 proxy 后面。
- **`kt app` 一启动就崩溃**。 通常是少了 `pywebview`。请安装 `pip install 'kohakuterrarium[full]'`，或退回用 `kt web`。

## 延伸阅读

- [前端布局指南](frontend-layout.md) — UI 中有哪些 panel 和 preset。
- [参考 / HTTP API](../reference/http.md) — REST + WebSocket 端点。
- [参考 / CLI](../reference/cli.md) — `kt web`、`kt app`、`kt serve` 的旗标。
- [ROADMAP](../../ROADMAP.md) — 规划中的 daemon 驱动工作流程。
