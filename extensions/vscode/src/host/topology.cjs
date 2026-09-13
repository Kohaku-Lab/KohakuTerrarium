class TopologyWatcher {
  constructor({
    socketFactory,
    endpoint,
    token,
    onInvalidate,
    initialRetryMs = 1_000,
    maxRetryMs = 10_000,
    openTimeoutMs = 10_000,
    setTimer = setTimeout,
    clearTimer = clearTimeout,
  }) {
    this.socketFactory = socketFactory
    this.endpoint = endpoint
    this.token = token
    this.onInvalidate = onInvalidate
    this.initialRetryMs = initialRetryMs
    this.maxRetryMs = maxRetryMs
    this.openTimeoutMs = openTimeoutMs
    this.setTimer = setTimer
    this.clearTimer = clearTimer
    this.socket = null
    this.openTimer = null
    this.retryTimer = null
    this.retryToken = null
    this.retryMs = initialRetryMs
    this.generation = 0
  }

  start() {
    this.close()
    return this.connect(this.generation)
  }

  connect(generation) {
    if (generation !== this.generation) return null
    const url = this.endpoint.replace(/^http:/, 'ws:') + '/ws/runtime/graph'
    let socket
    try {
      socket = this.socketFactory(url, this.token ? [`kt-token.${this.token}`] : [])
    } catch {
      this.scheduleRetry(generation)
      return null
    }
    try {
      if (!socket || (typeof socket !== 'object' && typeof socket !== 'function') || typeof socket.close !== 'function') {
        this.scheduleRetry(generation)
        return null
      }
    } catch {
      this.scheduleRetry(generation)
      return null
    }
    this.socket = socket
    let retryScheduled = false
    let openTimer = null
    const cancelOpenTimer = () => {
      if (openTimer == null) return
      try {
        this.clearTimer(openTimer)
      } catch {}
      if (this.openTimer === openTimer) this.openTimer = null
      openTimer = null
    }
    const retry = () => {
      if (retryScheduled || this.socket !== socket || generation !== this.generation) return
      retryScheduled = true
      cancelOpenTimer()
      this.socket = null
      try {
        socket.close()
      } catch {}
      this.scheduleRetry(generation)
    }
    let invalidating = false
    let pendingFrame = null
    const invalidate = async (frame) => {
      if (invalidating) {
        pendingFrame = frame
        return
      }
      invalidating = true
      let current = frame
      while (current && this.socket === socket && generation === this.generation) {
        try {
          await this.onInvalidate(current)
        } catch {
          retry()
          return
        }
        current = pendingFrame
        pendingFrame = null
      }
      invalidating = false
    }
    try {
      openTimer = this.setTimer(retry, this.openTimeoutMs)
      this.openTimer = openTimer
      openTimer?.unref?.()
      socket.onopen = () => {
        if (this.socket !== socket || generation !== this.generation) return
        cancelOpenTimer()
        this.retryMs = this.initialRetryMs
        void invalidate({ type: 'topology_reconnected' })
      }
      socket.onerror = retry
      socket.onclose = retry
      socket.onmessage = (event) => {
        if (this.socket !== socket || generation !== this.generation) return
        let frame
        try {
          frame = JSON.parse(String(event.data))
        } catch {
          return
        }
        if (!frame || typeof frame !== 'object' || Array.isArray(frame)) return
        if (!['topology_changed', 'creature_stopped'].includes(frame.type)) return
        void invalidate(frame)
      }
    } catch {
      retry()
      return null
    }
    return socket
  }

  scheduleRetry(generation) {
    if (generation !== this.generation || this.retryToken) return
    const delay = this.retryMs
    this.retryMs = Math.min(this.retryMs * 2, this.maxRetryMs)
    const token = {}
    this.retryToken = token
    const retry = () => {
      setImmediate(() => {
        if (this.retryToken !== token || generation !== this.generation) return
        this.retryToken = null
        this.retryTimer = null
        this.connect(generation)
      })
    }
    try {
      this.retryTimer = this.setTimer(retry, delay)
      this.retryTimer?.unref?.()
    } catch {
      this.retryToken = null
      this.retryTimer = null
    }
  }

  close() {
    this.generation++
    this.retryToken = null
    try {
      if (this.openTimer != null) this.clearTimer(this.openTimer)
    } catch {}
    this.openTimer = null
    try {
      if (this.retryTimer != null) this.clearTimer(this.retryTimer)
    } catch {}
    this.retryTimer = null
    this.retryMs = this.initialRetryMs
    const socket = this.socket
    this.socket = null
    try {
      socket?.close()
    } catch {}
  }
}

module.exports = { TopologyWatcher }
