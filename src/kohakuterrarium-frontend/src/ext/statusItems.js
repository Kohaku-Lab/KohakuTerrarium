/**
 * Status-bar items contributed by packages.
 *
 * ``statusItems`` holds the registered segments; the status bar draws the
 * ones for its left or right side, ordered by ``order`` then registration.
 */

import { markRaw, reactive } from "vue"

export const DEFAULT_STATUS_WIDTH = 160

export const statusItems = reactive(new Map())

/** Register a segment; ``options`` come from the manifest (``side``, ``maxWidth``). */
export function registerStatusItem({ key, pkg, id, label, order, component, options }) {
  statusItems.set(key, {
    key,
    pkg,
    id,
    label: label || id,
    order: Number.isFinite(order) ? order : 100,
    component: markRaw(component),
    options: { ...options },
  })
}

export function clearStatusItems() {
  statusItems.clear()
}

/** Segments for one side of the bar, in display order. */
export function statusItemsFor(side) {
  return [...statusItems.values()]
    .filter((item) => (item.options.side || "right") === side)
    .sort((a, b) => a.order - b.order)
}

/** Pixel width cap of one segment. */
export function statusItemWidth(item) {
  const width = item.options.maxWidth
  return Number.isFinite(width) && width > 0 ? width : DEFAULT_STATUS_WIDTH
}
