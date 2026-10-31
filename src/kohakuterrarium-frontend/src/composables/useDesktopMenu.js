import { onBeforeUnmount, onMounted } from "vue"

import { popOutFocusedSession } from "@/composables/useDesktopPopout"
import { OPEN_WORKSPACE_EVENT } from "@/composables/useDesktopOpen"
import { useTabsStore } from "@/stores/tabs"
import { firePaletteOpen } from "@/utils/layoutEvents"

/** Page event the desktop shell sends when an application menu item is chosen. */
export const MENU_EVENT = "kt:desktop-menu"

const ACTIONS = {
  settings: () => useTabsStore().openTab({ kind: "settings", id: "settings" }),
  palette: () => firePaletteOpen(),
  "new-session": () =>
    window.dispatchEvent(new CustomEvent(OPEN_WORKSPACE_EVENT, { detail: { path: "" } })),
  popout: () => popOutFocusedSession(),
}

/** Run a menu action by name; resolves ``false`` for a name the app does not know. */
export async function runMenuAction(action) {
  const run = ACTIONS[action]
  if (!run) return false
  await run()
  return true
}

export function useDesktopMenu() {
  function onEvent(event) {
    void runMenuAction(event.detail?.action)
  }
  onMounted(() => window.addEventListener(MENU_EVENT, onEvent))
  onBeforeUnmount(() => window.removeEventListener(MENU_EVENT, onEvent))
}
