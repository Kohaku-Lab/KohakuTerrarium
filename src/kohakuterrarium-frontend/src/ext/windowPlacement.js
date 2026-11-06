import { desktopCapabilities, overlayPlacement, getOverlayBounds } from "@/utils/hostBridge"

// Native coordinates and persistence stay in the shell. The page only consumes
// confirmed bounds and sends reset intent; legacy shells use the in-app overlay.
export function trackWindowPlacement(key) {
  let disposed = false
  const resetKey = `kt.overlay.reset.${key}`
  const reset = (event) => {
    if (event.key === resetKey) void overlayPlacement("reset")
  }
  window.addEventListener("storage", reset)
  void (async () => {
    if (!(await desktopCapabilities())?.overlayPlacementNative || disposed) return
    await overlayPlacement("validate")
    const bounds = await getOverlayBounds()
    if (!disposed && bounds)
      window.dispatchEvent(new CustomEvent("kt-overlay-native-bounds", { detail: bounds }))
  })()
  return () => {
    disposed = true
    window.removeEventListener("storage", reset)
  }
}
