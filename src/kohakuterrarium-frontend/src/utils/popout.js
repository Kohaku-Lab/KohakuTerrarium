import { openPopoutWindow, popoutWindowsSupported } from "@/utils/hostBridge"
import { makeWindowLabel } from "@/utils/windowLabel"

export const POPOUT_PARAM = "kt_popout"
export const POPOUT_LABEL_PREFIX = "popout-"

/** The session a window was opened for, or ``null`` in the normal app. */
export function popoutScopeFromSearch(search) {
  return new URLSearchParams(search || "").get(POPOUT_PARAM) || null
}

/** Native window label for a session: one window per session. */
export function popoutLabel(scope) {
  return makeWindowLabel(POPOUT_LABEL_PREFIX, scope)
}

/** Show ``scope`` in a window of its own. Resolves ``false`` where the shell cannot. */
export async function popOutSession(scope, title = "") {
  if (!scope || !popoutWindowsSupported()) return false
  return openPopoutWindow({ label: popoutLabel(scope), scope, title: title || "KohakuTerrarium" })
}
