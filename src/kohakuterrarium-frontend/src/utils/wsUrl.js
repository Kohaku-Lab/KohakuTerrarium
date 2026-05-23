/**
 * Resolve a relative ``/ws/...`` path to an absolute WebSocket URL.
 *
 * Three modes, decided by the hosts store + the path shape:
 *
 *   1. Absolute already (``ws://...`` / ``wss://...``) — pass
 *      through unchanged.  Lets legacy callers and the cluster-
 *      wizard's explicit ``wss://`` paths short-circuit.
 *
 *   2. **Remote mode** — the hosts store has an active host.  The
 *      WS URL targets that host's authority with the matching ws
 *      scheme (``wss://`` if the host URL is ``https://``, else
 *      ``ws://``).  If the host has a token, it's appended as
 *      ``?token=<...>`` — WebSocket has no Authorization header,
 *      so the backend ``verify_ws_host_token`` accepts the query-
 *      string fallback (see api/auth/ws_auth.py).
 *
 *   3. **Same-origin mode** (default) — derive ws scheme from
 *      ``window.location`` and append the path to
 *      ``window.location.host``.  This is the ``kt serve`` /
 *      web-build path where the frontend is served BY the host
 *      it talks to.
 *
 * The store is imported lazily-at-call-time so this helper works
 * before pinia is initialised (early bootstrap, tests).  If the
 * store call throws, we fall through to same-origin mode.
 */

import { useHostsStore } from "@/stores/hosts"

export function wsUrl(path) {
  if (typeof window === "undefined") return path
  if (/^wss?:\/\//.test(path)) return path

  let active = null
  try {
    active = useHostsStore().activeHost
  } catch (_err) {
    active = null
  }

  if (active) {
    const isHttps = active.url.startsWith("https://")
    const scheme = isHttps ? "wss:" : "ws:"
    const authority = active.url.replace(/^https?:\/\//, "").replace(/\/+$/, "")
    const joiner = path.includes("?") ? "&" : "?"
    const tokenSuffix = active.token ? `${joiner}token=${encodeURIComponent(active.token)}` : ""
    return `${scheme}//${authority}${path}${tokenSuffix}`
  }

  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:"
  return `${scheme}//${window.location.host}${path}`
}
