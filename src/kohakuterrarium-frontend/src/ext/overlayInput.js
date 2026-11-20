import { computed, onMounted, onUnmounted, shallowRef } from "vue"

import { setOverlayHitRegions } from "@/utils/hostBridge"

export const POINTER_EVENT = "kt:overlay-pointer"
export const desktopPointer = shallowRef(null)
const input = { captured: false, dragging: false, pointer: false }
let report = () => {}

/** Pointer coordinates in the document viewport, including points outside a native window. */
export function useOverlayPointer(mode) {
  if (mode === "window") return computed(() => desktopPointer.value)
  const point = shallowRef(null)
  const move = (event) => {
    point.value =
      event.pointerType === "mouse"
        ? { valid: true, pointerType: "mouse", x: event.clientX, y: event.clientY }
        : null
  }
  const clear = () => {
    point.value = null
  }
  const visibility = () => {
    if (document.hidden) clear()
  }
  onMounted(() => {
    window.addEventListener("pointermove", move, { passive: true })
    window.addEventListener("blur", clear)
    document.documentElement.addEventListener("pointerleave", clear)
    document.addEventListener("visibilitychange", visibility)
  })
  onUnmounted(() => {
    window.removeEventListener("pointermove", move)
    window.removeEventListener("blur", clear)
    document.documentElement.removeEventListener("pointerleave", clear)
    document.removeEventListener("visibilitychange", visibility)
  })
  return computed(() => point.value)
}

/** Visible overlay elements that accept OS pointer input. */
export function overlayHitRegions(root = document) {
  return [...root.querySelectorAll(".kt-overlay-hit")]
    .filter((element) => {
      for (let current = element; current; current = current.parentElement) {
        const style = getComputedStyle(current)
        if (
          current.hidden ||
          style.display === "none" ||
          style.visibility === "hidden" ||
          style.opacity === "0"
        )
          return false
      }
      return true
    })
    .map((element) => {
      const r = element.getBoundingClientRect()
      return [r.left, r.top, r.width, r.height]
    })
    .filter((r) => r[2] > 0 && r[3] > 0)
}

export function beginOverlayMove() {
  if (!input.captured) return false
  input.dragging = true
  report()
  return true
}

/** Native cursor delivery and a leased capture until pointer release/cancellation. */
export function installOverlayInput(through, pointerEnabled = () => false) {
  let disposed = false
  let busy = false
  let dirty = false
  let last = ""
  let lastPointer = 0
  let capturedTarget = null
  let press = null
  let reported = false
  let explicitRegions = null
  const received = (event) => {
    lastPointer = performance.now()
    desktopPointer.value = event.detail?.valid
      ? {
          ...event.detail,
          captured: Boolean(
            input.captured && event.detail.captured && event.detail.captureId === press?.id,
          ),
        }
      : null
  }
  async function flush() {
    dirty = true
    if (busy) return
    busy = true
    try {
      while (dirty) {
        dirty = false
        const enabled = !disposed && through()
        if (!enabled) {
          capturedTarget = null
          press = null
          Object.assign(input, { captured: false, dragging: false, pointer: false })
          desktopPointer.value = null
          if (!reported) continue
        }
        const regions = enabled ? (explicitRegions ?? overlayHitRegions()) : []
        input.pointer = enabled && pointerEnabled() && !document.hidden
        const state = enabled
          ? { ...input, press, width: window.innerWidth, height: window.innerHeight }
          : { ...input, enabled: false }
        const encoded = JSON.stringify([regions, state])
        if (last === encoded && !input.captured) continue
        if (enabled) reported = true
        if (await setOverlayHitRegions(regions, state)) {
          last = encoded
          reported = enabled
        }
      }
    } finally {
      busy = false
    }
  }
  const regionsChanged = ({ detail }) => {
    if (
      detail === null ||
      (Array.isArray(detail) &&
        detail.length <= 16 &&
        detail.every(
          (r) =>
            Array.isArray(r) && r.length === 4 && r.every(Number.isFinite) && r[2] > 0 && r[3] > 0,
        ))
    ) {
      explicitRegions = detail
      void flush()
    }
  }
  const release = () => {
    capturedTarget = null
    press = null
    input.captured = false
    input.dragging = false
    void flush()
  }
  const down = (event) => {
    if (
      !through() ||
      event.button !== 0 ||
      event.isPrimary === false ||
      !event.target?.closest?.(".kt-overlay-hit")
    )
      return
    capturedTarget = event.target
    press =
      Number.isFinite(event.clientX) && Number.isFinite(event.clientY)
        ? { id: crypto.randomUUID(), x: event.clientX, y: event.clientY }
        : null
    input.captured = true
    input.dragging = false
    void flush()
  }
  const visibility = () => {
    if (document.hidden) {
      desktopPointer.value = null
      release()
    } else void flush()
  }
  const tick = () => {
    if (capturedTarget && !capturedTarget.isConnected) release()
    if (desktopPointer.value && performance.now() - lastPointer > 1000) desktopPointer.value = null
    void flush()
  }
  report = () => {
    void flush()
  }
  window.addEventListener("kt-overlay-regions", regionsChanged)
  window.addEventListener(POINTER_EVENT, received)
  document.addEventListener("pointerdown", down, true)
  document.addEventListener("pointerup", release, true)
  document.addEventListener("pointercancel", release, true)
  document.addEventListener("lostpointercapture", release, true)
  window.addEventListener("blur", release)
  document.addEventListener("visibilitychange", visibility)
  const observer = new MutationObserver(tick)
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["class", "hidden", "style"],
  })
  const timer = setInterval(tick, 200)
  tick()
  return () => {
    disposed = true
    clearInterval(timer)
    observer.disconnect()
    window.removeEventListener("kt-overlay-regions", regionsChanged)
    window.removeEventListener(POINTER_EVENT, received)
    document.removeEventListener("pointerdown", down, true)
    document.removeEventListener("pointerup", release, true)
    document.removeEventListener("pointercancel", release, true)
    document.removeEventListener("lostpointercapture", release, true)
    window.removeEventListener("blur", release)
    document.removeEventListener("visibilitychange", visibility)
    Object.assign(input, { captured: false, dragging: false, pointer: false })
    desktopPointer.value = null
    report = () => {}
    void flush()
  }
}
