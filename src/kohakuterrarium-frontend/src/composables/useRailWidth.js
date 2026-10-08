/**
 * Persistent, resizable, collapsible rail for the macro shell.
 *
 * The rail's right edge gets a drag handle (per the LayoutNode pattern
 * used elsewhere in the app). Width is clamped to [MIN, MAX] and
 * persisted under ``kt.rail.width``; the collapsed state (a
 * COLLAPSED_RAIL_WIDTH icon strip) under ``kt.rail.collapsed``.
 */

import { onBeforeUnmount, onMounted, ref } from "vue"

const KEY = "kt.rail.width"
const COLLAPSED_KEY = "kt.rail.collapsed"
export const MIN_RAIL_WIDTH = 180
export const MAX_RAIL_WIDTH = 480
export const DEFAULT_RAIL_WIDTH = 240
export const COLLAPSED_RAIL_WIDTH = 48

function loadCollapsed() {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1"
  } catch {
    return false
  }
}

function saveCollapsed(value) {
  try {
    localStorage.setItem(COLLAPSED_KEY, value ? "1" : "0")
  } catch {
    /* swallow */
  }
}

function loadWidth() {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return DEFAULT_RAIL_WIDTH
    const n = parseInt(raw, 10)
    if (Number.isNaN(n)) return DEFAULT_RAIL_WIDTH
    return Math.max(MIN_RAIL_WIDTH, Math.min(MAX_RAIL_WIDTH, n))
  } catch {
    return DEFAULT_RAIL_WIDTH
  }
}

function saveWidth(w) {
  try {
    localStorage.setItem(KEY, String(w))
  } catch {
    /* swallow */
  }
}

export function useRailWidth() {
  const width = ref(loadWidth())
  const collapsed = ref(loadCollapsed())
  const dragging = ref(false)
  let pendingPersist = null

  function clamp(w) {
    return Math.max(MIN_RAIL_WIDTH, Math.min(MAX_RAIL_WIDTH, Math.round(w)))
  }

  function setWidth(w) {
    width.value = clamp(w)
    if (pendingPersist) clearTimeout(pendingPersist)
    pendingPersist = setTimeout(() => saveWidth(width.value), 200)
  }

  function startDrag(e) {
    if (typeof window === "undefined") return
    e.preventDefault()
    dragging.value = true
    const startX = e.clientX
    const startWidth = width.value

    function onMove(ev) {
      const delta = ev.clientX - startX
      setWidth(startWidth + delta)
    }
    function onUp() {
      dragging.value = false
      document.removeEventListener("pointermove", onMove)
      document.removeEventListener("pointerup", onUp)
      document.removeEventListener("pointercancel", onUp)
      saveWidth(width.value)
    }

    document.addEventListener("pointermove", onMove)
    document.addEventListener("pointerup", onUp)
    document.addEventListener("pointercancel", onUp)
  }

  function resetWidth() {
    setWidth(DEFAULT_RAIL_WIDTH)
  }

  function toggleCollapsed() {
    collapsed.value = !collapsed.value
    saveCollapsed(collapsed.value)
  }

  // Cross-tab sync — another window writing the pref updates this one.
  function onStorage(e) {
    if (e.key === KEY) width.value = loadWidth()
    if (e.key === COLLAPSED_KEY) collapsed.value = loadCollapsed()
  }

  onMounted(() => {
    if (typeof window !== "undefined") window.addEventListener("storage", onStorage)
  })
  onBeforeUnmount(() => {
    if (typeof window !== "undefined") window.removeEventListener("storage", onStorage)
    if (pendingPersist) clearTimeout(pendingPersist)
  })

  return { width, collapsed, dragging, startDrag, resetWidth, toggleCollapsed }
}
