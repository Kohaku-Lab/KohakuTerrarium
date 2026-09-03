import { nextTick } from "vue"

// Distance (px) from the top of the viewport at which continuous
// upward scrolling expands the render window without a click on
// "show earlier".
export const CHAT_AUTO_EXPAND_TOP_PX = 48

// Reading-position anchor for prepending content. The first message
// wrapper visible at the viewport top is captured before the DOM
// changes; after the commit its viewport-relative position is restored
// via an absolute delta on its live rect. Unlike a scrollHeight delta
// this is self-correcting when the browser's native scroll anchoring
// already adjusted scrollTop for the insertion (the anchor delta then
// reads ~0, so no double compensation).
export function captureViewportAnchor(getViewportEl) {
  const el = getViewportEl()
  if (!el) return null
  const viewportTop = el.getBoundingClientRect().top
  for (const wrapper of el.querySelectorAll("[data-message-id]")) {
    if (wrapper.getBoundingClientRect().bottom > viewportTop) {
      return { element: wrapper, offset: wrapper.getBoundingClientRect().top - viewportTop }
    }
  }
  return null
}

export function restoreViewportAnchor(getViewportEl, anchor) {
  if (!anchor || !anchor.element.isConnected) return
  const el = getViewportEl()
  if (!el) return
  const viewportTop = el.getBoundingClientRect().top
  el.scrollTop += anchor.element.getBoundingClientRect().top - viewportTop - anchor.offset
}

function defaultScheduleIdle(callback) {
  if (typeof window !== "undefined" && typeof window.requestIdleCallback === "function") {
    return window.requestIdleCallback(callback, { timeout: 400 })
  }
  return setTimeout(callback, 120)
}

function defaultCancelIdle(handle) {
  if (typeof window !== "undefined" && typeof window.cancelIdleCallback === "function") {
    window.cancelIdleCallback(handle)
  } else {
    clearTimeout(handle)
  }
}

// Drives automatic history expansion on top of useChatRenderWindow:
// - ``maybeExpandAtTop`` grows the window one small step when scrolling
//   reaches the top of the rendered range, compensating scrollTop after
//   the DOM commit so reading position never jumps.
// - ``scheduleIdleExpand`` pre-mounts the next step off the interaction
//   path. It never chains: at most one batch lives ahead of the
//   viewport, so idle expansion cannot walk the whole history.
export function createChatHistoryExpander({
  canExpand,
  expand,
  getViewportEl,
  getContext,
  scheduleIdle = defaultScheduleIdle,
  cancelIdle = defaultCancelIdle,
}) {
  let idleHandle = null
  let expanding = false
  let disposed = false

  async function expandAndCompensate() {
    const context = getContext?.()
    const anchor = captureViewportAnchor(getViewportEl)
    expand()
    await nextTick()
    // A scope switch during the DOM commit means the prepended content
    // no longer belongs to the mounted list — don't shift the new
    // scope's restored scroll position by a stale delta.
    if (getContext && getContext() !== context) return
    restoreViewportAnchor(getViewportEl, anchor)
  }

  async function runExpand() {
    expanding = true
    try {
      await expandAndCompensate()
    } catch (error) {
      // Scroll-handler-originated work must never surface as an
      // unhandled rejection.
      console.warn("[chat] history expansion failed", error)
    } finally {
      expanding = false
    }
  }

  function maybeExpandAtTop(scrollTop) {
    if (disposed || expanding || !canExpand() || scrollTop > CHAT_AUTO_EXPAND_TOP_PX) return false
    // The interactive step mounts the batch a pending lookahead was
    // going to pre-mount, so supersede it: keeps at most one batch
    // ahead of the reading position, and keeps the setTimeout fallback
    // from firing a stale expansion mid-gesture.
    cancelIdleExpand()
    const context = getContext?.()
    runExpand().then(() => {
      // A scope switch or disposal during the in-flight expansion
      // invalidates the continuation: never re-arm the lookahead for a
      // scope the user did not scroll in.
      if (disposed || (getContext && getContext() !== context)) return
      scheduleIdleExpand()
    })
    return true
  }

  function scheduleIdleExpand() {
    if (disposed || idleHandle !== null || expanding || !canExpand()) return
    idleHandle = scheduleIdle(() => {
      idleHandle = null
      if (disposed || !canExpand()) return
      runExpand()
    })
  }

  function cancelIdleExpand() {
    if (idleHandle === null) return
    cancelIdle(idleHandle)
    idleHandle = null
  }

  function dispose() {
    disposed = true
    cancelIdleExpand()
  }

  return {
    cancelIdleExpand,
    dispose,
    maybeExpandAtTop,
    scheduleIdleExpand,
  }
}
