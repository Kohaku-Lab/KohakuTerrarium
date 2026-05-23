/**
 * Studio-local WebSocket URL helpers.
 *
 * Mirrors the runner's utils/wsUrl.js pattern AND respects the
 * active host — studio's WS paths (``/ws/studio/...``) target the
 * same host as the studio REST calls.  When a remote host is
 * active, the token query-param fallback applies (WebSocket has no
 * Authorization header).
 */

import { useHostsStore } from "@/stores/hosts"

/** Build a ws:// or wss:// URL for a relative studio path. */
export function studioWsUrl(path) {
  const clean = path.startsWith("/") ? path : `/${path}`
  let active = null
  try {
    active = useHostsStore().activeHost
  } catch (_err) {
    active = null
  }
  if (active) {
    const scheme = active.url.startsWith("https://") ? "wss:" : "ws:"
    const authority = active.url.replace(/^https?:\/\//, "").replace(/\/+$/, "")
    const joiner = clean.includes("?") ? "&" : "?"
    const tokenSuffix = active.token ? `${joiner}token=${encodeURIComponent(active.token)}` : ""
    return `${scheme}//${authority}${clean}${tokenSuffix}`
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
  const host = window.location.host
  return `${proto}//${host}${clean}`
}

/** Test-drive WS (Phase 6). */
export function testdriveWsUrl(sessionId) {
  return studioWsUrl(`/ws/studio/testdrive/${encodeURIComponent(sessionId)}`)
}
