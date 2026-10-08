/**
 * The session's drives store, shared by the dock, the drives widget, the
 * drive detail and the Status table. Keyed by the shell's instance id
 * (fixed for the shell's life), so a graph merge — which changes
 * `ctx.sessionId` — keeps one store; the store (re)loads whenever the
 * session id changes. With `poll`, it reconciles when the owner is shown
 * and every 8 s while it stays shown; `kept` owners (inside KeepAlive)
 * pause when deactivated.
 */

import { onActivated, onBeforeUnmount, onDeactivated, onMounted, watch } from "vue"

import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useDrivesStore } from "@/stores/drives"

export const DRIVE_POLL_MS = 8000

export function useSessionDrives(ctx, { poll = false, kept = false } = {}) {
  const store = useDrivesStore(ctx.instanceId.value || undefined)
  const ensureLoaded = () => {
    const sid = ctx.sessionId.value
    if (sid && store.sessionId !== sid) return store.load(sid)
    return null
  }
  watch(() => ctx.sessionId.value, ensureLoaded, { immediate: true })

  if (poll) {
    const poller = createVisibilityInterval(() => store.reconcile(), DRIVE_POLL_MS)
    const start = () => {
      if (!ensureLoaded() && store.sessionId) store.reconcile()
      poller.start()
    }
    if (kept) {
      onActivated(start)
      onDeactivated(() => poller.stop())
    } else {
      onMounted(start)
    }
    onBeforeUnmount(() => poller.stop())
  }
  return { store, ensureLoaded }
}
