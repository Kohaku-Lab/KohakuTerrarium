/**
 * `kt://` links handed to the page by a native shell.
 *
 * Supported: `kt://session/<name>` opens that saved session's history,
 * `kt://live/<scope>` shows a running session, `kt://new[?path=<folder>]` opens the
 * new-session dialog, and `kt://install/<name>` asks before installing that marketplace
 * package. Anything else is ignored, so a crafted link can only reach what the app
 * already offers.
 */

import { OPEN_WORKSPACE_EVENT } from "@/composables/useDesktopOpen"
import { openSessionScope } from "@/utils/attentionNavigation"
import { takeDeepLink } from "@/utils/hostBridge"

export const DEEP_LINK_EVENT = "kt:deep-link"
export const DEEP_LINK_INSTALL_EVENT = "kt:deep-link-install"

const PACKAGE_NAME = /^[a-z0-9][a-z0-9._-]{0,63}$/i
const MAX_PATH = 1024

function plainSegment(pathname) {
  let text
  try {
    text = decodeURIComponent(pathname.replace(/^\/+/, "")).trim()
  } catch {
    return ""
  }
  return text && !text.includes("/") && !text.includes("\\") ? text : ""
}

/** The action a `kt://` link asks for, or ``null`` when it is not understood. */
export function parseDeepLink(raw) {
  let url
  try {
    url = new URL(String(raw))
  } catch {
    return null
  }
  if (url.protocol !== "kt:") return null
  switch (url.hostname) {
    case "session": {
      const name = plainSegment(url.pathname)
      return name ? { kind: "session", name } : null
    }
    case "live": {
      const scope = plainSegment(url.pathname)
      return scope ? { kind: "live", scope } : null
    }
    case "new": {
      const path = (url.searchParams.get("path") || "").trim()
      return { kind: "new", path: path.length <= MAX_PATH ? path : "" }
    }
    case "install": {
      const name = plainSegment(url.pathname)
      return PACKAGE_NAME.test(name) ? { kind: "install", spec: `@${name}` } : null
    }
    default:
      return null
  }
}

/** Perform the action for `raw`. Returns whether it was understood. */
export function openDeepLink(raw) {
  const action = parseDeepLink(raw)
  if (!action) return false
  switch (action.kind) {
    case "session":
      window.dispatchEvent(
        new CustomEvent("kt:open-saved-session-history", { detail: { sessionName: action.name } }),
      )
      break
    case "live":
      openSessionScope(action.scope)
      break
    case "new":
      window.dispatchEvent(new CustomEvent(OPEN_WORKSPACE_EVENT, { detail: { path: action.path } }))
      break
    case "install":
      window.dispatchEvent(
        new CustomEvent(DEEP_LINK_INSTALL_EVENT, { detail: { spec: action.spec } }),
      )
      break
  }
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
