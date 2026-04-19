/**
 * Visibility-aware setInterval.
 *
 * Starts a polling interval that automatically stops while the page is
 * hidden (backgrounded tab, minimised window) and resumes when it
 * becomes visible again — running the callback once on resume so the
 * UI doesn't show stale data.
 *
 * Background tabs with active polling are a significant idle GPU /
 * CPU drain: the browser already throttles `setInterval` in
 * background tabs but does not stop it, and every tick still triggers
 * reactive updates in Vue. Gating on `document.visibilityState`
 * eliminates both costs.
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

/**
 * Create a visibility-aware interval controller.
 *
 * @param {() => void} callback   Fired on each tick AND once on resume.
 * @param {number} intervalMs     Tick interval in milliseconds.
 * @param {object} [opts]
 * @param {boolean} [opts.immediate=false]
 *   If true, invoke `callback` immediately on start().
 * @returns {{ start: () => void, stop: () => void, isRunning: () => boolean }}
 */
export function createVisibilityInterval(callback, intervalMs, opts = {}) {
  const { immediate = false } = opts
  let timer = null
  let started = false
  let onVisibility = null

  function tick() {
    try {
      callback()
    } catch (err) {
      console.error("[useVisibilityInterval] callback threw:", err)
    }
  }

  function armTimer() {
    if (timer !== null) return
    timer = setInterval(tick, intervalMs)
  }
  function disarmTimer() {
    if (timer === null) return
    clearInterval(timer)
    timer = null
  }

  function start() {
    if (started) return
    started = true
    if (immediate) tick()
    if (document.visibilityState === "visible") armTimer()
    onVisibility = () => {
      if (!started) return
      if (document.visibilityState === "visible") {
        if (timer === null) {
          tick() // catch up once immediately
          armTimer()
        }
      } else {
        disarmTimer()
      }
    }
    document.addEventListener("visibilitychange", onVisibility)
  }

  function stop() {
    if (!started) return
    started = false
    disarmTimer()
    if (onVisibility) {
      document.removeEventListener("visibilitychange", onVisibility)
      onVisibility = null
    }
  }

  return {
    start,
    stop,
    isRunning: () => started,
  }
}

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
