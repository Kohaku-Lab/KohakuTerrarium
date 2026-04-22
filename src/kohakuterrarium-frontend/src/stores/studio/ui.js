import { defineStore } from "pinia"
import { ref } from "vue"

import { pointInRect, pointInTriangle, safeTriangleBase } from "@/utils/studio/safeTriangle"

/**
 * Studio UI state — hover target (safe-triangle preserved), per-frame
 * column widths, and other transient UI knobs.
 *
 * The hover target uses a "safe triangle" preservation strategy:
 * when a source (pool item / slot row) reports the mouse leaving, we
 * don't clear immediately. Instead we watch the mouse for a short
 * grace period — as long as it stays inside a triangle that points
 * at the detail panel (or enters the panel itself), the hover state
 * is preserved so the user can actually click buttons in the detail
 * panel.
 *
 * Components just call setHoverTarget() / clearHoverTarget() — the
 * safe-triangle machinery is invisible to them. DetailPanel.vue
 * registers itself as the destination via registerHoverDestination().
 */

const COL_KEY = "studio:column-widths"

// Grace period after the mouse stops moving inside the wedge — if
// it doesn't make progress toward the destination, we clear anyway.
const STILL_TIMEOUT_MS = 600

function loadCols() {
  try {
    const raw = localStorage.getItem(COL_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

function saveCols(map) {
  try {
    localStorage.setItem(COL_KEY, JSON.stringify(map))
  } catch {
    /* noop */
  }
}

export const useStudioUiStore = defineStore("studio-ui", () => {
  const columnWidths = ref(loadCols())
  const hoverTarget = ref(null)
  // DOM element registered as the "destination" for safe-triangle
  // preservation — DetailPanel registers itself on mount.
  const hoverDestinationEl = ref(null)

  // ---- safe-triangle internals ---------------------------------
  let lastMouse = { x: 0, y: 0 }
  let mouseTracker = null
  let originAtLeave = null
  let moveListener = null
  let destLeaveListener = null
  let stillTimer = null

  function attachMouseTracker() {
    if (mouseTracker || typeof document === "undefined") return
    mouseTracker = (e) => {
      lastMouse = { x: e.clientX, y: e.clientY }
    }
    document.addEventListener("mousemove", mouseTracker, { passive: true })
  }

  function detachMouseTracker() {
    if (mouseTracker && typeof document !== "undefined") {
      document.removeEventListener("mousemove", mouseTracker)
    }
    mouseTracker = null
  }

  function detachMoveListener() {
    if (moveListener && typeof document !== "undefined") {
      document.removeEventListener("mousemove", moveListener)
    }
    moveListener = null
    if (stillTimer) {
      clearTimeout(stillTimer)
      stillTimer = null
    }
  }

  function detachDestLeaveListener() {
    if (destLeaveListener && typeof document !== "undefined") {
      document.removeEventListener("mousemove", destLeaveListener)
    }
    destLeaveListener = null
  }

  function detachAll() {
    detachMoveListener()
    detachDestLeaveListener()
  }

  function doClear() {
    detachAll()
    hoverTarget.value = null
    detachMouseTracker()
  }

  function resetStillTimer() {
    if (stillTimer) clearTimeout(stillTimer)
    stillTimer = setTimeout(() => {
      doClear()
    }, STILL_TIMEOUT_MS)
  }

  function attachDestLeaveListener() {
    if (typeof document === "undefined") return
    destLeaveListener = (e) => {
      const destNow = hoverDestinationEl.value
      if (!destNow) {
        doClear()
        return
      }
      const rect = destNow.getBoundingClientRect()
      if (!pointInRect({ x: e.clientX, y: e.clientY }, rect)) {
        doClear()
      }
    }
    document.addEventListener("mousemove", destLeaveListener, { passive: true })
  }

  function startSafeCheck() {
    detachAll()
    const destEl = hoverDestinationEl.value
    if (
      !destEl ||
      typeof destEl.getBoundingClientRect !== "function" ||
      typeof document === "undefined"
    ) {
      doClear()
      return
    }
    originAtLeave = { ...lastMouse }

    moveListener = (e) => {
      const p = { x: e.clientX, y: e.clientY }
      lastMouse = p

      const destNow = hoverDestinationEl.value
      if (!destNow) {
        doClear()
        return
      }
      const rect = destNow.getBoundingClientRect()

      // Reached destination → lock hover until cursor leaves it.
      if (pointInRect(p, rect)) {
        detachMoveListener()
        attachDestLeaveListener()
        return
      }

      const [topBase, bottomBase] = safeTriangleBase(rect)
      if (!pointInTriangle(p, originAtLeave, topBase, bottomBase)) {
        doClear()
      } else {
        resetStillTimer()
      }
    }
    document.addEventListener("mousemove", moveListener, { passive: true })
    resetStillTimer()
  }

  // ---- public API ---------------------------------------------

  function setHoverTarget(target) {
    detachAll()
    hoverTarget.value = target
    attachMouseTracker()
  }

  /**
   * Arm a safe-triangle check. If the mouse reaches the registered
   * destination before exiting the wedge, hover is preserved.
   * Otherwise, hover clears when the cursor exits the triangle or
   * stops moving for longer than STILL_TIMEOUT_MS.
   *
   * Pass `{ immediate: true }` to bypass the safe triangle and
   * clear synchronously (useful for explicit dismissals).
   */
  function clearHoverTarget(options = {}) {
    const { immediate = false } = options
    if (immediate) {
      doClear()
      return
    }
    if (!hoverDestinationEl.value) {
      doClear()
      return
    }
    startSafeCheck()
  }

  function registerHoverDestination(el) {
    hoverDestinationEl.value = el || null
  }

  function getColumns(frameId, defaults = { left: 220, right: 320 }) {
    return columnWidths.value[frameId] ?? { ...defaults }
  }

  function setColumns(frameId, widths) {
    columnWidths.value = { ...columnWidths.value, [frameId]: widths }
    saveCols(columnWidths.value)
  }

  return {
    columnWidths,
    hoverTarget,
    getColumns,
    setColumns,
    setHoverTarget,
    clearHoverTarget,
    registerHoverDestination,
  }
})
