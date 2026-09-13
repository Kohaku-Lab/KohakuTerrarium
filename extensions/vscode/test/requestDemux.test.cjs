const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const root = path.resolve(__dirname, '..')

async function helpers() {
  return import(pathToFileURL(path.join(root, 'src', 'webview', 'requestDemux.mjs')))
}

function pendingRequest() {
  let resolution
  let rejection
  return {
    entry: {
      timer: setTimeout(() => {}, 10000),
      resolve: (value) => (resolution = value),
      reject: (error) => (rejection = error),
    },
    outcome: () => ({ resolution, rejection }),
  }
}

test('request demux ignores malformed host messages', async () => {
  const { settleRequestMessage } = await helpers()
  assert.equal(settleRequestMessage(new Map(), null), false)
  assert.equal(settleRequestMessage(new Map(), 'message'), false)
  assert.equal(settleRequestMessage(new Map(), []), false)
})

test('request demux ignores socket envelopes with the same numeric identifier', async () => {
  const { settleRequestMessage } = await helpers()
  const pending = new Map()
  const request = pendingRequest()
  request.entry.type = 'session.list'
  pending.set(7, request.entry)

  assert.equal(settleRequestMessage(pending, { type: 'ws.opened', socketId: 7 }), false)
  assert.equal(pending.has(7), true)
  assert.deepEqual(request.outcome(), { resolution: undefined, rejection: undefined })

  assert.equal(settleRequestMessage(pending, { type: 'session.list.result', requestId: 7, data: ['session'] }), true)
  assert.equal(pending.has(7), false)
  assert.deepEqual(request.outcome(), { resolution: ['session'], rejection: undefined })
})

test('request demux requires the expected result type for the pending request', async () => {
  const { settleRequestMessage } = await helpers()
  const pending = new Map()
  const request = pendingRequest()
  request.entry.type = 'session.list'
  pending.set(11, request.entry)

  assert.equal(settleRequestMessage(pending, { type: 'session.create.result', requestId: 11, data: {} }), false)
  assert.equal(settleRequestMessage(pending, { type: 'session.list.result', requestId: 11, socketId: 11, data: [] }), false)
  assert.equal(settleRequestMessage(pending, { type: 'session.list.result', requestId: 11 }), false)
  assert.equal(settleRequestMessage(pending, { type: 'error', requestId: 11, error: { message: 'failed' } }), false)
  assert.equal(pending.has(11), true)
  assert.equal(settleRequestMessage(pending, { type: 'error', requestId: 11, error: 'failed' }), true)
  assert.match(request.outcome().rejection.message, /failed/)
})
