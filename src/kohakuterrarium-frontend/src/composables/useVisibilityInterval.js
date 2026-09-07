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
 * Slow-backend back-pressure: when a callback returns a promise, ticks
 * fired before that promise settles are skipped. A backend answering
 * slower than the poll period must not turn the fixed interval into a
 * pile of overlapping duplicate requests — without the skip, one more
 * request stacks per tick and the load makes the backend slower
 * still. Synchronous callbacks (returning undefined) are never
 * skipped; only return a promise from the callback when overlapping
 * ticks are unsafe.
 *
 * The visibility-resume catch-up tick obeys the same skip: if a
 * request was still in flight when the tab was hidden, the first tick
 * after returning may be dropped and data can sit up to one poll
 * window stale until the next tick lands.
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
 * @param {() => void | Promise<unknown>} callback
 *   Fired on each tick AND once on resume. A returned promise arms the
 *   in-flight skip; see the module docstring.
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
  let pending = null

  function tick() {
    if (pending !== null) return
    let result
    try {
      result = callback()
    } catch (err) {
      console.error("[useVisibilityInterval] callback threw:", err)
      return
    }
    if (result && typeof result.then === "function") {
      pending = result
      const settled = () => {
        if (pending === result) pending = null
      }
      result.then(settled, settled)
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
    if (document.visibilityState === "visible") {
      if (immediate) tick()
      armTimer()
    }
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
    // A request left in flight by the previous run must not suppress
    // the first ticks after a restart.
    pending = null
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
