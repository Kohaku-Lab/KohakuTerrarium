import {
  applyExternalLinkRule as applyPublicExternalLinkRule,
  isBackendResourceUrl as isPublicBackendResourceUrl,
  isExternalUrl as isPublicExternalUrl,
  isImageUrl,
} from "../public/chat/externalLinks.js"
import { hasDesktopHost, openExternalViaHost } from "./hostBridge.js"
import { downloadUrl, openImageLightbox } from "./imageLightbox.js"

function currentOrigin() {
  if (typeof window === "undefined" || !window.location) return null
  return window.location.origin
}

export function isExternalUrl(href, origin = currentOrigin()) {
  return isPublicExternalUrl(href, origin)
}

/** Whether `href` is a same-origin backend file or endpoint, not a page of the app. */
export function isBackendResourceUrl(href, origin = currentOrigin()) {
  return isPublicBackendResourceUrl(href, origin)
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
 * Show a backend file without leaving the app. A native shell shows an image
 * in the in-app viewer and saves any other file; the system browser is not
 * used because it lacks the shell's session cookie. A browser opens a new tab.
 */
export function openBackendResource(url) {
  if (typeof window === "undefined") return
  if (!hasDesktopHost()) {
    window.open(url, "_blank", "noopener,noreferrer")
    return
  }
  if (isImageUrl(url)) openImageLightbox(url)
  else downloadUrl(url)
}

/**
 * Keep the app window on the app. In a native shell every external anchor,
 * including `target="_blank"`, and every external `window.open` goes to the
 * system browser, because an embedded webview has no back button and may load
 * a new-window request into the app's own view. A same-origin backend file
 * link (`/api/...`) never replaces the app either: see `openBackendResource`.
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
    const leftToPlatform = target && target !== "_self" && !hasDesktopHost()
    const href = anchor.getAttribute("href")
    if (isBackendResourceUrl(href)) {
      if (anchor.hasAttribute("download") || leftToPlatform) return
      event.preventDefault()
      openBackendResource(new URL(href, window.location.href).href)
      return
    }
    if (leftToPlatform) return
    if (!isExternalUrl(href)) return
    event.preventDefault()
    openExternal(new URL(href, window.location.href).href)
  }

  const nativeOpen = window.open
  window.open = function open(url, ...rest) {
    if (hasDesktopHost() && url) {
      if (isExternalUrl(String(url))) {
        openExternal(new URL(String(url), window.location.href).href)
        return null
      }
      if (isBackendResourceUrl(String(url))) {
        openBackendResource(new URL(String(url), window.location.href).href)
        return null
      }
    }
    return nativeOpen.call(window, url, ...rest)
  }

  root.addEventListener("click", onClick, true)
  return () => {
    root.removeEventListener("click", onClick, true)
    window.open = nativeOpen
  }
}
