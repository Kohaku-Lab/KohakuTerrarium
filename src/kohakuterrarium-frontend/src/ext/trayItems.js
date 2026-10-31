/**
 * Tray menu items contributed by packages (desktop shell only).
 *
 * A ``tray-item`` is a label in the tray icon's menu; choosing it runs the
 * handler the package registered with ``kt.registerTrayItem``, loading the
 * package first. The shell shows at most a dozen items.
 */

import { reactive } from "vue"

import { runExtensionCommand } from "@/ext/commands"

export const TRAY_ITEM_EVENT = "kt:desktop-tray-item"

export const trayItems = reactive(new Map())

export function registerTrayItem({ key, pkg, label, order }) {
  trayItems.set(key, { key, pkg, label: label || key, order: Number.isFinite(order) ? order : 100 })
}

export function clearTrayItems() {
  trayItems.clear()
}

/** `[{ id, label }]` for the shell, in display order. */
export function trayItemList() {
  return [...trayItems.values()]
    .sort((a, b) => a.order - b.order)
    .map((item) => ({ id: item.key, label: item.label }))
}

/** Run the handler of the item the tray reported. */
export function runTrayItem(id) {
  const item = trayItems.get(id)
  return item ? runExtensionCommand(item.pkg, item.key, item.label) : Promise.resolve()
}
