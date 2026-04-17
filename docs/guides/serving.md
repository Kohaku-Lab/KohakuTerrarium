# Serving

For readers running KohakuTerrarium's web UI, the desktop app, or a long-lived daemon.

Three commands: `kt web` (foreground web server), `kt app` (desktop window via pywebview), `kt serve` (detached daemon). They share the same FastAPI backend and Vue frontend; they differ in lifecycle and transport.

Concept primer: [agent as a Python object](../concepts/python-native/agent-as-python-object.md) — the serving layer is just another consumer of the core runtime.

## Which one do I want?

| Surface | Lifecycle | When |
|---|---|---|
| `kt web` | Foreground; exits when you Ctrl+C | You want to open `http://127.0.0.1:8001` in a browser on this machine. |
| `kt app` | Foreground; exits when you close the window | Native-feeling desktop app. Needs `pywebview`. |
| `kt serve` | Detached daemon; outlives the terminal | Long-running agents, SSH sessions, remote boxes, persistent workflows. |

All three use the same API and frontend. Pick based on lifecycle.

## `kt web`

```bash
kt web
kt web --host 0.0.0.0 --port 9000
kt web --dev
kt web --log-level DEBUG
```

- Default host `127.0.0.1`, port `8001` (auto-increments if busy).
- `--dev` serves API only; run `npm run dev --prefix src/kohakuterrarium-frontend` separately for HMR.
- Runs until Ctrl+C.

Without a built frontend you'll see a placeholder — build it once from source:

```bash
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

PyPI installs ship the built assets.

## `kt app`

```bash
kt app
kt app --port 8002
```

Opens a native desktop window using pywebview, talking to an embedded API server. Requires the desktop extra:

```bash
pip install 'kohakuterrarium[full]'
```

When you close the window, the server stops.

## `kt serve`

```bash
kt serve start                  # detached daemon
kt serve start --host 0.0.0.0 --port 8001 --dev --log-level INFO
kt serve status                 # running/stopped/stale, PID, URL, uptime
kt serve logs --follow          # tail the daemon log
kt serve logs --lines 200
kt serve stop                   # SIGTERM + grace (default 5s) then SIGKILL
kt serve stop --timeout 30
kt serve restart                # stop then start
```

State files:

```
~/.kohakuterrarium/run/web.pid    # process id
~/.kohakuterrarium/run/web.json   # url, host, port, started_at, git commit, version
~/.kohakuterrarium/run/web.log    # stdout + stderr
```

`kt serve status` reports `stale` if the PID file exists but the process doesn't — remove with `rm ~/.kohakuterrarium/run/web.*` or let `kt serve start` clean it.

### Dev daemon

```bash
kt serve start --dev
npm run dev --prefix src/kohakuterrarium-frontend
```

Frontend HMR hits the daemon API, daemon outlives the terminal, you get both.

## When to prefer the daemon

- SSH session keeps disconnecting — work in `kt serve start` + reconnect with `ssh -L 8001:localhost:8001`.
- Remote machine you don't always want to keep a terminal open on.
- Long-lived monitoring agent that should never be killed by a lost terminal.
- Multiple users connecting to the same instance (bind `--host 0.0.0.0`, but use a reverse proxy with auth — the API has no built-in auth).

## The API itself

All three surfaces expose the same FastAPI app:

- REST endpoints under `/api/agents`, `/api/terrariums`, `/api/creatures`, `/api/channels`, `/api/configs`, `/api/sessions`
- WebSocket endpoints for streaming chat, channel observation, log tailing

Full endpoint list: [Reference / HTTP API](../reference/http.md).

## Troubleshooting

- **`kt web` prints "frontend not built".** See the build step above, or use `kt web --dev` and run `vite dev` separately.
- **`kt serve status` says `stale`.** Stale PID file from a kill -9. Run `kt serve start` again (it cleans) or remove `~/.kohakuterrarium/run/web.*`.
- **Two instances fighting for port 8001.** `kt web` auto-increments; `kt serve` fails if the configured port is taken. Use `--port`.
- **Browser doesn't open for `kt web`.** It only prints the URL. Open manually.
- **Can't reach daemon from another host.** You bound to `127.0.0.1`. Re-start with `--host 0.0.0.0` and front it with a proxy.
- **`kt app` crashes immediately.** Missing `pywebview`. Install with `pip install 'kohakuterrarium[full]'` or fall back to `kt web`.

## See also

- [Frontend Layout](frontend-layout.md) — what panels and presets are available in the UI.
- [Reference / HTTP API](../reference/http.md) — REST + WebSocket endpoints.
- [Reference / CLI](../reference/cli.md) — `kt web`, `kt app`, `kt serve` flags.
- [ROADMAP](../../ROADMAP.md) — planned daemon-backed workflows.
