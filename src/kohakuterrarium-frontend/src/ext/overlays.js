/**
 * Overlay registry and per-user placement state.
 *
 * An overlay is a package component drawn in a fixed layer above the whole
 * app. ``overlays`` holds the registered ones; ``placements`` holds what the
 * user changed (visibility, position, size) and is remembered per host.
 */

import { markRaw, reactive } from "vue"

export const OVERLAY_MARGIN = 16
const MIN_SIZE = 48
const STORAGE_PREFIX = "kt.overlays."

export const overlays = reactive(new Map())
export const placements = reactive({})

/** Size of the window the overlays live in; the layer keeps it current. */
export const viewport = reactive({
  width: typeof window === "undefined" ? 1280 : window.innerWidth,
  height: typeof window === "undefined" ? 800 : window.innerHeight,
})

export function syncViewport() {
  viewport.width = window.innerWidth
  viewport.height = window.innerHeight
}

let _storageKey = ""

function readStorage(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "{}") || {}
  } catch (_err) {
    return {}
  }
}

function writeStorage() {
  if (!_storageKey) return
  try {
    localStorage.setItem(_storageKey, JSON.stringify(placements))
  } catch (_err) {
    // Storage can be blocked; placement then lasts for this page only.
  }
}

/** Load the saved placements of ``hostKey`` (replaces the in-memory set). */
export function loadPlacements(hostKey) {
  _storageKey = `${STORAGE_PREFIX}${hostKey}`
  reloadPlacements()
}

/** Re-read the current host's saved placements, e.g. after another window changed them. */
export function reloadPlacements() {
  if (!_storageKey) return
  for (const key of Object.keys(placements)) delete placements[key]
  Object.assign(placements, readStorage(_storageKey))
}

/** Whether ``storage`` event ``key`` is where the current host's placements live. */
export function isPlacementStorageKey(key) {
  return Boolean(_storageKey) && key === _storageKey
}

/** Whether the user allowed the overlay to open a native window of its own. */
export function isWindowAllowed(key) {
  return placement(key).windowAllowed === true
}

export function setWindowAllowed(key, allowed) {
  patch(key, { windowAllowed: Boolean(allowed) })
}

/** Register an overlay; ``options`` come from the manifest and may be overridden by code. */
export function registerOverlay({ key, pkg, id, label, component, options }) {
  overlays.set(key, {
    key,
    pkg,
    id,
    label: label || id,
    component: markRaw(component),
    options: { ...options },
  })
}

export function unregisterOverlays(keys) {
  for (const key of keys) overlays.delete(key)
}

export function clearOverlays() {
  overlays.clear()
}

function placement(key) {
  return placements[key] || {}
}

/** Whether the overlay is shown: the user's choice, else its ``defaultVisible``. */
export function isOverlayVisible(key) {
  const saved = placement(key).visible
  if (typeof saved === "boolean") return saved
  return overlays.get(key)?.options.defaultVisible !== false
}

function patch(key, fields) {
  placements[key] = { ...placement(key), ...fields }
  writeStorage()
}

export function showOverlay(key) {
  patch(key, { visible: true })
}

export function hideOverlay(key) {
  patch(key, { visible: false })
}

export function toggleOverlay(key) {
  patch(key, { visible: !isOverlayVisible(key) })
}

/** Keep a box inside the viewport. */
function clampBox(box, viewport) {
  const width = Math.min(Math.max(box.width, MIN_SIZE), viewport.width)
  const height = Math.min(Math.max(box.height, MIN_SIZE), viewport.height)
  return {
    width,
    height,
    left: Math.min(Math.max(box.left, 0), Math.max(viewport.width - width, 0)),
    top: Math.min(Math.max(box.top, 0), Math.max(viewport.height - height, 0)),
  }
}

/** The pixel box of an overlay: the saved position, else its anchor in ``viewport``. */
export function resolveBounds(options, saved, viewport) {
  const width = saved.width ?? options.width ?? 320
  const height = saved.height ?? options.height ?? 240
  let left
  let top
  if (Number.isFinite(saved.left) && Number.isFinite(saved.top)) {
    left = saved.left
    top = saved.top
  } else {
    const anchor = options.anchor || "bottom-right"
    const right = anchor.endsWith("right")
    const bottom = anchor.startsWith("bottom")
    const centered = anchor === "center"
    left = centered
      ? (viewport.width - width) / 2
      : right
        ? viewport.width - width - OVERLAY_MARGIN
        : OVERLAY_MARGIN
    top = centered
      ? (viewport.height - height) / 2
      : bottom
        ? viewport.height - height - OVERLAY_MARGIN
        : OVERLAY_MARGIN
  }
  return clampBox({ left, top, width, height }, viewport)
}

export function overlayBounds(key, vp = viewport) {
  const overlay = overlays.get(key)
  return resolveBounds(overlay?.options || {}, placement(key), vp)
}

export function moveOverlay(key, left, top, vp = viewport) {
  const box = overlayBounds(key, vp)
  const next = clampBox({ ...box, left, top }, vp)
  patch(key, { left: next.left, top: next.top })
}

export function resizeOverlay(key, width, height, vp = viewport) {
  const box = overlayBounds(key, vp)
  const next = clampBox({ ...box, width, height }, vp)
  patch(key, { width: next.width, height: next.height, left: next.left, top: next.top })
}

/** Follow the pointer from a ``pointerdown`` until release; ``mode`` is ``move`` or ``resize``. */
export function beginDrag(key, event, mode = "move") {
  if (typeof window === "undefined" || event.button > 0) return
  const options = overlays.get(key)?.options || {}
  if (mode === "move" ? options.draggable === false : !options.resizable) return
  event.preventDefault()
  const start = { x: event.clientX, y: event.clientY, box: overlayBounds(key) }
  const onMove = (e) => {
    const dx = e.clientX - start.x
    const dy = e.clientY - start.y
    if (mode === "move") moveOverlay(key, start.box.left + dx, start.box.top + dy)
    else resizeOverlay(key, start.box.width + dx, start.box.height + dy)
  }
  const onUp = () => {
    window.removeEventListener("pointermove", onMove)
    window.removeEventListener("pointerup", onUp)
    window.removeEventListener("pointercancel", onUp)
  }
  window.addEventListener("pointermove", onMove)
  window.addEventListener("pointerup", onUp)
  window.addEventListener("pointercancel", onUp)
}

/** Forget the saved position and size of one overlay. */
export function resetOverlay(key) {
  const { visible, windowAllowed } = placement(key)
  const kept = {}
  if (typeof visible === "boolean") kept.visible = visible
  if (windowAllowed === true) kept.windowAllowed = true
  placements[key] = kept
  writeStorage()
}

export function resetAllOverlays() {
  for (const key of overlays.keys()) resetOverlay(key)
}
