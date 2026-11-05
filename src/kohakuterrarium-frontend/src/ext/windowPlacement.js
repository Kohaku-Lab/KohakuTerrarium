import { setOverlayBounds, getOverlayBounds } from "@/utils/hostBridge"

export function trackWindowPlacement(key) {
  const storageKey = `kt.overlay.window.${key}`
  const resetKey = `kt.overlay.reset.${key}`
  const reset = async () => {
    const bounds = await getOverlayBounds()
    if (!bounds) return
    const [, , w, h] = bounds
    return setOverlayBounds(
      (screen.availLeft || 0) + Math.max(0, (screen.availWidth - w) / 2),
      (screen.availTop || 0) + Math.max(0, (screen.availHeight - h) / 2),
      w,
      h,
    )
  }
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey))
    if (
      Array.isArray(saved) &&
      saved.length === 4 &&
      saved.every(Number.isFinite) &&
      saved[2] >= 48 &&
      saved[3] >= 48
    ) {
      // Restore only reachable placements on the screen reported by this window.
      const left = screen.availLeft || 0,
        top = screen.availTop || 0
      if (
        saved[0] + saved[2] > left + 48 &&
        saved[0] < left + screen.availWidth - 48 &&
        saved[1] + saved[3] > top + 48 &&
        saved[1] < top + screen.availHeight - 48
      )
        setOverlayBounds(...saved)
      else reset()
    }
  } catch {
    /* Optional placement persistence. */
  }
  let previous = "",
    disposed = false,
    polling = false
  const timer = setInterval(async () => {
    if (polling) return
    polling = true
    const bounds = await getOverlayBounds()
    polling = false
    if (disposed || !bounds) return
    window.dispatchEvent(new CustomEvent("kt-overlay-native-bounds", { detail: bounds }))
    const next = JSON.stringify(bounds)
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
