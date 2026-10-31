/**
 * Panels that also open from the session dock.
 *
 * A ``panel`` declared with ``dock: true`` gets a dock button that opens it as
 * the floating widget, and the widget can be pinned to the side view like the
 * built-in ones. ``dockPanels`` maps the contribution key to its host component.
 */

import { markRaw, reactive } from "vue"

export const dockPanels = reactive(new Map())

export function registerDockPanel({ key, label, icon, order, component }) {
  dockPanels.set(key, {
    key,
    label: label || key,
    icon: icon || "i-carbon-apps",
    order: Number.isFinite(order) ? order : 100,
    component: markRaw(component),
  })
}

export function clearDockPanels() {
  dockPanels.clear()
}

/** Dock panels in display order. */
export function dockPanelList() {
  return [...dockPanels.values()].sort((a, b) => a.order - b.order)
}

export function isDockPanel(id) {
  return dockPanels.has(id)
}

/** Component of a dock panel, or ``null``. */
export function dockPanelComponent(id) {
  return dockPanels.get(id)?.component || null
}

/** Title for a widget id: the panel's label for a dock panel, else ``fallback()``. */
export function dockPanelTitle(id, fallback) {
  return dockPanels.get(id)?.label ?? fallback()
}
