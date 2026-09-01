export function createChatScrollScheduler({
  afterDomCommit,
  requestFrame,
  cancelFrame,
  shouldScroll,
  scroll,
}) {
  let commitPending = false
  let frameId = null
  let forcePending = false
  let pendingScope
  let generation = 0
  let suppressed = false
  let disposed = false

  function invalidate() {
    generation++
    commitPending = false
    forcePending = false
    pendingScope = undefined
    if (frameId !== null) {
      cancelFrame(frameId)
      frameId = null
    }
  }

  function suppress() {
    invalidate()
    suppressed = true
  }

  function resume() {
    suppressed = false
  }

  function schedule(force = false, scope) {
    if (disposed || suppressed) return
    if ((commitPending || frameId !== null) && pendingScope !== scope) invalidate()

    pendingScope = scope
    forcePending ||= force
    if (commitPending || frameId !== null) return

    const scheduledGeneration = generation
    commitPending = true
    afterDomCommit(() => {
      if (disposed || generation !== scheduledGeneration) return
      commitPending = false
      frameId = requestFrame(() => {
        if (disposed || suppressed || generation !== scheduledGeneration) return
        frameId = null
        const forceScroll = forcePending
        forcePending = false
        pendingScope = undefined
        if (forceScroll || shouldScroll()) scroll()
      })
    })
  }

  function dispose() {
    disposed = true
    invalidate()
  }

  return { schedule, suppress, resume, invalidate, dispose }
}
