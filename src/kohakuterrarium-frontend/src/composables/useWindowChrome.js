import { onBeforeUnmount, onMounted } from "vue"

import { hostKind, hostSupports, setWindowChrome } from "@/utils/hostBridge"

/** `[r, g, b]` of a computed `rgb()`/`rgba()` colour that is mostly opaque, else ``null``. */
export function parseOpaqueColor(text) {
  const numbers = String(text).match(/\d*\.?\d+/g)
  if (!numbers || numbers.length < 3) return null
  const [r, g, b, alpha = "1"] = numbers.map(Number)
  if (alpha < 0.5 || [r, g, b].some((part) => part > 255)) return null
  return [r, g, b].map(Math.round)
}

/** The first mostly opaque background from `element` up through its parents. */
export function firstOpaqueColor(element, style = getComputedStyle) {
  for (let node = element; node; node = node.parentElement) {
    const color = parseOpaqueColor(style(node).backgroundColor)
    if (color) return color
  }
  return null
}

/** Colour the native window frame like the top edge of the page, and again when the theme changes. */
export function useWindowChrome() {
  let observer = null
  let frame = 0

  function paint() {
    frame = 0
    const edge = document.elementFromPoint?.(window.innerWidth / 2, 1) || document.body
    const color = firstOpaqueColor(edge)
    if (color) void setWindowChrome(color)
  }

  function schedule() {
    if (!frame) frame = requestAnimationFrame(paint)
  }

  onMounted(() => {
    if (hostKind() !== "tauri" || !hostSupports("set_window_chrome")) return
    observer = new MutationObserver(schedule)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    schedule()
  })
  onBeforeUnmount(() => {
    observer?.disconnect()
    if (frame) cancelAnimationFrame(frame)
  })
}
