/**
 * `kt://` links handed to the page by a native shell.
 *
 * Supported: `kt://session/<name>` opens that saved session's history. Anything
 * else is ignored, so a crafted link can only reach what the app already offers.
 */

import { takeDeepLink } from "@/utils/hostBridge"

export const DEEP_LINK_EVENT = "kt:deep-link"

/** The action a `kt://` link asks for, or ``null`` when it is not understood. */
export function parseDeepLink(raw) {
  let url
  try {
    url = new URL(String(raw))
  } catch {
    return null
  }
  if (url.protocol !== "kt:") return null
  if (url.hostname === "session") {
    const name = decodeURIComponent(url.pathname.replace(/^\/+/, "")).trim()
    if (name && !name.includes("/") && !name.includes("\\")) return { kind: "session", name }
  }
  return null
}

/** Perform the action for `raw`. Returns whether it was understood. */
export function openDeepLink(raw) {
  const action = parseDeepLink(raw)
  if (!action) return false
  window.dispatchEvent(
    new CustomEvent("kt:open-saved-session-history", { detail: { sessionName: action.name } }),
  )
  return true
}

/** Listen for deep links, including one that arrived before the page loaded. */
export function installDeepLinks() {
  if (typeof window === "undefined") return () => {}
  const onLink = (event) => openDeepLink(event.detail?.url)
  window.addEventListener(DEEP_LINK_EVENT, onLink)
  void takeDeepLink().then((pending) => {
    if (pending) openDeepLink(pending)
  })
  return () => window.removeEventListener(DEEP_LINK_EVENT, onLink)
}
