/**
 * Visibility-aware setInterval.
 *
 * The pure, framework-free poller lives in ``./visibilityInterval`` so the
 * VS Code webview's alias boundary can re-export the exact same
 * implementation. This module adds the Vue component-scoped wrapper on top.
 *
 * Usage inside a component:
 *
 *   import { useVisibilityInterval } from "@/composables/useVisibilityInterval"
 *
 *   useVisibilityInterval(() => {
 *     fetchData()
 *   }, 5000)
 *
 * Usage inside a Pinia store (no component lifecycle available):
 *
 *   import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
 *
 *   const interval = createVisibilityInterval(() => this.fetchAll(), 5000)
 *   interval.start()
 *   // later: interval.stop()
 */

import { onBeforeUnmount } from "vue"

import { createVisibilityInterval } from "./visibilityInterval"

export { createVisibilityInterval }

/**
 * Component-scoped visibility-aware interval. Auto-starts immediately
 * and auto-stops on component unmount.
 *
 * @param {() => void} callback
 * @param {number} intervalMs
 * @param {object} [opts]
 * @returns {{ stop: () => void }}
 */
export function useVisibilityInterval(callback, intervalMs, opts = {}) {
  const ctrl = createVisibilityInterval(callback, intervalMs, opts)
  ctrl.start()
  onBeforeUnmount(() => ctrl.stop())
  return { stop: ctrl.stop }
}
