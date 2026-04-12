/**
 * useLogStream — opens a websocket to /ws/logs and keeps a reactive
 * circular buffer of parsed log lines. Auto-reconnects with backoff.
 *
 * The backend endpoint is the one added in Phase 1
 * (src/kohakuterrarium/api/ws/logs.py). Each incoming message is
 * either `{type: "meta", ...}`, `{type: "line", ts, level, module,
 * text}`, or `{type: "error", text}`.
 */

import { onMounted, onUnmounted, ref } from "vue"

const BUFFER_SIZE = 5000

function _wsUrl(path) {
  if (typeof window === "undefined") return path
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:"
  return `${scheme}//${window.location.host}${path}`
}

export function useLogStream() {
  const lines = ref(
    /** @type {Array<{ts: string, level: string, module: string, text: string}>} */ ([]),
  )
  const meta = ref(/** @type {{path: string, pid: number} | null} */ (null))
  const connected = ref(false)
  const error = ref("")

  let ws = null
  let retryTimer = null
  let retryDelay = 500
  let closedByCaller = false

  function connect() {
    closedByCaller = false
    try {
      ws = new WebSocket(_wsUrl("/ws/logs"))
    } catch (err) {
      error.value = String(err)
      scheduleReconnect()
      return
    }

    ws.onopen = () => {
      connected.value = true
      error.value = ""
      retryDelay = 500
    }

    ws.onmessage = (ev) => {
      let data
      try {
        data = JSON.parse(ev.data)
      } catch {
        return
      }
      if (data.type === "meta") {
        meta.value = { path: data.path, pid: data.pid }
        return
      }
      if (data.type === "error") {
        error.value = data.text || ""
        return
      }
      if (data.type === "line") {
        lines.value.push({
          ts: data.ts || "",
          level: data.level || "info",
          module: data.module || "",
          text: data.text || "",
        })
        // Circular trim
        if (lines.value.length > BUFFER_SIZE) {
          lines.value = lines.value.slice(-BUFFER_SIZE)
        }
      }
    }

    ws.onerror = () => {
      error.value = "WebSocket error"
    }

    ws.onclose = () => {
      connected.value = false
      ws = null
      if (!closedByCaller) scheduleReconnect()
    }
  }

  function scheduleReconnect() {
    if (retryTimer) clearTimeout(retryTimer)
    retryTimer = setTimeout(() => {
      retryDelay = Math.min(retryDelay * 2, 5000)
      connect()
    }, retryDelay)
  }

  function disconnect() {
    closedByCaller = true
    if (retryTimer) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    if (ws) {
      try {
        ws.close()
      } catch {
        // ignore
      }
      ws = null
    }
    connected.value = false
  }

  function clear() {
    lines.value = []
  }

  onMounted(connect)
  onUnmounted(disconnect)

  return { lines, meta, connected, error, clear, connect, disconnect }
}
