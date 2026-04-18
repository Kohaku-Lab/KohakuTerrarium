/**
 * Resolve a relative `/ws/...` path to an absolute WebSocket URL.
 *
 * In dev the Vite proxy (see vite.config.js `server.proxy`) rewrites
 * `/ws/*` onto the backend, so going through `window.location.host`
 * Just Works. In prod the backend serves both the static bundle and
 * the WS endpoints off the same origin, so the same code path applies.
 *
 * We intentionally don't try to detect "dev mode by port number" any
 * more — it was fragile and disagreed with the proxy config. Any
 * legacy caller can still pass an absolute URL and this will pass it
 * through unchanged.
 */
export function wsUrl(path) {
  if (typeof window === "undefined") return path
  if (/^wss?:\/\//.test(path)) return path
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:"
  return `${scheme}//${window.location.host}${path}`
}
