const assert = require('node:assert/strict')
const test = require('node:test')

const { TopologyWatcher } = require('../src/host/topology.cjs')

function fakeSocket() {
  return {
    closeCount: 0,
    close() {
      this.closeCount++
    },
  }
}

function flushImmediate() {
  return new Promise((resolve) => setImmediate(resolve))
}

function deferred() {
  let resolve
  const promise = new Promise((onResolve) => (resolve = onResolve))
  return { promise, resolve }
}

function controlledTimers() {
  const scheduled = []
  return {
    scheduled,
    clearTimer(timer) {
      timer.cleared = true
    },
    async runNext() {
      let timer
      while ((timer = scheduled.shift())?.cleared) {}
      if (timer) timer.callback()
      await flushImmediate()
    },
    setTimer(callback, delay) {
      const timer = { callback, delay, cleared: false }
      scheduled.push(timer)
      return timer
    },
  }
}

test('topology watcher filters frames and coalesces invalidations', async () => {
  const calls = []
  const socket = fakeSocket()
  const watcher = new TopologyWatcher({
    socketFactory: () => socket,
    endpoint: 'http://127.0.0.1:8000',
    token: 'secret',
    onInvalidate: async (frame) => calls.push(frame.type),
  })

  watcher.start()
  socket.onmessage({ data: Buffer.from('null') })
  socket.onmessage({ data: Buffer.from(JSON.stringify({ type: 'turn_completed' })) })
  socket.onmessage({ data: Buffer.from(JSON.stringify({ type: 'topology_changed' })) })
  socket.onmessage({ data: Buffer.from(JSON.stringify({ type: 'creature_stopped' })) })
  socket.onmessage({ data: Buffer.from('not-json') })
  await new Promise((resolve) => setImmediate(resolve))

  assert.deepEqual(calls, ['topology_changed', 'creature_stopped'])
})

test('topology reconciliation failure closes and retries the current socket', async () => {
  const sockets = []
  const timers = controlledTimers()
  let calls = 0
  const watcher = new TopologyWatcher({
    socketFactory: () => {
      const socket = fakeSocket()
      sockets.push(socket)
      return socket
    },
    endpoint: 'http://127.0.0.1:8000',
    token: 'secret',
    onInvalidate: async () => {
      calls++
      if (calls === 1) throw Error('reconcile failed')
    },
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  })

  watcher.start()
  sockets[0].onmessage({ data: JSON.stringify({ type: 'topology_changed' }) })
  await flushImmediate()
  assert.equal(sockets[0].closeCount, 1)
  assert.equal(timers.scheduled.filter((timer) => !timer.cleared).length, 1)

  await timers.runNext()
  sockets[1].onopen()
  await flushImmediate()
  assert.equal(calls, 2, 'reconnect performs one catch-up reconciliation')
})

test('topology watcher ignores late frames from a replaced socket', async () => {
  const calls = []
  const sockets = []
  const watcher = new TopologyWatcher({
    socketFactory: () => {
      const socket = fakeSocket()
      sockets.push(socket)
      return socket
    },
    endpoint: 'http://127.0.0.1:8000',
    token: 'secret',
    onInvalidate: async (frame) => calls.push(frame.marker),
  })

  watcher.start()
  watcher.start()
  sockets[0].onmessage({ data: JSON.stringify({ type: 'topology_changed', marker: 'stale' }) })
  sockets[1].onmessage({ data: JSON.stringify({ type: 'topology_changed', marker: 'current' }) })
  await new Promise((resolve) => setImmediate(resolve))

  assert.deepEqual(calls, ['current'])
})

