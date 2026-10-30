import {
  applyExternalLinkRule as applyPublicExternalLinkRule,
  isExternalUrl as isPublicExternalUrl,
} from "../public/chat/externalLinks.js"
import { hasDesktopHost, openExternalViaHost } from "./hostBridge.js"

function currentOrigin() {
  if (typeof window === "undefined" || !window.location) return null
  return window.location.origin
}

export function isExternalUrl(href, origin = currentOrigin()) {
  return isPublicExternalUrl(href, origin)
}

export function applyExternalLinkRule(md) {
  return applyPublicExternalLinkRule(md, currentOrigin())
}

/**
 * Open `url` outside the app. A native shell opens the system browser itself;
 * `window.open` is only safe in a real browser, where it makes a new tab.
 */
export function openExternal(url) {
  if (typeof window === "undefined") return
  if (hasDesktopHost()) {
    void openExternalViaHost(url)
    return
  }
  window.open(url, "_blank", "noopener,noreferrer")
}

/**
 * Keep external navigation out of the app window. In a native shell every
 * external anchor, including `target="_blank"`, and every external
 * `window.open` goes to the system browser, because an embedded webview has no
 * back button and may load a new-window request into the app's own view.
 */
export function installExternalLinkGuard(root = typeof document === "undefined" ? null : document) {
  if (!root) return () => {}

  const onClick = (event) => {
    if (event.defaultPrevented) return
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
      return
    const anchor = event.target?.closest?.("a[href]")
    if (!anchor) return
    const target = anchor.getAttribute("target")
    if (target && target !== "_self" && !hasDesktopHost()) return
    const href = anchor.getAttribute("href")
    if (!isExternalUrl(href)) return
    event.preventDefault()
    openExternal(new URL(href, window.location.href).href)
  }

  const nativeOpen = window.open
  window.open = function open(url, ...rest) {
    if (hasDesktopHost() && url && isExternalUrl(String(url))) {
      openExternal(new URL(String(url), window.location.href).href)
      return null
    }
    return nativeOpen.call(window, url, ...rest)
  }

  root.addEventListener("click", onClick, true)
  return () => {
    root.removeEventListener("click", onClick, true)
    window.open = nativeOpen
  }
}
