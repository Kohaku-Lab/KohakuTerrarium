/**
 * Studio-local WebSocket URL helpers.
 *
 * Mirrors the runner's utils/wsUrl.js pattern but never imports
 * it — studio's WS paths (`/ws/studio/...`) are a separate surface.
 * Phase 6 + Phase 7 consumers live here.
 */

/** Build a ws:// or wss:// URL for a relative studio path. */
export function studioWsUrl(path) {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
  const host = window.location.host
  const clean = path.startsWith("/") ? path : `/${path}`
  return `${proto}//${host}${clean}`
}

/** Test-drive WS (Phase 6). */
export function testdriveWsUrl(sessionId) {
  return studioWsUrl(`/ws/studio/testdrive/${encodeURIComponent(sessionId)}`)
}
