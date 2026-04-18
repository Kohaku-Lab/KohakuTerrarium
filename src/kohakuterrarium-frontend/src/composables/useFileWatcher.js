/**
 * useFileWatcher — connects to /ws/files/{agentId} and exposes a
 * reactive list of recent file changes. The FilesPanel and EditorMain
 * can watch this to auto-refresh.
 */

import { onMounted, onUnmounted, ref, watch } from "vue"

import { wsUrl as _wsUrl } from "@/utils/wsUrl"

export function useFileWatcher(agentIdRef) {
  const changes = ref([])
  const connected = ref(false)
  /** Bumps on every batch of changes — watchers can use this as a trigger. */
  const revision = ref(0)

  let ws = null
  let retryTimer = null
  let retryDelay = 2000
  let retryCount = 0
  const MAX_RETRIES = 5
  let closed = false

  function connect(agentId) {
    disconnect()
    if (!agentId) return
    closed = false

    try {
      ws = new WebSocket(_wsUrl(`/ws/files/${agentId}`))
    } catch {
      scheduleRetry(agentId)
      return
    }

    ws.onopen = () => {
      connected.value = true
      retryDelay = 2000
      retryCount = 0
    }

    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data)
        if (data.type === "change" && Array.isArray(data.changes)) {
          changes.value = data.changes
          revision.value++
        }
      } catch {
        // ignore parse errors
      }
    }

    ws.onclose = () => {
      connected.value = false
      ws = null
      if (!closed) scheduleRetry(agentId)
    }

    ws.onerror = () => {
      // onclose will fire after this
    }
  }

  function scheduleRetry(agentId) {
    if (retryCount >= MAX_RETRIES) return // stop retrying
    retryCount++
    if (retryTimer) clearTimeout(retryTimer)
    retryTimer = setTimeout(() => {
      retryDelay = Math.min(retryDelay * 2, 30000)
      connect(agentId)
    }, retryDelay)
  }

  function disconnect() {
    closed = true
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
  }

  // Auto-connect when agentId changes.
  watch(
    agentIdRef,
    (id) => {
      if (id) connect(id)
      else disconnect()
    },
    { immediate: true },
  )

  onMounted(() => {
    const id = typeof agentIdRef === "function" ? agentIdRef() : agentIdRef?.value
    if (id) connect(id)
  })

  onUnmounted(disconnect)

  return { changes, connected, revision }
}