test('topology watcher retries a socket that never opens', async () => {
  const sockets = []
  const timers = controlledTimers()
  const watcher = new TopologyWatcher({
    socketFactory: () => {
      const socket = fakeSocket()
      sockets.push(socket)
      return socket
    },
    endpoint: 'http://127.0.0.1:8000',
    token: 'secret',
    onInvalidate: async () => {},
    openTimeoutMs: 25,
    initialRetryMs: 10,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  })

  watcher.start()
  assert.equal(timers.scheduled[0].delay, 25)
  await timers.runNext()
  assert.equal(sockets[0].closeCount, 1)
  assert.equal(timers.scheduled[0].delay, 10)
  await timers.runNext()
  assert.equal(sockets.length, 2)
})

test('topology watcher retries once per failed socket with bounded backoff', async () => {
  const sockets = []
  const timers = controlledTimers()
  const watcher = new TopologyWatcher({
    socketFactory: () => {
      const socket = fakeSocket()
      sockets.push(socket)
      return socket
    },
    endpoint: 'http://127.0.0.1:8000',
    token: 'secret',
    onInvalidate: async () => {},
    initialRetryMs: 10,
    maxRetryMs: 20,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  })

  watcher.start()
  sockets[0].onerror({ message: 'connection refused' })
  sockets[0].onclose({ code: 1006 })
  assert.deepEqual(
    timers.scheduled.filter((timer) => !timer.cleared).map((timer) => timer.delay),
    [10],
  )

  await timers.runNext()
  sockets[1].onclose({ code: 1006 })
  assert.deepEqual(
    timers.scheduled.filter((timer) => !timer.cleared).map((timer) => timer.delay),
    [20],
  )
  await timers.runNext()
  sockets[2].onopen()
  sockets[2].onclose({ code: 1006 })
  assert.deepEqual(
    timers.scheduled.filter((timer) => !timer.cleared).map((timer) => timer.delay),
    [10],
  )
})

test('synchronous socket factory failures stay inside bounded recovery', async () => {
  const timers = controlledTimers()
  let attempts = 0
  const watcher = new TopologyWatcher({
    socketFactory: () => {
      attempts++
      throw Error('invalid websocket options')
    },
    endpoint: 'http://127.0.0.1:8000',
    token: 'secret',
    onInvalidate: async () => {},
    initialRetryMs: 10,
    maxRetryMs: 20,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  })

  assert.doesNotThrow(() => watcher.start())
  assert.equal(attempts, 1)
  assert.equal(timers.scheduled[0].delay, 10)
  await timers.runNext()
  assert.equal(attempts, 2)
  assert.equal(timers.scheduled[0].delay, 20)
})

test('recovered socket invalidations are not blocked by a hung old generation', async () => {
  const sockets = []
  const timers = controlledTimers()
  const blocked = deferred()
  const calls = []
  const watcher = new TopologyWatcher({
    socketFactory: () => {
      const socket = fakeSocket()
      sockets.push(socket)
      return socket
    },
    endpoint: 'http://127.0.0.1:8000',
    token: 'secret',
    onInvalidate: async (frame) => {
      calls.push(frame.marker)
      if (frame.marker === 'old') await blocked.promise
    },
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  })

  watcher.start()
  sockets[0].onmessage({ data: JSON.stringify({ type: 'topology_changed', marker: 'old' }) })
  await Promise.resolve()
  sockets[0].onclose({ code: 1006 })
  await timers.runNext()
  sockets[1].onopen()
  sockets[1].onmessage({ data: JSON.stringify({ type: 'topology_changed', marker: 'new' }) })
  await new Promise((resolve) => setImmediate(resolve))

  assert.deepEqual(calls, ['old', undefined, 'new'])
  blocked.resolve()
})

test('synchronous timer callbacks do not recurse through permanent factory failures', async () => {
  let attempts = 0
  const watcher = new TopologyWatcher({
    socketFactory: () => {
      attempts++
      throw Error('permanent failure')
    },
    endpoint: 'http://127.0.0.1:8000',
    token: 'secret',
    onInvalidate: async () => {},
    setTimer: (callback) => {
      callback()
      return { synchronous: true }
    },
    clearTimer: () => {},
  })

  assert.doesNotThrow(() => watcher.start())
  await flushImmediate()
  assert.equal(attempts, 2)
  watcher.close()
})

