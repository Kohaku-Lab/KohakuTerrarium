/**
 * ``host.theme`` for widget renderers: the dashboard's current CSS tokens as
 * ``{scheme, vars}``, kept in sync with the light/dark switch.
 */

import { reactive } from "vue"

import { readTheme } from "./app/appDocument.js"

let shared = null

export function widgetTheme() {
  if (shared) return shared
  shared = reactive(readTheme())
  const root = globalThis.document?.documentElement
  if (root && globalThis.MutationObserver) {
    new MutationObserver(() => Object.assign(shared, readTheme())).observe(root, {
      attributes: true,
      attributeFilter: ["class", "style"],
    })
  }
  return shared
}
