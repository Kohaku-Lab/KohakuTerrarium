import { openPopoutWindow, popoutWindowsSupported } from "@/utils/hostBridge"
import { makeWindowLabel } from "@/utils/windowLabel"

export const POPOUT_PARAM = "kt_popout"
export const POPOUT_LABEL_PREFIX = "popout-"
/** Window event the shell raises in the main window when a session window hands its session back. */
export const POPOUT_RETURN_EVENT = "kt:desktop-popout-return"

/** The session a window was opened for, or ``null`` in the normal app. */
export function popoutScopeFromSearch(search) {
  return new URLSearchParams(search || "").get(POPOUT_PARAM) || null
}

/** Native window label for a session: one window per session. */
export function popoutLabel(scope) {
  return makeWindowLabel(POPOUT_LABEL_PREFIX, scope)
}

/**
 * Show ``scope`` in a window of its own, at ``position`` (`{ x, y }` screen pixels)
 * when given. Resolves ``false`` where the shell cannot.
 */
export async function popOutSession(scope, title = "", position = null) {
  if (!scope || !popoutWindowsSupported()) return false
  const spec = { label: popoutLabel(scope), scope, title: title || "KohakuTerrarium" }
  if (Number.isFinite(position?.x) && Number.isFinite(position?.y)) {
    spec.x = Math.round(position.x)
    spec.y = Math.round(position.y)
  }
  return openPopoutWindow(spec)
}
