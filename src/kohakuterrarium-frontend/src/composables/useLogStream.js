/**
 * useLogStream — opens a websocket to /ws/logs and keeps a reactive
 * circular buffer of parsed log lines. Auto-reconnects with backoff.
 *
 * The backend endpoint is the one added in Phase 1
 * (src/kohakuterrarium/api/ws/logs.py). Each incoming message is
 * either `{type: "meta", ...}`, `{type: "line", ts, level, module,
 * text}`, or `{type: "error", text}`. Each kept line carries a `seq`
 * that increases across the stream's life. With `autoConnect: false`
 * the caller opens and closes the socket.
 */

import { onMounted, onUnmounted, ref } from "vue"

import { wsUrl as _wsUrl } from "@/utils/wsUrl"

const BUFFER_SIZE = 5000

export function useLogStream({ autoConnect = true } = {}) {
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
  let seq = 0

  function connect() {
    if (ws) return
    closedByCaller = false
    let socket
    try {
      socket = new WebSocket(_wsUrl("/ws/logs"))
    } catch (err) {
      error.value = String(err)
      scheduleReconnect()
      return
    }
    ws = socket

    socket.onopen = () => {
      if (ws !== socket) return
      // Every connection replays the log's recent tail, so it replaces what was kept.
      lines.value = []
      connected.value = true
      error.value = ""
      retryDelay = 500
    }

    socket.onmessage = (ev) => {
      if (ws !== socket) return
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
          seq: seq++,
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

    socket.onerror = () => {
      if (ws === socket) error.value = "WebSocket error"
    }

    socket.onclose = () => {
      if (ws !== socket) return
      connected.value = false
      ws = null
      if (!closedByCaller) scheduleReconnect()
    }
  }

  function scheduleReconnect() {
    if (retryTimer) clearTimeout(retryTimer)
    retryTimer = setTimeout(() => {
      retryTimer = null
      retryDelay = Math.min(retryDelay * 2, 5000)
      if (!closedByCaller) connect()
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

  if (autoConnect) onMounted(connect)
  onUnmounted(disconnect)

  return { lines, meta, connected, error, clear, connect, disconnect }
}
