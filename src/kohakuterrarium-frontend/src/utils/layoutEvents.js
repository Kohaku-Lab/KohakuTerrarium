/**
 * Tiny global event bus for layout-related actions that cross panel
 * boundaries. Consumers subscribe with `onLayoutEvent(name, handler)`
 * and fire with one of the typed helpers below.
 *
 * Phase 4 uses this for the preset strip's edit / save-as buttons and
 * for global keyboard shortcuts. Phase 5 (edit mode) and Phase 10
 * (command palette) add more handlers.
 */

const target = typeof window !== "undefined" ? window : /** @type {any} */ ({})

const LAYOUT_EVENTS = Object.freeze({
  EDIT_REQUESTED: "layout:edit-requested",
  SAVE_AS_REQUESTED: "layout:save-as-requested",
  PALETTE_OPEN: "palette:open",
  MODEL_CONFIG_OPEN: "model:config-open",
  // Deep-link into the Drives panel: {sessionId, driveId?}. An open Drives
  // panel for that session focuses the record and marks the event handled;
  // the header badge opens a drawer when nothing claimed it.
  OPEN_DRIVES: "drives:open",
  // Open the full Drives panel as a drawer over the current layout:
  // {sessionId, driveId?}. Handled by the header badge that hosts the drawer.
  OPEN_DRIVES_DRAWER: "drives:open-drawer",
})

function _dispatch(name, detail) {
  if (typeof CustomEvent === "undefined" || !target.dispatchEvent) return false
  // A listener that acts on the event calls ``preventDefault`` so the
  // dispatcher can tell "handled" from "nobody was listening".
  return !target.dispatchEvent(new CustomEvent(name, { detail, cancelable: true }))
}

export function fireLayoutEditRequested(detail = {}) {
  _dispatch(LAYOUT_EVENTS.EDIT_REQUESTED, detail)
}

export function fireLayoutSaveAsRequested(detail = {}) {
  _dispatch(LAYOUT_EVENTS.SAVE_AS_REQUESTED, detail)
}

export function firePaletteOpen(detail = {}) {
  _dispatch(LAYOUT_EVENTS.PALETTE_OPEN, detail)
}

export function fireModelConfigOpen(detail = {}) {
  _dispatch(LAYOUT_EVENTS.MODEL_CONFIG_OPEN, detail)
}

/** Returns true when a mounted Drives panel claimed the event. */
export function fireOpenDrives(detail = {}) {
  return _dispatch(LAYOUT_EVENTS.OPEN_DRIVES, detail)
}

/** Returns true when a drawer host claimed the event. */
export function fireOpenDrivesDrawer(detail = {}) {
  return _dispatch(LAYOUT_EVENTS.OPEN_DRIVES_DRAWER, detail)
}

export function onLayoutEvent(name, handler) {
  if (!target.addEventListener) return () => {}
  target.addEventListener(name, handler)
  return () => target.removeEventListener(name, handler)
}

export { LAYOUT_EVENTS }
