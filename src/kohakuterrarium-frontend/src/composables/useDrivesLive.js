/**
 * Keep a drives store live for one session: the initial load, a periodic
 * reconcile poller, and the runtime-graph WebSocket whose Drive EngineEvents
 * fold into the store. The poller is the reliable backstop when the socket is
 * down or an event is missed.
 *
 * Shared by the full Drives panel and the compact Creature State tab. Stops
 * cleanly on session change and unmount, including a pending reconnect timer
 * so its callback can never reopen a socket for a session that went away
 * (R1-39).
 */

import { onBeforeUnmount, onMounted, watch } from "vue"

import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { wsUrl } from "@/utils/wsUrl"

export function useDrivesLive(sessionId, store, { intervalMs = 6000, onStart = null } = {}) {
  let poller = null
  let ws = null
  let wsClosedByUs = false
  let reconnectTimer = null

  function start() {
    store.load(sessionId.value)
    if (onStart) onStart()
    poller = createVisibilityInterval(() => store.reconcile(), intervalMs)
    poller.start()
    startLive()
  }

  function stopLive() {
    if (poller) {
      poller.stop()
      poller = null
    }
    wsClosedByUs = true
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (ws) {
      ws.close()
      ws = null
    }
  }

  function startLive() {
    if (typeof WebSocket === "undefined") return
    wsClosedByUs = false
    try {
      ws = new WebSocket(wsUrl("/ws/runtime/graph"))
      ws.onmessage = (event) => {
        let data
        try {
          data = JSON.parse(event.data)
        } catch {
          return
        }
        const kind = data.kind || data.type || ""
        if (String(kind).startsWith("drive")) store.applyEvent(data)
      }
      ws.onclose = () => {
        ws = null
        if (!wsClosedByUs) {
          reconnectTimer = setTimeout(() => {
            reconnectTimer = null
            if (!wsClosedByUs && sessionId.value) startLive()
          }, 2000)
        }
      }
      ws.onerror = () => {}
    } catch {
      /* live updates are best-effort; reconcile covers the gap */
    }
  }

  onMounted(() => {
    if (sessionId.value) start()
  })

  watch(sessionId, (id, prev) => {
    if (id === prev) return
    stopLive()
    if (id) start()
  })

  onBeforeUnmount(stopLive)

  return { restart: start, stop: stopLive }
}
