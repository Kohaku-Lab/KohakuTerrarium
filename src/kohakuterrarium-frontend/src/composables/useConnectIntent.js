/**
 * useConnectIntent — reactive consumer of ``ktconnect://`` URIs
 * arriving via Android deep-link.
 *
 * The Android ``MainActivity`` handles ``Intent.ACTION_VIEW`` on
 * the ``ktconnect`` scheme and injects:
 *
 *   - ``window.__KT_PENDING_CONNECT_URI`` = "ktconnect://..."
 *
 * then dispatches a ``kt-connect-uri`` event.  This composable
 * subscribes once (module-level singleton) and exposes the
 * parsed URI as a reactive ref so the host-picker UI can react
 * without polling.
 *
 * On web (no Android deep-link), the composable is inert — the
 * event never fires, the ref stays null.
 *
 * Consumer pattern:
 *
 *     const { pendingUri, consume } = useConnectIntent()
 *     watch(pendingUri, async (uri) => {
 *       if (!uri) return
 *       const { url, token } = parseKtConnect(uri)
 *       // pre-populate Add-Host form …
 *       consume()  // clear the ref so we don't re-prompt
 *     })
 */

import { onScopeDispose, ref } from "vue"

const hasWindow = typeof window !== "undefined"

const _pendingUri = ref(null)
let _initialized = false

function _initialize() {
  if (_initialized) return
  _initialized = true
  if (!hasWindow) return

  // Pick up a URI that arrived BEFORE this composable was first
  // imported (the WebView can fire the event before any Vue
  // component mounts; the global persists).
  if (typeof window.__KT_PENDING_CONNECT_URI === "string") {
    _pendingUri.value = window.__KT_PENDING_CONNECT_URI
  }

  window.addEventListener("kt-connect-uri", () => {
    const uri = window.__KT_PENDING_CONNECT_URI
    if (typeof uri === "string" && uri.startsWith("ktconnect://")) {
      _pendingUri.value = uri
    }
  })
}

export function useConnectIntent() {
  _initialize()
  function consume() {
    _pendingUri.value = null
    if (hasWindow) {
      try {
        delete window.__KT_PENDING_CONNECT_URI
      } catch (_err) {
        window.__KT_PENDING_CONNECT_URI = undefined
      }
      // Acknowledge to the Java side that JS has consumed the
      // URI; without this Java keeps replaying via
      // ``window.dispatchEvent(new Event('kt-connect-uri'))``
      // every 1.5s, which would re-prompt the user for the same
      // host indefinitely.  Audit fix.
      const bridge = window.KohakuBridge
      if (bridge && typeof bridge.ackConnectUri === "function") {
        try {
          bridge.ackConnectUri()
        } catch (_err) {
          // Best-effort — if the bridge throws, the worst case is
          // a few extra dispatches before Vue ignores them
          // (pendingUri is already null).
        }
      }
    }
  }
  onScopeDispose(() => {
    // Singleton — keep the listener alive across component
    // remounts.
  })
  return {
    pendingUri: _pendingUri,
    consume,
  }
}

export function _resetConnectIntentForTests() {
  _initialized = false
  _pendingUri.value = null
  if (hasWindow) {
    try {
      delete window.__KT_PENDING_CONNECT_URI
    } catch (_err) {
      window.__KT_PENDING_CONNECT_URI = undefined
    }
  }
}
