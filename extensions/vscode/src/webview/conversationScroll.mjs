'use strict'

export const NEAR_BOTTOM_THRESHOLD = 80

// Shared near-bottom probe so the scroll controller, the paging expander, and
// the live-follow tail all agree on one threshold.
export function isNearBottom(element) {
  return !element || element.scrollHeight - element.scrollTop - element.clientHeight < NEAR_BOTTOM_THRESHOLD
}

export function createConversationScrollController({ schedule = (callback) => callback() } = {}) {
  const positions = new Map()
  let identity = ''
  let viewport = null
  let nearBottom = true
  let forceFollowNextUpdate = true
  let hasMessages = false
  let initialPositionApplied = false
  let generation = 0
  let disposed = false

  function current(tokenIdentity, tokenViewport, tokenGeneration) {
    return !disposed && identity === tokenIdentity && viewport === tokenViewport && generation === tokenGeneration
  }

  function updateState() {
    if (!viewport || !identity) return
    nearBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < NEAR_BOTTOM_THRESHOLD
    positions.set(identity, viewport.scrollTop)
  }

  function scrollToBottom() {
    if (!viewport || !identity) return
    viewport.scrollTop = viewport.scrollHeight
    updateState()
  }

  function scheduleCurrent(callback) {
    const tokenIdentity = identity
    const tokenViewport = viewport
    const tokenGeneration = generation
    schedule(() => {
      if (current(tokenIdentity, tokenViewport, tokenGeneration)) callback(tokenViewport)
    })
  }

  function applyInitialPosition() {
    if (initialPositionApplied || !hasMessages || !viewport || !identity) return
    initialPositionApplied = true
    const saved = positions.get(identity)
    scheduleCurrent((element) => {
      if (saved == null) element.scrollTop = element.scrollHeight
      else element.scrollTop = Math.max(0, Math.min(saved, element.scrollHeight - element.clientHeight))
      updateState()
      forceFollowNextUpdate = saved == null
    })
  }

  return {
    setIdentity(nextIdentity, options = {}) {
      if (disposed) return
      const normalized = nextIdentity || ''
      const nextHasMessages = options.hasMessages === true
      if (normalized === identity) {
        hasMessages = nextHasMessages
        applyInitialPosition()
        return
      }
      if (viewport && identity) positions.set(identity, viewport.scrollTop)
      identity = normalized
      hasMessages = nextHasMessages
      initialPositionApplied = false
      forceFollowNextUpdate = true
      nearBottom = true
      generation += 1
      applyInitialPosition()
    },

    onViewportReady(element, expectedIdentity = identity) {
      if (disposed || !element || expectedIdentity !== identity) return
      if (viewport !== element) {
        viewport = element
        initialPositionApplied = false
        generation += 1
      }
      applyInitialPosition()
    },

    onScroll(event, expectedIdentity = identity) {
      if (disposed || !viewport || expectedIdentity !== identity || event?.target !== viewport) return
      updateState()
      if (!nearBottom) forceFollowNextUpdate = false
    },

    onMessagesUpdated(options = {}) {
      if (disposed) return
      hasMessages = options.hasMessages === true
      if (!initialPositionApplied) {
        applyInitialPosition()
        return
      }
      if (forceFollowNextUpdate || nearBottom) {
        forceFollowNextUpdate = false
        scheduleCurrent(scrollToBottom)
      }
    },

    forceFollow() {
      if (disposed) return
      forceFollowNextUpdate = true
      nearBottom = true
      scheduleCurrent(scrollToBottom)
    },

    beforePrepend() {
      if (disposed || !viewport || !identity) return () => {}
      const tokenIdentity = identity
      const tokenViewport = viewport
      const tokenGeneration = generation
      const oldHeight = viewport.scrollHeight
      return () => {
        schedule(() => {
          if (!current(tokenIdentity, tokenViewport, tokenGeneration)) return
          tokenViewport.scrollTop += tokenViewport.scrollHeight - oldHeight
          updateState()
        })
      }
    },

    getSavedPosition(key) {
      return positions.get(key)
    },

    dispose() {
      if (disposed) return
      if (viewport && identity) positions.set(identity, viewport.scrollTop)
      disposed = true
      viewport = null
      generation += 1
    },
  }
}
