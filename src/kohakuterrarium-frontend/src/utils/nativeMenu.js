import { hostKind, hostSupports, showContextMenu } from "@/utils/hostBridge"

/** Page event the shell raises with the chosen row's id in `detail.id`. */
export const CHOICE_EVENT = "kt:desktop-context-choice"

let handler = null
let listening = false

/** Whether the shell can draw context menus with the operating system. */
export function nativeMenuSupported() {
  return hostKind() === "tauri" && hostSupports("show_context_menu")
}

function onChoice(event) {
  const choose = handler
  handler = null
  choose?.(event.detail?.id)
}

/**
 * Show `items` (`{ id, label, enabled? }` or `{ separator: true }`) at the pointer and call
 * `onChoose(id)` if a row is chosen; a dismissed menu calls nothing. Resolves ``false`` when the
 * shell had nothing to show, so the caller can fall back to its own menu.
 */
export async function showNativeMenu(items, onChoose) {
  if (!listening) {
    window.addEventListener(CHOICE_EVENT, onChoice)
    listening = true
  }
  handler = onChoose
  const shown = await showContextMenu(items)
  if (!shown) handler = null
  return shown
}
