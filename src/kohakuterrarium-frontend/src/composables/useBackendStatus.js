import { onMounted, ref } from "vue"

import { useVisibilityInterval } from "@/composables/useVisibilityInterval"
import { getBackendStatus, restartBackend } from "@/utils/hostBridge"

const REFRESH_MS = 5000

/** Describe a duration in seconds as "3d 4h", "2h 5m", "7m 3s" or "12s". */
export function formatUptime(seconds) {
  const total = Math.max(0, Math.floor(seconds))
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${total % 60}s`
  return `${total}s`
}

/** Bytes as whole megabytes, e.g. "612 MB". */
export function formatMemory(bytes) {
  return `${Math.round(bytes / (1024 * 1024))} MB`
}

/** The backend's status as the shell reports it, refreshed while the page is visible; ``null`` outside the Tauri shell. */
export function useBackendStatus() {
  const status = ref(null)

  async function refresh() {
    status.value = await getBackendStatus()
  }

  async function restart() {
    if (await restartBackend()) status.value = { ...(status.value || {}), state: "restarting" }
  }

  onMounted(refresh)
  useVisibilityInterval(refresh, REFRESH_MS)
  return { status, refresh, restart }
}
