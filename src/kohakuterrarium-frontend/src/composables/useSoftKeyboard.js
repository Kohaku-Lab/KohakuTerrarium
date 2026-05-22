/**
 * useSoftKeyboard — reactive soft-keyboard state for mobile shells.
 *
 * Two signal sources in priority order:
 *   1. ``window.KohakuBridge.addKeyboardListener`` — the Briefcase
 *      Android shell's JS bridge.  Uses Android's WindowInsets +
 *      IME-insets reporting for accurate height + show/hide
 *      timing.  Only present in the Android APK.
 *   2. ``window.visualViewport`` resize — the web (and older
 *      Android WebView) fallback that infers keyboard state from
 *      viewport-height shrinkage.
 *
 * Reactive cells:
 *   - isOpen: boolean — is the keyboard currently visible?
 *   - height: number — height in CSS pixels (0 when closed)
 *
 * Single shared instance; the chrome subscribes once and every
 * input field gets viewport adjustment automatically via CSS
 * variables the App.vue sets from this composable.
 */

import { onScopeDispose, ref } from "vue"

const hasWindow = typeof window !== "undefined"

const _isOpen = ref(false)
const _height = ref(0)

let _initialized = false
let _unsubs = []

async function _initialize() {
  if (_initialized) return
  _initialized = true
  if (!hasWindow) return

  // Soft-keyboard signal sources, in priority order:
  //   1. KohakuBridge.addKeyboardListener — Java-side ViewTreeObserver
  //      with proper IME-insets reporting (Android 11+ has
  //      WindowInsets.Type.ime() which is the only reliable signal).
  //   2. ``window.visualViewport`` resize — the web fallback that
  //      also works on older Android WebViews.
  // No Capacitor SDK in tree — Android shell is Briefcase +
  // native WebView, bridge installed via ``addJavascriptInterface``.
  let bridgeAttached = false
  const bridge = typeof window !== "undefined" ? window.KohakuBridge : null
  if (bridge && typeof bridge.addKeyboardListener === "function") {
    try {
      const unsub = bridge.addKeyboardListener((open, height) => {
        _isOpen.value = Boolean(open)
        _height.value = Number(height) || 0
      })
      _unsubs.push(typeof unsub === "function" ? unsub : () => {})
      bridgeAttached = true
    } catch (_err) {
      // Bridge present but errored — fall back to visualViewport.
    }
  }

  if (!bridgeAttached && window.visualViewport) {
    // Web fallback — visualViewport shrinks when an OSK opens.
    const baselineHeight = window.visualViewport.height
    const handler = () => {
      const vh = window.visualViewport.height
      const delta = baselineHeight - vh
      if (delta > 100) {
        _isOpen.value = true
        _height.value = delta
      } else {
        _isOpen.value = false
        _height.value = 0
      }
    }
    window.visualViewport.addEventListener("resize", handler)
    _unsubs.push(() => window.visualViewport.removeEventListener("resize", handler))
  }
}

export function useSoftKeyboard() {
  _initialize()
  onScopeDispose(() => {
    // Singleton — don't tear down on per-consumer dispose.
  })
  return {
    isOpen: _isOpen,
    height: _height,
  }
}

export function _resetSoftKeyboardForTests() {
  _initialized = false
  _isOpen.value = false
  _height.value = 0
  for (const u of _unsubs) {
    try {
      u()
    } catch (_err) {
      /* best-effort */
    }
  }
  _unsubs = []
}
