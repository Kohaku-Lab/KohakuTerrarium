/**
 * useSessionEventStream — websocket bridge to ``/ws/sessions/{name}/events``.
 *
 * Subscribes to live append-events from a running session. Optional
 * ``agent`` filter is forwarded to the backend so a viewer focused on
 * one creature is not flooded by sibling agents in a terrarium.
 *
 * Lossy by design: when the buffer overflows or the connection
 * disconnects, recent events are kept and older ones drop. The trace
 * viewer's timeline reads from the persistent event log on demand;
 * this stream only feeds the "↓ N new" banner + auto-scroll while
 * Live attach is on.
 *
 * Backend emits:
 *   {type: "subscribed", session_name, agent}
 *   {type: "event", key, event}
 *   {type: "error", reason?, message}
 */

import { onUnmounted, ref, watch } from "vue"

import { wsUrl as _wsUrl } from "@/utils/wsUrl"

const BUFFER_SIZE = 500

export function useSessionEventStream() {
  /** Reactive stream state — components bind to these. */
  const events = ref([])
  const connected = ref(false)
  const subscribed = ref(false)
  const error = ref("")
  const newSinceLastClear = ref(0)

  let ws = null
  let retryTimer = null
  let retryDelay = 500
  let closedByCaller = false
  let currentName = ""
  let currentAgent = null

  function _disconnect() {
    closedByCaller = true
    if (retryTimer) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    if (ws) {
      try {
        ws.close()
      } catch {
        /* ignore */
      }
      ws = null
    }
    connected.value = false
    subscribed.value = false
  }

  function _scheduleReconnect() {
    if (retryTimer) clearTimeout(retryTimer)
    retryTimer = setTimeout(() => {
      retryDelay = Math.min(retryDelay * 2, 5000)
      _open(currentName, currentAgent)
    }, retryDelay)
  }

  function _open(sessionName, agent) {
    closedByCaller = false
    let path = `/ws/sessions/${encodeURIComponent(sessionName)}/events`
    if (agent) path += `?agent=${encodeURIComponent(agent)}`
    try {
      ws = new WebSocket(_wsUrl(path))
    } catch (err) {
      error.value = String(err)
      _scheduleReconnect()
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
      if (data.type === "subscribed") {
        subscribed.value = true
        return
      }
      if (data.type === "error") {
        error.value = data.message || data.reason || "Unknown WS error"
        return
      }
      if (data.type === "event") {
        events.value.push({ key: data.key, event: data.event })
        newSinceLastClear.value += 1
        if (events.value.length > BUFFER_SIZE) {
          events.value = events.value.slice(-BUFFER_SIZE)
        }
      }
    }

    ws.onerror = () => {
      error.value = "WebSocket error"
    }

    ws.onclose = () => {
      connected.value = false
      subscribed.value = false
      ws = null
      if (!closedByCaller) _scheduleReconnect()
    }
  }

  function attach(sessionName, agent = null) {
    if (!sessionName) return
    detach()
    currentName = sessionName
    currentAgent = agent
    events.value = []
    newSinceLastClear.value = 0
    error.value = ""
    _open(sessionName, agent)
  }

  function detach() {
    _disconnect()
    currentName = ""
    currentAgent = null
  }

  function clearNewCounter() {
    newSinceLastClear.value = 0
  }

  onUnmounted(detach)

  return {
    events,
    connected,
    subscribed,
    error,
    newSinceLastClear,
    attach,
    detach,
    clearNewCounter,
  }
}
