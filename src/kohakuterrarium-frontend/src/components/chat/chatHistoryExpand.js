import { nextTick } from "vue"

// Distance (px) from the top of the viewport at which continuous
// upward scrolling expands the render window without a click on
// "show earlier".
export const CHAT_AUTO_EXPAND_TOP_PX = 48

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

  async function expandAndCompensate() {
    const context = getContext?.()
    const el = getViewportEl()
    const prevHeight = el ? el.scrollHeight : 0
    expand()
    await nextTick()
    // A scope switch during the DOM commit means the prepended content
    // no longer belongs to the mounted list — don't shift the new
    // scope's restored scroll position by a stale delta.
    if (getContext && getContext() !== context) return
    const after = getViewportEl()
    if (after && prevHeight) after.scrollTop += after.scrollHeight - prevHeight
  }

  async function runExpand() {
    expanding = true
    try {
      await expandAndCompensate()
    } finally {
      expanding = false
    }
  }

  function maybeExpandAtTop(scrollTop) {
    if (expanding || !canExpand() || scrollTop > CHAT_AUTO_EXPAND_TOP_PX) return false
    runExpand().then(scheduleIdleExpand)
    return true
  }

  function scheduleIdleExpand() {
    if (idleHandle !== null || expanding || !canExpand()) return
    idleHandle = scheduleIdle(() => {
      idleHandle = null
      if (!canExpand()) return
      runExpand()
    })
  }

  function cancelIdleExpand() {
    if (idleHandle === null) return
    cancelIdle(idleHandle)
    idleHandle = null
  }

  function dispose() {
    cancelIdleExpand()
  }

  return {
    cancelIdleExpand,
    dispose,
    maybeExpandAtTop,
    scheduleIdleExpand,
  }
}
