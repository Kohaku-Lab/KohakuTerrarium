const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

let BridgeWebSocket

test.before(async () => {
  const source = fs.readFileSync(path.resolve(__dirname, '../src/webview/bridge.js'), 'utf8')
  const moduleUrl = `data:text/javascript;base64,${Buffer.from(`const markRaw = value => value;${source}`).toString('base64')}`
  ;({ BridgeWebSocket } = await import(moduleUrl))
})

test.beforeEach(() => {
  BridgeWebSocket.disposeAll(Error('test reset'))
  BridgeWebSocket.sockets.clear()
  BridgeWebSocket.nextId = 1
  BridgeWebSocket.nextSendId = 1
  BridgeWebSocket.confirmationTimeout = 1000
})

test('matching send ids on different sockets settle only the named socket', async () => {
  const first = new BridgeWebSocket('ws://first')
  const second = new BridgeWebSocket('ws://second')
  first.readyState = BridgeWebSocket.OPEN
  second.readyState = BridgeWebSocket.OPEN
  BridgeWebSocket.nextSendId = 1
  const firstCapture = BridgeWebSocket.captureSend(() => first.send('first'))
  BridgeWebSocket.nextSendId = 1
  const secondCapture = BridgeWebSocket.captureSend(() => second.send('second'))

  BridgeWebSocket.receive({ type: 'ws.send.result', socketId: second.id, sendId: 1 })
  await secondCapture.confirmation
  assert.equal(first.pendingSends.has(1), true)
  firstCapture.cancel(Error('cleanup'))
  await assert.rejects(firstCapture.confirmation, /cleanup/)
})

test('socket errors terminate connecting and closing bridge sockets exactly once', () => {
  const socket = new BridgeWebSocket('ws://bridge')
  let close
  socket.onclose = (event) => (close = event)

  BridgeWebSocket.receive({ type: 'ws.error', socketId: socket.id, error: 'connect failed', code: 'WS_OPEN_FAILED' })

  assert.equal(socket.readyState, BridgeWebSocket.CLOSED)
  assert.equal(BridgeWebSocket.sockets.has(socket.id), false)
  assert.equal(close.code, 1011)

  const closing = new BridgeWebSocket('ws://bridge')
  let closeCount = 0
  closing.onclose = () => closeCount++
  closing.close()
  BridgeWebSocket.receive({ type: 'ws.error', socketId: closing.id, error: 'connect failed' })
  BridgeWebSocket.receive({ type: 'ws.closed', socketId: closing.id, code: 1011 })
  assert.equal(closing.readyState, BridgeWebSocket.CLOSED)
  assert.equal(closeCount, 1)
})

test('captured send retains WebSocket signature and resolves only its correlated Host result', async () => {
  const posts = []
  BridgeWebSocket.post = (message) => posts.push(message)
  const first = new BridgeWebSocket('ws://bridge')
  const second = new BridgeWebSocket('ws://bridge')
  BridgeWebSocket.receive({ type: 'ws.opened', socketId: first.id })
  BridgeWebSocket.receive({ type: 'ws.opened', socketId: second.id })

  const captured = BridgeWebSocket.captureSend(() => first.send('one'))
  const other = BridgeWebSocket.captureSend(() => second.send('two'))
  assert.equal(captured.value, undefined)
  assert.equal(captured.frame, 'one')
  assert.equal(other.frame, 'two')
  assert.equal(posts.at(-2).sendId !== posts.at(-1).sendId, true)

  BridgeWebSocket.receive({
    type: 'ws.send.result',
    socketId: second.id,
    sendId: posts.at(-1).sendId,
  })
  await other.confirmation
  let settled = false
  captured.confirmation.finally(() => (settled = true))
  await Promise.resolve()
  assert.equal(settled, false)

  BridgeWebSocket.receive({
    type: 'ws.send.result',
    socketId: first.id,
    sendId: posts.at(-2).sendId,
  })
  await captured.confirmation
})

test('capturing a send returns the callback error without losing a posted frame', () => {
  BridgeWebSocket.post = () => {}
  const socket = new BridgeWebSocket('ws://bridge')
  BridgeWebSocket.receive({ type: 'ws.opened', socketId: socket.id })

  const captured = BridgeWebSocket.captureSend(() => {
    socket.send('posted')
    throw Error('store failed after send')
  })

  assert.equal(captured.frame, 'posted')
  assert.match(captured.error.message, /store failed/)
})

test('multiple captured frames reject and clear every confirmation immediately', async () => {
  BridgeWebSocket.post = () => {}
  const socket = new BridgeWebSocket('ws://bridge')
  BridgeWebSocket.receive({ type: 'ws.opened', socketId: socket.id })

  assert.throws(
    () =>
      BridgeWebSocket.captureSend(() => {
        socket.send('one')
        socket.send('two')
      }),
    /multiple WebSocket frames/,
  )
  assert.equal(socket.pendingSends.size, 0)
})

test('capturing a send fails when the chat store emits no socket frame', () => {
  assert.throws(
    () =>
      BridgeWebSocket.captureSend(() => undefined, {
        requireConfirmation: true,
      }),
    /did not reach the Host/,
  )
})

test('correlated failure, close, timeout, and disposal reject pending confirmation', async () => {
  BridgeWebSocket.post = () => {}
  const socket = new BridgeWebSocket('ws://bridge')
  BridgeWebSocket.receive({ type: 'ws.opened', socketId: socket.id })
  const failed = BridgeWebSocket.captureSend(() => socket.send('failed'))
  const failedSendId = BridgeWebSocket.nextSendId - 1
  BridgeWebSocket.receive({
    type: 'ws.send.error',
    socketId: socket.id,
    sendId: failedSendId,
    error: 'not open',
  })
  await assert.rejects(failed.confirmation, /not open/)

  const closed = BridgeWebSocket.captureSend(() => socket.send('closed'))
  BridgeWebSocket.receive({ type: 'ws.closed', socketId: socket.id })
  await assert.rejects(closed.confirmation, /closed/i)

  const timeoutSocket = new BridgeWebSocket('ws://bridge')
  BridgeWebSocket.receive({ type: 'ws.opened', socketId: timeoutSocket.id })
  BridgeWebSocket.confirmationTimeout = 1
  const timed = BridgeWebSocket.captureSend(() => timeoutSocket.send('timed'))
  await assert.rejects(timed.confirmation, /timed out/i)

  BridgeWebSocket.confirmationTimeout = 1000
  const disposed = BridgeWebSocket.captureSend(() => timeoutSocket.send('disposed'))
  BridgeWebSocket.disposeAll(Error('disposed'))
  await assert.rejects(disposed.confirmation, /disposed/)
  BridgeWebSocket.receive({
    type: 'ws.send.result',
    socketId: timeoutSocket.id,
    sendId: BridgeWebSocket.nextSendId - 1,
  })
})
