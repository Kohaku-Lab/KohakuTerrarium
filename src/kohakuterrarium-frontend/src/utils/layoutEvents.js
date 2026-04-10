/**
 * Tiny global event bus for layout-related actions that cross panel
 * boundaries. Consumers subscribe with `onLayoutEvent(name, handler)`
 * and fire with one of the typed helpers below.
 *
 * Phase 4 uses this for the preset strip's edit / save-as buttons and
 * for global keyboard shortcuts. Phase 5 (edit mode) and Phase 10
 * (command palette) add more handlers.
 */

const target =
  typeof window !== "undefined" ? window : /** @type {any} */ ({});

const LAYOUT_EVENTS = Object.freeze({
  EDIT_REQUESTED: "layout:edit-requested",
  SAVE_AS_REQUESTED: "layout:save-as-requested",
  PALETTE_OPEN: "palette:open",
});

function _dispatch(name, detail) {
  if (typeof CustomEvent === "undefined" || !target.dispatchEvent) return;
  target.dispatchEvent(new CustomEvent(name, { detail }));
}

export function fireLayoutEditRequested(detail = {}) {
  _dispatch(LAYOUT_EVENTS.EDIT_REQUESTED, detail);
}

export function fireLayoutSaveAsRequested(detail = {}) {
  _dispatch(LAYOUT_EVENTS.SAVE_AS_REQUESTED, detail);
}

export function firePaletteOpen(detail = {}) {
  _dispatch(LAYOUT_EVENTS.PALETTE_OPEN, detail);
}

export function onLayoutEvent(name, handler) {
  if (!target.addEventListener) return () => {};
  target.addEventListener(name, handler);
  return () => target.removeEventListener(name, handler);
}

export { LAYOUT_EVENTS };
