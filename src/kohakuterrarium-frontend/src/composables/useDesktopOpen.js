import { onBeforeUnmount, onMounted } from "vue"

import { appOpenAPI } from "@/utils/appOpenApi"
import { onHostReady, takePendingOpens } from "@/utils/hostBridge"

/** Page event the desktop shell sends when it is handed a file or folder while running. */
export const OPEN_EVENT = "kt:desktop-open"
/** Window events this module raises for the parts of the app that act on an open. */
export const OPEN_HISTORY_EVENT = "kt:open-saved-session-history"
export const OPEN_WORKSPACE_EVENT = "kt:open-workspace"

/**
 * Act on one thing the shell was asked to open: a session file becomes a saved
 * session whose history opens, a folder opens the new-session dialog in it.
 * Resolves ``true`` when something was opened.
 */
export async function openDesktopTarget({ kind, path }, { onError } = {}) {
  if (kind === "workspace") {
    window.dispatchEvent(new CustomEvent(OPEN_WORKSPACE_EVENT, { detail: { path } }))
    return true
  }
  if (kind !== "session") return false
  try {
    const sessionName = await appOpenAPI.openSession(path)
    window.dispatchEvent(new CustomEvent(OPEN_HISTORY_EVENT, { detail: { sessionName } }))
    return true
  } catch (error) {
    onError?.(error, path)
    return false
  }
}

/** Open what the shell was launched with and anything it hands over later. */
export function useDesktopOpen({ onError } = {}) {
  let stopHostReady = () => {}

  async function drain() {
    for (const target of await takePendingOpens()) await openDesktopTarget(target, { onError })
  }

  function onEvent() {
    void drain()
  }

  onMounted(() => {
    stopHostReady = onHostReady(() => void drain())
    window.addEventListener(OPEN_EVENT, onEvent)
  })
  onBeforeUnmount(() => {
    stopHostReady()
    window.removeEventListener(OPEN_EVENT, onEvent)
  })
}
