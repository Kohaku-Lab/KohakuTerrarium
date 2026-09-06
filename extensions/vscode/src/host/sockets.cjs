class SocketOwners {
  constructor({ writeTimeoutMs = 30_000 } = {}) {
    this.sockets = new Map()
    this.generation = 0
    this.pending = new Map()
    this.writeTimeoutMs = writeTimeoutMs
  }

  begin() {
    this.close()
    return this.generation
  }

  open(generation, socketId, factory, view) {
    if (generation !== this.generation) {
      view.postMessage({ type: 'ws.closed', socketId, code: 1008 })
      return null
    }
    const previous = this.sockets.get(socketId)
    if (previous) {
      this.settlePending(previous, false)
      previous.close()
    }
    let socket
    try {
      socket = factory()
    } catch (error) {
      view.postMessage({ type: 'ws.closed', socketId, code: 1011 })
      throw error
    }
    this.sockets.set(socketId, socket)
    const owned = () => this.generation === generation && this.sockets.get(socketId) === socket
    socket.onopen = () => owned() && view.postMessage({ type: 'ws.opened', socketId })
    socket.onmessage = (event) => owned() && view.postMessage({ type: 'ws.frame', socketId, data: String(event.data) })
    socket.onerror = () => {
      if (!owned()) return
      view.postMessage({ type: 'ws.error', socketId })
      this.settlePending(socket, false)
      this.sockets.delete(socketId)
      view.postMessage({ type: 'ws.closed', socketId, code: 1011 })
      socket.close()
    }
    socket.onclose = (event) => {
      if (!owned()) return
      this.settlePending(socket, false)
      this.sockets.delete(socketId)
      view.postMessage({ type: 'ws.closed', socketId, code: event.code })
    }
    return socket
  }

  send(generation, id, data) {
    if (generation !== this.generation) return Promise.resolve(false)
    const socket = this.sockets.get(id)
    if (!socket || socket.readyState !== socket.OPEN) return Promise.resolve(false)
    return new Promise((resolve, reject) => {
      let timeout
      const complete = (error, accepted = null) => {
        const callbacks = this.pending.get(socket)
        if (!callbacks?.delete(complete)) return
        clearTimeout(timeout)
        if (callbacks.size === 0) this.pending.delete(socket)
        if (error) reject(error)
        else resolve(accepted ?? (this.generation === generation && this.sockets.get(id) === socket))
      }
      let callbacks = this.pending.get(socket)
      if (!callbacks) {
        callbacks = new Set()
        this.pending.set(socket, callbacks)
      }
      callbacks.add(complete)
      timeout = setTimeout(() => complete(Error('Chat socket write timed out')), this.writeTimeoutMs)
      try {
        socket.send(data, complete)
      } catch (error) {
        complete(error)
      }
    })
  }

  closeSocket(generation, id, view = null) {
    if (generation !== this.generation) {
      view?.postMessage({ type: 'ws.closed', socketId: id, code: 1008 })
      return false
    }
    const socket = this.sockets.get(id)
    if (!socket) {
      view?.postMessage({ type: 'ws.closed', socketId: id, code: 1000 })
      return false
    }
    socket.onclose?.({ code: 1000 })
    socket.close()
    return true
  }

  closeGeneration(generation) {
    if (generation !== this.generation) return false
    this.close()
    return true
  }

  close() {
    for (const socket of [...this.sockets.values()]) {
      this.settlePending(socket, false)
      socket.onclose?.({ code: 1000 })
      socket.close()
    }
    this.sockets.clear()
    this.generation++
  }

  settlePending(socket, accepted) {
    for (const complete of [...(this.pending.get(socket) || [])]) complete(null, accepted)
  }
}

module.exports = { SocketOwners }
