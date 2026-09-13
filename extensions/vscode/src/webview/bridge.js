export class BridgeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static sockets = new Map()
  static nextId = 1
  static nextSendId = 1
  static confirmationTimeout = 30000
  static capture = null
  static post = () => {}
  static getReadyId = () => null

  constructor(url) {
    this.url = url
    this.readyId = BridgeWebSocket.getReadyId()
    this.readyState = BridgeWebSocket.CONNECTING
    this.id = BridgeWebSocket.nextId++
    this.pendingSends = new Map()
    BridgeWebSocket.sockets.set(this.id, this)
    BridgeWebSocket.post({ type: 'ws.open', socketId: this.id, ...(this.readyId == null ? {} : { readyId: this.readyId }) })
  }

  send(data) {
    if (this.readyState !== BridgeWebSocket.OPEN) throw Error('WebSocket not open')
    const sendId = BridgeWebSocket.nextSendId++
    const confirmation = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingSends.delete(sendId)
        reject(Error('Chat send confirmation timed out'))
      }, BridgeWebSocket.confirmationTimeout)
      this.pendingSends.set(sendId, { resolve, reject, timer })
    })
    // Standard WebSocket callers do not observe Host confirmations.
    confirmation.catch(() => {})
    BridgeWebSocket.capture?.push({
      confirmation,
      frame: data,
      cancel: (error) => BridgeWebSocket.cancelSend(this, sendId, error),
    })
    BridgeWebSocket.post({ type: 'ws.send', socketId: this.id, sendId, data, ...(this.readyId == null ? {} : { readyId: this.readyId }) })
  }

  close() {
    if (this.readyState >= BridgeWebSocket.CLOSING) return
    this.readyState = BridgeWebSocket.CLOSING
    this.rejectPending(Error('Chat socket closed'))
    BridgeWebSocket.post({ type: 'ws.close', socketId: this.id, ...(this.readyId == null ? {} : { readyId: this.readyId }) })
  }

  rejectPending(error) {
    for (const pending of this.pendingSends.values()) {
      clearTimeout(pending.timer)
      pending.reject(error)
    }
    this.pendingSends.clear()
  }

  static captureSend(callback, { requireConfirmation = false } = {}) {
    const previous = this.capture
    const sends = []
    this.capture = sends
    try {
      let value
      let error = null
      try {
        value = callback()
      } catch (cause) {
        error = cause
      }
      if (sends.length > 1) {
        const error = Error('Chat submit generated multiple WebSocket frames')
        for (const send of sends) send.cancel(error)
        throw error
      }
      const send = sends[0] || null
      if (requireConfirmation && send == null) {
        throw error || Error('Chat message did not reach the Host. Press Refresh Sessions and try again.')
      }
      if (error && send == null) throw error
      return {
        value,
        error,
        confirmation: send?.confirmation || null,
        frame: send?.frame,
        cancel: send?.cancel || (() => {}),
      }
    } finally {
      this.capture = previous
    }
  }

  static cancelSend(socket, sendId, error) {
    const pending = socket.pendingSends.get(sendId)
    if (!pending) return
    socket.pendingSends.delete(sendId)
    clearTimeout(pending.timer)
    pending.reject(error)
  }

  static settleSend(socket, message, error) {
    const pending = socket.pendingSends.get(message.sendId)
    if (!pending) return
    socket.pendingSends.delete(message.sendId)
    clearTimeout(pending.timer)
    if (error) pending.reject(error)
    else pending.resolve()
  }

  static receive(message) {
    if (!message || typeof message !== 'object' || Array.isArray(message)) return false
    const socket = this.sockets.get(message.socketId)
    if (!socket) return
    if (message.type === 'ws.opened') {
      socket.readyState = this.OPEN
      socket.onopen?.({ target: socket })
    } else if (message.type === 'ws.frame') {
      socket.onmessage?.({ data: message.data, target: socket })
    } else if (message.type === 'ws.closed') {
      socket.readyState = this.CLOSED
      socket.rejectPending(Error('Chat socket closed'))
      this.sockets.delete(message.socketId)
      socket.onclose?.({ code: message.code || 1000, target: socket })
    } else if (message.type === 'ws.error') {
      socket.onerror?.({ target: socket, error: message.error })
      if (socket.readyState === this.CONNECTING || socket.readyState === this.CLOSING) {
        socket.readyState = this.CLOSED
        socket.rejectPending(Error(message.error || 'Chat socket failed'))
        this.sockets.delete(message.socketId)
        socket.onclose?.({ code: 1011, target: socket })
      }
    } else if (message.type === 'ws.send.result') {
      this.settleSend(socket, message)
    } else if (message.type === 'ws.send.error') {
      this.settleSend(socket, message, Error(message.error || 'Chat send was rejected'))
    }
  }

  static disposeAll(error = Error('WebSocket bridge disposed')) {
    for (const socket of this.sockets.values()) socket.rejectPending(error)
  }
}
