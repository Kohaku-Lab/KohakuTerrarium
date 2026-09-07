const assert = require('node:assert/strict')
const test = require('node:test')

const { SocketOwners } = require('../src/host/sockets.cjs')

function fakeSocket() {
  return {
    OPEN: 1,
    readyState: 1,
    sent: [],
    closeCount: 0,
    send(data, callback) {
      this.sent.push(data)
      callback?.()
    },
    close() {
      this.closeCount++
    },
  }
}

test('send waits for one owned OPEN socket callback and rejects write races', async () => {
  const owners = new SocketOwners()
  const generation = owners.begin()
  const callbacks = []
  const socket = fakeSocket()
  socket.send = (data, callback) => {
    socket.sent.push(data)
    callbacks.push(callback)
  }
  owners.open(generation, 8, () => socket, { postMessage: () => {} })

  const accepted = owners.send(generation, 8, 'first')
  let settled = false
  accepted.finally(() => (settled = true))
  await Promise.resolve()
  assert.equal(settled, false)
  callbacks[0]()
  callbacks[0](Error('duplicate'))
  assert.equal(await accepted, true)

  socket.readyState = 0
  assert.equal(await owners.send(generation, 8, 'not-open'), false)
  socket.readyState = socket.OPEN
  socket.send = () => {
    throw Error('write threw')
  }
  await assert.rejects(owners.send(generation, 8, 'throw'), /write threw/)

  socket.send = (_data, callback) => callback(Error('write failed'))
  await assert.rejects(owners.send(generation, 8, 'failed'), /write failed/)
})

test('write timeout settles and releases a callback that never arrives', async () => {
  const owners = new SocketOwners({ writeTimeoutMs: 5 })
  const generation = owners.begin()
  const socket = fakeSocket()
  socket.send = () => {}
  owners.open(generation, 12, () => socket, { postMessage: () => {} })

  await assert.rejects(owners.send(generation, 12, 'frame'), /timed out/)
  assert.equal(owners.pending.size, 0)
})

test('socket teardown settles pending sends before a withheld callback arrives', async () => {
  const owners = new SocketOwners()
  const generation = owners.begin()
  const first = fakeSocket()
  let callback
  first.send = (_data, complete) => (callback = complete)
  owners.open(generation, 2, () => first, { postMessage: () => {} })
  const pending = owners.send(generation, 2, 'frame')

  owners.begin()
  assert.equal(await pending, false)
  callback()
})

test('same-id socket replacement settles the previous owner pending sends', async () => {
  const owners = new SocketOwners()
  const generation = owners.begin()
  const first = fakeSocket()
  first.send = () => {}
  owners.open(generation, 2, () => first, { postMessage: () => {} })
  const pending = owners.send(generation, 2, 'frame')

  owners.open(generation, 2, fakeSocket, { postMessage: () => {} })

  assert.equal(await pending, false)
})

test('stale generation opens and closes emit a terminal close envelope', () => {
  const messages = []
  const owners = new SocketOwners()
  const generation = owners.begin()
  const view = { postMessage: (message) => messages.push(message) }

  assert.equal(owners.open(generation - 1, 9, fakeSocket, view), null)
  assert.equal(owners.closeSocket(generation - 1, 10, view), false)
  assert.deepEqual(messages, [
    { type: 'ws.closed', socketId: 9, code: 1008 },
    { type: 'ws.closed', socketId: 10, code: 1008 },
  ])
})

test('socket owners ignore stale callbacks and preserve a reused id owner', async () => {
  const messages = []
  const owners = new SocketOwners()
  const generation = owners.begin()
  const first = fakeSocket()
  const second = fakeSocket()

  owners.open(generation, 1, () => first, { postMessage: (message) => messages.push(message) })
  owners.open(generation, 1, () => second, { postMessage: (message) => messages.push(message) })
  first.onopen()
  first.onmessage({ data: 'stale' })
  first.onclose({ code: 1000 })
  second.onopen()
  second.onmessage({ data: Buffer.from('current') })

  assert.equal(first.closeCount, 1)
  assert.deepEqual(messages, [
    { type: 'ws.opened', socketId: 1 },
    { type: 'ws.frame', socketId: 1, data: 'current' },
  ])
  assert.equal(await owners.send(generation, 1, 'input'), true)
  assert.deepEqual(second.sent, ['input'])
})

test('closing a generation notifies the Bridge, closes sockets, and rejects future sends', async () => {
  const messages = []
  const owners = new SocketOwners()
  const generation = owners.begin()
  const socket = fakeSocket()
  owners.open(generation, 3, () => socket, { postMessage: (message) => messages.push(message) })

  owners.closeGeneration(generation)

  assert.deepEqual(messages, [{ type: 'ws.closed', socketId: 3, code: 1000 }])
  assert.equal(socket.closeCount, 1)
  assert.equal(await owners.send(generation, 3, 'input'), false)
})

test('asynchronous socket errors emit error and close exactly once', async () => {
  const messages = []
  const owners = new SocketOwners()
  const generation = owners.begin()
  const socket = fakeSocket()
  owners.open(generation, 6, () => socket, { postMessage: (message) => messages.push(message) })

  socket.onerror()
  socket.onclose?.({ code: 1006 })

  assert.deepEqual(messages, [
    { type: 'ws.error', socketId: 6 },
    { type: 'ws.closed', socketId: 6, code: 1011 },
  ])
  assert.equal(await owners.send(generation, 6, 'input'), false)
})

test('failed socket creation emits a deterministic error and close frame', () => {
  const messages = []
  const owners = new SocketOwners()
  const generation = owners.begin()

  assert.throws(
    () =>
      owners.open(
        generation,
        4,
        () => {
          throw Error('connect failed')
        },
        { postMessage: (message) => messages.push(message) },
      ),
    /connect failed/,
  )

  assert.deepEqual(messages, [{ type: 'ws.closed', socketId: 4, code: 1011 }])
})

test('local close without an owner still emits a terminal close frame', () => {
  const messages = []
  const owners = new SocketOwners()
  const generation = owners.begin()

  assert.equal(owners.closeSocket(generation, 7, { postMessage: (message) => messages.push(message) }), false)
  assert.deepEqual(messages, [{ type: 'ws.closed', socketId: 7, code: 1000 }])
})

test('local close emits a deterministic close frame before releasing ownership', async () => {
  const messages = []
  const owners = new SocketOwners()
  const generation = owners.begin()
  const socket = fakeSocket()
  owners.open(generation, 5, () => socket, { postMessage: (message) => messages.push(message) })

  assert.equal(owners.closeSocket(generation, 5), true)

  assert.deepEqual(messages, [{ type: 'ws.closed', socketId: 5, code: 1000 }])
  assert.equal(await owners.send(generation, 5, 'input'), false)
})
