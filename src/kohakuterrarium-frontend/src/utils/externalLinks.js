import {
  applyExternalLinkRule as applyPublicExternalLinkRule,
  isExternalUrl as isPublicExternalUrl,
} from "../public/chat/externalLinks.js"

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

export function openExternal(url) {
  if (typeof window === "undefined") return
  window.open(url, "_blank", "noopener,noreferrer")
}

export function installExternalLinkGuard(root = typeof document === "undefined" ? null : document) {
  if (!root) return () => {}

  const onClick = (event) => {
    if (event.defaultPrevented) return
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
      return
    const anchor = event.target?.closest?.("a[href]")
    if (!anchor) return
    const target = anchor.getAttribute("target")
    if (target && target !== "_self") return
    const href = anchor.getAttribute("href")
    if (!isExternalUrl(href)) return
    event.preventDefault()
    openExternal(new URL(href, window.location.href).href)
  }

  root.addEventListener("click", onClick, true)
  return () => root.removeEventListener("click", onClick, true)
}
