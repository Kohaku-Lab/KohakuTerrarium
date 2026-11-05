import { overlayPlacement, getOverlayBounds } from "@/utils/hostBridge"

export function trackWindowPlacement(key) {
  const storageKey = `kt.overlay.window.${key}`
  const resetKey = `kt.overlay.reset.${key}`
  let disposed = false
  const reset = () => overlayPlacement("reset")
  const restore = async () => {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey))
      if (!disposed && saved?.version === 1) await overlayPlacement("restore", saved)
    } catch {
      /* Optional placement persistence. */
    }
  }
  const restored = restore()
  let previous = "",
    polling = false
  const timer = setInterval(async () => {
    if (polling) return
    polling = true
    await restored
    if (disposed) {
      polling = false
      return
    }
    const bounds = await getOverlayBounds()
    const placement = await overlayPlacement()
    polling = false
    if (disposed || !bounds) return
    window.dispatchEvent(new CustomEvent("kt-overlay-native-bounds", { detail: bounds }))
    if (!placement) return
    const next = JSON.stringify(placement)
    if (previous !== next) {
      previous = next
      try {
        localStorage.setItem(storageKey, next)
      } catch {
        /* Optional storage. */
      }
    }
  }, 1000)
  const changed = (event) => {
    if (event.key === resetKey) reset()
  }
  window.addEventListener("storage", changed)
  return () => {
    disposed = true
    clearInterval(timer)
    window.removeEventListener("storage", changed)
  }
}
