const assert = require('node:assert/strict')
const test = require('node:test')
const http = require('node:http')
const { createClient } = require('../src/host/client.cjs')

const { allowedMessage } = require('../src/host/protocol.cjs')
const { deferred, harness } = require('./runtimeHarness.cjs')

const envelope = { type: 'goal.execute', requestId: 11, readyId: 10, selectionVersion: 0, args: 'list' }
function setup() {
  const result = harness()
  result.host.runtimeEpoch = 10
  result.state.selection = { session: 'graph-a', graph: 'graph-a', creature: 'alpha', targetCreatureId: 'id-alpha' }
  return result
}

test('Host goal sends fixed authenticated HTTP command and aborts a stalled body', async (t) => {
  const calls = []
  let hold = false
  let bodyClosed = false
  const server = http.createServer(async (request, response) => {
    let body = ''
    for await (const chunk of request) body += chunk
    calls.push({ url: request.url, method: request.method, token: request.headers['x-kt-host-token'], body: JSON.parse(body) })
    response.writeHead(200, { 'Content-Type': 'application/json' })
    if (hold) {
      response.write('{')
      response.on('close', () => {
        bodyClosed = true
      })
    } else response.end(JSON.stringify({ command: 'goal', success: true, output: 'live goal result', error: '' }))
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  t.after(async () => {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
  })
  const { host, posts } = setup()
  host.client = createClient({ endpoint: `http://127.0.0.1:${server.address().port}`, token: 'host-secret' })
  await host.handle(envelope)
  assert.deepEqual(calls[0], {
    url: '/api/sessions/graph-a/creatures/id-alpha/command',
    method: 'POST',
    token: 'host-secret',
    body: { command: 'goal', args: 'list' },
  })
  assert.equal(posts[0].data.output, 'live goal result')
  hold = true
  host.goalTimeoutMs = 100
  await assert.rejects(host.handle({ ...envelope, requestId: 12 }), /timed out/)
  for (let attempt = 0; attempt < 50 && !bodyClosed; attempt++) await new Promise((resolve) => setTimeout(resolve, 10))
  assert.equal(bodyClosed, true)
  assert.equal(posts.length, 1)
  await host.clearSelection()
})

test('goal protocol accepts only args and ownership fences, never a client-selected command or target', () => {
  assert.equal(allowedMessage(envelope), true)
  assert.equal(allowedMessage({ ...envelope, args: '' }), true)
  for (const fields of [
    { command: 'clear' },
    { session: 'other' },
    { creature: 'other' },
    { creatureId: 'id-other' },
    { args: {} },
    { args: null },
    { readyId: null },
    { selectionVersion: -1 },
    { sendId: 1 },
    { id: 1 },
  ])
    assert.equal(allowedMessage({ ...envelope, ...fields }), false, JSON.stringify(fields))
})

test('goal execution uses Host selection and waits for backend command result', async () => {
  const { host, client, posts } = setup()
  const response = deferred()
  const calls = []
  client.creatureCommand = (...args) => {
    calls.push(args.slice(0, 4))
    return response.promise
  }
  const pending = host.handle(envelope)
  await new Promise((resolve) => setImmediate(resolve))
  assert.deepEqual(calls, [['graph-a', 'id-alpha', 'goal', 'list']])
  assert.deepEqual(posts, [])
  const data = { command: 'goal', success: true, output: 'Goal list', error: '' }
  response.resolve(data)
  await pending
  assert.deepEqual(posts, [{ type: 'goal.execute.result', requestId: 11, data }])
})

test('goal execution rejects absent, stale, disposed and superseded selection without backend effects', async () => {
  const { host, client, state } = setup()
  await assert.rejects(host.handle({ ...envelope, readyId: 9 }), /ownership/)
  await assert.rejects(host.handle({ ...envelope, selectionVersion: 1 }), /ownership/)
  const blocked = deferred()
  host.selectionOperationTail = blocked.promise
  const pending = host.handle(envelope)
  const rejected = assert.rejects(pending, /ownership/)
  state.selection = { ...state.selection, targetCreatureId: 'other' }
  blocked.resolve()
  await rejected
  state.selection = null
  await assert.rejects(host.handle(envelope))
  host.dispose()
  await assert.rejects(host.handle(envelope))
  assert.equal(client.commandCalls.length, 0)
})

test('timed-out queued goal cannot execute later and an admitted timeout aborts the request', async () => {
  const { host, client } = setup()
  host.goalTimeoutMs = 5
  const blocked = deferred()
  host.selectionOperationTail = blocked.promise
  await assert.rejects(host.handle(envelope), /timed out/)
  blocked.resolve()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(client.commandCalls.length, 0)
  let signal
  client.creatureCommand = (_session, _creature, _command, _args, options) => {
    signal = options.signal
    return new Promise(() => {})
  }
  await assert.rejects(host.handle(envelope), /timed out/)
  assert.equal(signal.aborted, true)
  await host.clearSelection()
})

test('disposing a runtime cancels its pending goal request', async () => {
  const { host, client } = setup()
  let signal
  client.creatureCommand = (_session, _creature, _command, _args, options) => {
    signal = options.signal
    return new Promise(() => {})
  }
  const pending = host.handle(envelope)
  const rejected = assert.rejects(pending, /disposed/)
  await new Promise((resolve) => setImmediate(resolve))
  host.dispose()
  await rejected
  assert.equal(signal.aborted, true)
})

test('a newly queued explicit selection intent invalidates a waiting goal dispatch', async () => {
  const { host, client } = setup()
  const blocked = deferred()
  host.selectionOperationTail = blocked.promise
  const goal = host.handle(envelope)
  const rejected = assert.rejects(goal, /ownership/)
  const clear = host.clearSelection()
  blocked.resolve()
  await rejected
  await clear
  assert.equal(client.commandCalls.length, 0)
})
