/**
 * Overlay registry and per-user placement state.
 *
 * An overlay is a package component drawn in a fixed layer above the whole
 * app. ``overlays`` holds the registered ones; ``placements`` holds what the
 * user changed (visibility, position, size) and is remembered per host.
 */

import { markRaw, reactive } from "vue"
import { setHybridPref } from "@/utils/uiPrefs"

export const OVERLAY_MARGIN = 16
const MIN_SIZE = 48
const STORAGE_PREFIX = "kt.overlays."

export const overlays = reactive(new Map())
export const placements = reactive({})
export const interactionRegions = reactive(new Map())

export function setOverlayInteractionRegions(key, regions) {
  if (regions === null) interactionRegions.delete(key)
  else if (
    Array.isArray(regions) &&
    regions.length <= 16 &&
    regions.every(
      (r) => Array.isArray(r) && r.length === 4 && r.every(Number.isFinite) && r[2] > 0 && r[3] > 0,
    )
  )
    interactionRegions.set(key, regions)
}

/** Size of the window the overlays live in; the layer keeps it current. */
export const viewport = reactive({
  width: typeof window === "undefined" ? 1280 : window.innerWidth,
  height: typeof window === "undefined" ? 800 : window.innerHeight,
})

export function syncViewport() {
  viewport.width = window.innerWidth
  viewport.height = window.innerHeight
  for (const key of interactionRegions.keys()) {
    const saved = placement(key)
    if (Number.isFinite(saved.left) && Number.isFinite(saved.top))
      moveOverlay(key, saved.left, saved.top)
  }
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
  for (const key of keys) {
    overlays.delete(key)
    interactionRegions.delete(key)
  }
}

export function clearOverlays() {
  interactionRegions.clear()
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
  if (
    typeof fields.visible === "boolean" &&
    overlays.get(key)?.options.startupGroup &&
    _storageKey
  ) {
    const visibility = Object.fromEntries(
      Object.entries(placements)
        .filter(([, p]) => typeof p.visible === "boolean")
        .map(([id, p]) => [id, p.visible]),
    )
    try {
      setHybridPref(
        `kt.overlayVisibility.${_storageKey.slice(STORAGE_PREFIX.length)}`,
        visibility,
        { json: true },
      )
    } catch {
      /* local placement remains available */
    }
  }
}

export function restoreVisibility(key, visible) {
  placements[key] = { ...placement(key), visible }
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
export function clampBox(box, viewport, regions) {
  const width = Math.min(Math.max(box.width, MIN_SIZE), viewport.width)
  const height = Math.min(Math.max(box.height, MIN_SIZE), viewport.height)
  const valid = regions?.length ? regions : [[0, 0, width, height]]
  const left = Math.min(...valid.map((r) => r[0]))
  const top = Math.min(...valid.map((r) => r[1]))
  const right = Math.max(...valid.map((r) => r[0] + r[2]))
  const bottom = Math.max(...valid.map((r) => r[1] + r[3]))
  return {
    width,
    height,
    left: Math.min(Math.max(box.left, -left), Math.max(viewport.width - right, -left)) + 0,
    top: Math.min(Math.max(box.top, -top), Math.max(viewport.height - bottom, -top)) + 0,
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
  const saved = placement(key)
  const resolved = resolveBounds(overlay?.options || {}, saved, vp)
  return interactionRegions.has(key) && Number.isFinite(saved.left) && Number.isFinite(saved.top)
    ? { ...resolved, left: saved.left, top: saved.top }
    : resolved
}

export function moveOverlay(key, left, top, vp = viewport) {
  const box = overlayBounds(key, vp)
  const next = clampBox({ ...box, left, top }, vp, interactionRegions.get(key))
  patch(key, { left: next.left, top: next.top })
}

export function resizeOverlay(key, width, height, vp = viewport) {
  const box = overlayBounds(key, vp)
  const regions = interactionRegions
    .get(key)
    ?.map(([x, y, w, h]) => [
      (x * width) / box.width,
      (y * height) / box.height,
      (w * width) / box.width,
      (h * height) / box.height,
    ])
  const next = clampBox({ ...box, width, height }, vp, regions)
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
  interactionRegions.delete(key)
  try {
    localStorage.removeItem(`kt.overlay.window.${key}`)
    localStorage.setItem(`kt.overlay.reset.${key}`, String(Date.now()))
  } catch {
    /* Optional placement persistence. */
  }
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
