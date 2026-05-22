/**
 * useNetworkStatus — reactive connection state for the chrome.
 *
 * Three signals merge into one reactive `status` value:
 *   - Browser `navigator.onLine` (cheap, present on all platforms)
 *   - Android WebView `window.KohakuBridge.addNetworkListener`
 *     (when the Briefcase Android shell injects the bridge) —
 *     more reliable than `navigator.onLine` on Android, which can
 *     stay stale until the next request actually fails
 *   - The framework's own ping (a `/healthz` request — succeeds
 *     when the host is alive even if the network thinks "offline")
 *
 * Reflected as `status.value`:
 *   - "online"   — network up + host reachable
 *   - "host-only" — `navigator.onLine` says offline but our
 *     `/healthz` succeeded (== local host on phone via loopback)
 *   - "offline"  — neither
 *
 * Module-level singleton like `useDensity`; one event listener set
 * across the whole app.
 */

import { computed, onScopeDispose, ref } from "vue"

const hasWindow = typeof window !== "undefined"

const _navigatorOnline = ref(hasWindow ? Boolean(navigator.onLine) : true)
const _hostReachable = ref(true)
const _lastTransitionAt = ref(0)

let _initialized = false
let _bridgeUnsub = null

const _status = computed(() => {
  if (_navigatorOnline.value && _hostReachable.value) return "online"
  if (!_navigatorOnline.value && _hostReachable.value) return "host-only"
  return "offline"
})

function _markTransition() {
  _lastTransitionAt.value = Date.now()
}

async function _initialize() {
  if (_initialized) return
  _initialized = true
  if (!hasWindow) return

  window.addEventListener("online", () => {
    _navigatorOnline.value = true
    _markTransition()
  })
  window.addEventListener("offline", () => {
    _navigatorOnline.value = false
    _markTransition()
  })

  // Android-native augmentation: the WebView's Java bridge can
  // expose more granular network-change signals than browser
  // ``navigator.onLine`` (which on Android WebView is sometimes
  // stale until the next request).  Probed via the
  // ``window.KohakuBridge`` namespace — when the bridge exposes
  // ``addNetworkListener``, we use it; otherwise we rely on
  // navigator events alone (web build path).
  //
  // The Briefcase Android MainActivity registers KohakuBridge.
  // The Briefcase Android MainActivity registers KohakuBridge
  // via ``WebView.addJavascriptInterface``.  No Capacitor SDK
  // is in tree; this APK is a Briefcase + native WebView shell.
  const bridge = typeof window !== "undefined" ? window.KohakuBridge : null
  if (bridge && typeof bridge.addNetworkListener === "function") {
    try {
      const initial = bridge.getNetworkStatus
        ? Boolean(bridge.getNetworkStatus())
        : true
      _navigatorOnline.value = initial
      const unsub = bridge.addNetworkListener((connected) => {
        _navigatorOnline.value = Boolean(connected)
        _markTransition()
      })
      _bridgeUnsub =
        typeof unsub === "function" ? unsub : () => {}
    } catch (_err) {
      // Bridge present but errored — fall back to navigator events.
    }
  }
}

/**
 * Probe the host with a low-overhead request.  Caller drives it
 * (every 30s in the connection-status chrome).  Updates
 * `_hostReachable` reactively.
 *
 * Hits ``/healthz`` directly (NOT under ``/api``) — that's the
 * framework's documented unauthenticated liveness endpoint, the
 * one the container-orchestrator probes also use.  Earlier
 * versions of this composable used ``/api/version`` which doesn't
 * exist; the host appeared unreachable to mobile clients on every
 * cycle.  Audit fix.
 */
export async function pingHost(baseUrl = "") {
  if (!hasWindow) return
  const url = baseUrl ? `${baseUrl.replace(/\/+$/, "")}/healthz` : "/healthz"
  try {
    const resp = await fetch(url, {
      method: "GET",
      cache: "no-store",
      // Short timeout — anything > 5s is "offline" from the user's
      // perspective.  AbortSignal.timeout requires a recent browser
      // but is OK for the WebView baseline (Chromium 100+).
      signal: AbortSignal.timeout(5000),
    })
    const ok = resp.ok
    if (ok !== _hostReachable.value) {
      _hostReachable.value = ok
      _markTransition()
    }
  } catch (_err) {
    if (_hostReachable.value) {
      _hostReachable.value = false
      _markTransition()
    }
  }
}

export function useNetworkStatus() {
  _initialize()
  onScopeDispose(() => {
    // Don't disconnect the bridge listener — it's shared across
    // every consumer.  The listener lives for the app lifetime.
  })
  return {
    status: _status,
    navigatorOnline: _navigatorOnline,
    hostReachable: _hostReachable,
    lastTransitionAt: _lastTransitionAt,
    pingHost,
  }
}

// Test-only: reset module state so unit tests get a clean slate.
export function _resetNetworkStatusForTests() {
  _initialized = false
  _navigatorOnline.value = hasWindow ? Boolean(navigator.onLine) : true
  _hostReachable.value = true
  _lastTransitionAt.value = 0
  if (_bridgeUnsub) {
    _bridgeUnsub()
    _bridgeUnsub = null
  }
}
