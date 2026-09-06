export function createVisibilityInterval(callback, interval, opts = {}) {
  const { immediate = false } = opts
  let timer = null
  let started = false
  let onVisibility = null

  function tick() {
    try {
      callback()
    } catch (error) {
      console.error('[useVisibilityInterval] callback threw:', error)
    }
  }

  function armTimer() {
    if (timer === null) timer = setInterval(tick, interval)
  }

  function disarmTimer() {
    if (timer === null) return
    clearInterval(timer)
    timer = null
  }

  function start() {
    if (started) return
    started = true
    if (document.visibilityState === 'visible') {
      if (immediate) tick()
      armTimer()
    }
    onVisibility = () => {
      if (!started) return
      if (document.visibilityState === 'visible') {
        if (timer === null) {
          tick()
          armTimer()
        }
      } else {
        disarmTimer()
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
  }

  function stop() {
    if (!started) return
    started = false
    disarmTimer()
    if (onVisibility) {
      document.removeEventListener('visibilitychange', onVisibility)
      onVisibility = null
    }
  }

  return {
    start,
    stop,
    isRunning: () => started,
  }
}
