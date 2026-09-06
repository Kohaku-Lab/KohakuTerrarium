const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
let BridgeWebSocket

test.before(async () => {
  const source = fs.readFileSync(path.join(root, 'src', 'webview', 'bridge.js'), 'utf8')
  const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
  ;({ BridgeWebSocket } = await import(moduleUrl))
})

test.beforeEach(() => {
  BridgeWebSocket.sockets = new Map()
  BridgeWebSocket.nextId = 1
  BridgeWebSocket.nextSendId = 1
  BridgeWebSocket.post = () => {}
})

test('observed production Bridge WebSocket sees backend frames before chat store handler', async () => {
  const { createObservedWebSocket } = await import(
    `${pathToFileURL(path.join(root, 'src', 'webview', 'hostAcceptedChat.mjs'))}?t=${Date.now()}`
  )
  const observed = []
  const handled = []
  const ObservedWebSocket = createObservedWebSocket(BridgeWebSocket, (frame) => observed.push(frame))
  const socket = new ObservedWebSocket('ws://bridge')
  socket.onmessage = (event) => handled.push(JSON.parse(event.data))
  BridgeWebSocket.receive({ type: 'ws.opened', socketId: socket.id })

  const first = { type: 'user_input', source: 'worker', event_id: 'event-1' }
  BridgeWebSocket.receive({
    type: 'ws.frame',
    socketId: socket.id,
    data: JSON.stringify(first),
  })
  socket.onmessage = null
  const second = { type: 'processing_start', source: 'worker' }
  BridgeWebSocket.receive({
    type: 'ws.frame',
    socketId: socket.id,
    data: JSON.stringify(second),
  })

  assert.deepEqual(observed, [first, second])
  assert.deepEqual(handled, [first])
})
