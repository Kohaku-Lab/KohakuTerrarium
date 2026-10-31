import { onBeforeUnmount, onMounted } from "vue"

import { firePaletteOpen } from "@/utils/layoutEvents"

/** Page event the desktop shell sends when the system-wide shortcut raises the window. */
export const SUMMON_EVENT = "kt:desktop-summon"

const COMPOSER_SELECTOR = ".kt-chat-composer textarea"

/** Put the cursor in the visible message box, or open the command palette when there is none. */
export function focusComposerOrPalette() {
  const box = [...document.querySelectorAll(COMPOSER_SELECTOR)].find(
    (el) => el.offsetParent !== null,
  )
  if (box) {
    box.focus()
    return "composer"
  }
  firePaletteOpen()
  return "palette"
}

export function useDesktopSummon() {
  onMounted(() => window.addEventListener(SUMMON_EVENT, focusComposerOrPalette))
  onBeforeUnmount(() => window.removeEventListener(SUMMON_EVENT, focusComposerOrPalette))
}