test('same-socket invalidations coalesce to one latest pending frame', async () => {
  const socket = fakeSocket()
  const blocked = deferred()
  const calls = []
  const watcher = new TopologyWatcher({
    socketFactory: () => socket,
    endpoint: 'http://127.0.0.1:8000',
    token: 'secret',
    onInvalidate: async (frame) => {
      calls.push(frame.marker)
      if (frame.marker === 'first') await blocked.promise
    },
  })

  watcher.start()
  for (const marker of ['first', 'second', 'latest']) {
    socket.onmessage({ data: JSON.stringify({ type: 'topology_changed', marker }) })
  }
  await Promise.resolve()
  blocked.resolve()
  await flushImmediate()

  assert.deepEqual(calls, ['first', 'latest'])
})

test('malformed socket factories and close errors remain recoverable', async () => {
  const timers = controlledTimers()
  let attempts = 0
  const throwingClose = fakeSocket()
  throwingClose.close = () => {
    throw Error('close failed')
  }
  const watcher = new TopologyWatcher({
    socketFactory: () => {
      attempts++
      return attempts === 1 ? null : throwingClose
    },
    endpoint: 'http://127.0.0.1:8000',
    token: 'secret',
    onInvalidate: async () => {},
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  })

  assert.doesNotThrow(() => watcher.start())
  assert.equal(timers.scheduled.filter((timer) => !timer.cleared).length, 1)
  await timers.runNext()
  assert.doesNotThrow(() => throwingClose.onerror({ message: 'failed' }))
  assert.equal(timers.scheduled.filter((timer) => !timer.cleared).length, 1)
  assert.doesNotThrow(() => watcher.close())
})

test('timer and socket adapters cannot abort watcher cleanup', () => {
  const socket = fakeSocket()
  const throwingGetter = new Proxy(
    {},
    {
      get() {
        throw Error('socket getter failed')
      },
    },
  )
  let attempt = 0
  Object.defineProperty(socket, 'onopen', {
    set() {
      throw Error('handler failed')
    },
  })
  const watcher = new TopologyWatcher({
    socketFactory: () => (++attempt === 1 ? throwingGetter : socket),
    endpoint: 'http://127.0.0.1:8000',
    token: 'secret',
    onInvalidate: async () => {},
    setTimer: () => {
      throw Error('timer failed')
    },
    clearTimer: () => {
      throw Error('clear failed')
    },
  })

  assert.doesNotThrow(() => watcher.start())
  assert.doesNotThrow(() => watcher.close())
})

test('stale retry callback cannot replace current timer ownership', () => {
  const sockets = []
  const timers = controlledTimers()
  const watcher = new TopologyWatcher({
    socketFactory: () => {
      const socket = fakeSocket()
      sockets.push(socket)
      return socket
    },
    endpoint: 'http://127.0.0.1:8000',
    token: 'secret',
    onInvalidate: async () => {},
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  })

  watcher.start()
  sockets[0].onclose({ code: 1006 })
  const staleRetry = timers.scheduled[0].callback
  watcher.start()
  sockets[1].onclose({ code: 1006 })
  const currentTimer = timers.scheduled[1]
  staleRetry()
  watcher.close()

  assert.equal(currentTimer.cleared, true)
})

test('closing topology watcher cancels retries and stale retry callbacks', () => {
  const sockets = []
  const timers = controlledTimers()
  const watcher = new TopologyWatcher({
    socketFactory: () => {
      const socket = fakeSocket()
      sockets.push(socket)
      return socket
    },
    endpoint: 'http://127.0.0.1:8000',
    token: 'secret',
    onInvalidate: async () => {},
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  })

  watcher.start()
  sockets[0].onclose({ code: 1006 })
  const staleRetry = timers.scheduled[0].callback
  watcher.close()
  staleRetry()

  assert.equal(sockets.length, 1)
  assert.equal(sockets[0].closeCount, 1)
})
