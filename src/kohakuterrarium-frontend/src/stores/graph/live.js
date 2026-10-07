/**
 * Live runtime-graph feed shared by every graph view: one snapshot, one
 * `/ws/runtime/graph` connection (reference counted), a light status poll,
 * per-channel last message and one-shot pulse timestamps.
 */

import { defineStore } from "pinia"
import { computed, reactive, ref, shallowRef } from "vue"

import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { runtimeGraphAPI } from "@/utils/api"
import { buildGraphModel, channelNodeId } from "@/utils/graph/data/model"
import { wsUrl } from "@/utils/wsUrl"

/** Engine events after which the snapshot is re-read. */
export const REFRESH_EVENTS = new Set([
  "topology_changed",
  "creature_added",
  "creature_started",
  "creature_stopped",
  "output_wire_added",
  "output_wire_removed",
  "parent_link_changed",
  "session_kind_changed",
])

const REFRESH_DEBOUNCE_MS = 150
const STATUS_POLL_MS = 4000
const BACKOFF_MIN_MS = 500
const BACKOFF_MAX_MS = 10000

export const useGraphLiveStore = defineStore("graphLive", () => {
  const snapshot = shallowRef(null)
  const loading = ref(false)
  const error = ref("")
  const wsStatus = ref("closed")
  const lastMessages = reactive({})
  const pulses = reactive({})

  const model = computed(() => buildGraphModel(snapshot.value || { graphs: [] }))

  let refs = 0
  let ws = null
  let backoff = BACKOFF_MIN_MS
  let reconnectTimer = null
  let refreshTimer = null
  let inflight = null
  let poll = null
  let lastSnapshotText = ""

  async function refresh() {
    if (inflight) return inflight
    loading.value = !snapshot.value
    inflight = runtimeGraphAPI
      .snapshot()
      .then((data) => {
        const text = JSON.stringify(data)
        if (text !== lastSnapshotText) {
          lastSnapshotText = text
          snapshot.value = data
        }
        error.value = ""
      })
      .catch((err) => {
        error.value = err?.response?.data?.detail || err?.message || String(err)
      })
      .finally(() => {
        loading.value = false
        inflight = null
      })
    return inflight
  }

  function scheduleRefresh(delay = REFRESH_DEBOUNCE_MS) {
    if (refreshTimer) clearTimeout(refreshTimer)
    refreshTimer = setTimeout(() => {
      refreshTimer = null
      refresh()
    }, delay)
  }

  function handleEvent(event) {
    if (!event || typeof event !== "object") return
    if (event.type === "snapshot" && event.snapshot) {
      lastSnapshotText = JSON.stringify(event.snapshot)
      snapshot.value = event.snapshot
      return
    }
    if (event.type === "channel_message") {
      const sessionId = model.value.memberToSession[event.graph_id] || event.graph_id
      const id = channelNodeId(sessionId, event.channel)
      const previous = lastMessages[id]
      lastMessages[id] = {
        sender: event.sender || "",
        preview:
          event.content_preview ||
          (typeof event.content === "string" ? event.content.slice(0, 160) : ""),
        ts: event.timestamp || new Date().toISOString(),
        received: (previous?.received || 0) + 1,
      }
      pulses[id] = Date.now()
      return
    }
    if (REFRESH_EVENTS.has(event.type)) scheduleRefresh()
  }

  function connect() {
    if (ws || refs === 0 || typeof WebSocket === "undefined") return
    wsStatus.value = wsStatus.value === "closed" ? "connecting" : "reconnecting"
    const socket = new WebSocket(wsUrl("/ws/runtime/graph"))
    ws = socket
    socket.onopen = () => {
      wsStatus.value = "open"
      backoff = BACKOFF_MIN_MS
    }
    socket.onmessage = (msg) => {
      try {
        handleEvent(JSON.parse(msg.data))
      } catch {
        /* non-JSON frames carry nothing for the graph */
      }
    }
    socket.onclose = () => {
      if (ws === socket) ws = null
      if (refs === 0) {
        wsStatus.value = "closed"
        return
      }
      wsStatus.value = "reconnecting"
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        connect()
        refresh()
      }, backoff)
      backoff = Math.min(backoff * 2, BACKOFF_MAX_MS)
    }
  }

  function acquire() {
    refs += 1
    if (refs !== 1) return
    refresh()
    connect()
    poll = createVisibilityInterval(() => refresh(), STATUS_POLL_MS)
    poll.start()
  }

  function release() {
    refs = Math.max(0, refs - 1)
    if (refs !== 0) return
    poll?.stop()
    poll = null
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = null
    if (refreshTimer) clearTimeout(refreshTimer)
    refreshTimer = null
    const socket = ws
    ws = null
    wsStatus.value = "closed"
    socket?.close()
  }

  return {
    snapshot,
    model,
    loading,
    error,
    wsStatus,
    lastMessages,
    pulses,
    refresh,
    scheduleRefresh,
    handleEvent,
    acquire,
    release,
  }
})
