import { desktopCapabilities, overlayPlacement, getOverlayBounds } from "@/utils/hostBridge"

// Native coordinates and persistence stay in the shell. The page only consumes
// confirmed bounds and sends reset intent; legacy shells use the in-app overlay.
export function trackWindowPlacement(key) {
  let disposed = false
  const resetKey = `kt.overlay.reset.${key}`
  let pending = Promise.resolve()
  const update = () => {
    pending = pending
      .then(async () => {
        if (!(await desktopCapabilities())?.overlayPlacementNative || disposed) return
        const token = localStorage.getItem(resetKey)
        const result = await overlayPlacement(token ? "reset" : "validate")
        if (disposed) return
        if (token && result && localStorage.getItem(resetKey) === token)
          localStorage.removeItem(resetKey)
        const bounds = await getOverlayBounds()
        if (!disposed && bounds)
          window.dispatchEvent(new CustomEvent("kt-overlay-native-bounds", { detail: bounds }))
      })
      .catch(() => {})
  }
  const reset = (event) => {
    if (event.key === resetKey && event.newValue) update()
  }
  window.addEventListener("storage", reset)
  update()
  return () => {
    disposed = true
    window.removeEventListener("storage", reset)
  }
}
