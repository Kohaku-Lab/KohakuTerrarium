import { nextTick } from "vue"
import { indexOfSemanticKey, semanticKey } from "./chatRenderWindow"

// Distance (px) from the top of the viewport at which continuous
// upward scrolling expands the render window without a click on
// "show earlier".
export const CHAT_AUTO_EXPAND_TOP_PX = 48

// Automatic (scroll / idle) expansion uses a smaller step than explicit
// (button) expansion so a scroll gesture never mounts a whole page at
// once. These mirror the render budgets in chatRenderWindow.js.
export const CHAT_HISTORY_AUTO_STEP = { unitBudget: 500, messageLimit: 100 }
export const CHAT_HISTORY_MANUAL_STEP = { unitBudget: 1000, messageLimit: 200 }

// Capture the first visible row's physical key and viewport offset.
export function captureSemanticAnchor(getViewportEl, getMessages) {
  const el = getViewportEl()
  if (!el) return null
  const viewportTop = el.getBoundingClientRect().top
  for (const wrapper of el.querySelectorAll("[data-message-id]")) {
    if (wrapper.getBoundingClientRect().bottom > viewportTop) {
      let key = null
      if (getMessages) {
        const id =
          typeof wrapper.getAttribute === "function"
            ? wrapper.getAttribute("data-message-id")
            : null
        const message = getMessages().find((m) => m != null && String(m.id) === String(id))
        key = semanticKey(message)
      }
      return { element: wrapper, offset: wrapper.getBoundingClientRect().top - viewportTop, key }
    }
  }
  return null
}

// Resolve the current semantic row after the DOM commit.
export function restoreSemanticAnchor(getViewportEl, getMessages, anchor) {
  if (!anchor) return
  let element = anchor.key == null && anchor.element?.isConnected ? anchor.element : null
  if (getMessages && anchor.key != null) {
    const messages = getMessages()
    const index = indexOfSemanticKey(messages, anchor.key)
    if (index >= 0) {
      const id = messages[index]?.id
      const vp = getViewportEl()
      if (vp) {
        for (const wrapper of vp.querySelectorAll("[data-message-id]")) {
          if (
            typeof wrapper.getAttribute === "function" &&
            String(wrapper.getAttribute("data-message-id")) === String(id)
          ) {
            element = wrapper
            break
          }
        }
      }
    }
  }
  if (!element) return
  const el = getViewportEl()
  if (!el) return
  const viewportTop = el.getBoundingClientRect().top
  el.scrollTop += element.getBoundingClientRect().top - viewportTop - anchor.offset
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

const initialFills = new WeakMap()

// Coordinate manual, scroll, and idle expansion with semantic compensation.
export function createChatHistoryExpander({
  canExpand,
  expand,
  getViewportEl,
  getMessages,
  getContext,
  initialFill,
  onCompensated,
  autoStep = CHAT_HISTORY_AUTO_STEP,
  manualStep = CHAT_HISTORY_MANUAL_STEP,
  scheduleIdle = defaultScheduleIdle,
  cancelIdle = defaultCancelIdle,
}) {
  let idleHandle = null
  let expanding = false
  let disposed = false
  let fillState = null
  let fillHandle = null

  function getFillState() {
    const owner = initialFill.owner()
    const generation = initialFill.generation()
    let session = initialFills.get(owner)
    if (!session || session.generation !== generation) {
      session = { generation, tabs: new Map() }
      initialFills.set(owner, session)
    }
    const key = initialFill.key()
    let state = session.tabs.get(key)
    if (!state) session.tabs.set(key, (state = { claimed: false, cancelled: false }))
    return state
  }

  function cancelInitialFill(recordIntent = false) {
    const state = recordIntent && initialFill ? getFillState() : fillState
    if (state) {
      state.claimed = true
      state.cancelled = true
    }
    if (fillHandle !== null) cancelIdle(fillHandle)
    fillHandle = null
  }

  function startInitialFill() {
    if (disposed || !initialFill?.ready()) return
    const owner = initialFill.owner()
    const key = initialFill.key()
    const generation = initialFill.generation()
    const state = getFillState()
    if (state.claimed) return
    fillState = state
    state.claimed = true
    const deadline = Date.now() + 1500
    let pages = 0
    const current = () =>
      !disposed &&
      !state.cancelled &&
      initialFill.owner() === owner &&
      initialFill.key() === key &&
      initialFill.generation() === generation &&
      initialFill.atTail() &&
      Date.now() < deadline
    const schedule = () => {
      if (!current() || pages >= 3 || !initialFill.needsMore()) return
      fillHandle = scheduleIdle(async () => {
        fillHandle = null
        if (!current() || !initialFill.needsMore()) return
        pages += 1
        try {
          const result = await initialFill.prefetch()
          if (!current() || result?.discarded || !initialFill.needsMore()) return
          if (!initialFill.materialize()?.applied) return
          await nextTick()
          if (!current()) return
          initialFill.scroll()
          schedule()
        } catch (error) {
          console.warn("[chat] initial history fill failed", error)
        }
      })
    }
    schedule()
  }

  async function expandAndCompensate(step, intent) {
    const context = getContext?.()
    const preAnchor = captureSemanticAnchor(getViewportEl, getMessages)

    const result = await expand(step, intent)
    await nextTick()

    if (getContext && getContext() !== context) return
    if (result === false) return
    const anchor = result && typeof result === "object" ? result : preAnchor
    restoreSemanticAnchor(getViewportEl, getMessages, anchor)
    onCompensated?.()
  }

  async function runExpand(step, intent = { idle: false }) {
    expanding = true
    try {
      await expandAndCompensate(step, intent)
    } catch (error) {
      // Scroll-handler-originated work must never surface as an
      // unhandled rejection.
      console.warn("[chat] history expansion failed", error)
    } finally {
      expanding = false
    }
  }

  function maybeExpandAtTop(scrollTop) {
    cancelInitialFill()
    if (disposed || expanding || !canExpand() || scrollTop > CHAT_AUTO_EXPAND_TOP_PX) return false
    // The interactive step mounts the batch a pending lookahead was going
    // to pre-mount, so supersede it: keeps at most one batch ahead of the
    // reading position, and keeps the setTimeout fallback from firing a
    // stale expansion mid-gesture.
    cancelIdleExpand()
    const context = getContext?.()
    runExpand(autoStep).then(() => {
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
      runExpand(autoStep, { idle: true })
    })
  }

  function cancelIdleExpand() {
    if (idleHandle === null) return
    cancelIdle(idleHandle)
    idleHandle = null
  }

  // Explicit "show earlier" button expansion. Shares the coordinator with
  // automatic expansion but uses the larger manual budget.
  function expandManual() {
    cancelInitialFill(true)
    if (disposed) return Promise.resolve()
    cancelIdleExpand()
    if (expanding) return Promise.resolve()
    return runExpand(manualStep)
  }

  function dispose() {
    disposed = true
    cancelInitialFill()
    cancelIdleExpand()
  }

  return {
    startInitialFill,
    cancelInitialFill,
    cancelIdleExpand,
    dispose,
    expandManual,
    maybeExpandAtTop,
    scheduleIdleExpand,
  }
}
