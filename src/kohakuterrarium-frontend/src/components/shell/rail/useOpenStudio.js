/**
 * Studio as the shell's home: the app switch moves the home tab to Studio,
 * which opens on the open workspace when there is one, else the picker.
 * Read-only use of the Studio workspace store.
 */

import { setAppMode } from "@/components/shell/rail/useAppMode"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"
import { useTabsStore } from "@/stores/tabs"

/** Root of the Studio workspace that is open now, or "". */
export function useStudioWorkspaceRoot() {
  const ws = useStudioWorkspaceStore()
  return () => (ws.isOpen && ws.root ? ws.root : "")
}

/** Go to an app: switch the mode and bring the home tab forward. */
export function useGoToApp() {
  const tabs = useTabsStore()
  return function goTo(mode) {
    setAppMode(mode)
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
  }
}
