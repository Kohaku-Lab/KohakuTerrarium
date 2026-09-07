const assert = require('node:assert/strict')
const test = require('node:test')
const { harness, deferred } = require('./runtimeHarness.cjs')

test('ready transition retains collaborators but invalidates sockets, capabilities and queued mutations', async () => {
  const { host, state, client } = harness()
  state.selection = { session: 'graph-a', creature: 'alpha', targetCreatureId: 'id-alpha' }
  host.runtimeEpoch = 1
  const original = { client: host.client, state: host.state, sockets: host.sockets }
  const capability = host.acquireContextCommand()
  const generation = host.generation
  const blocked = deferred()
  host.selectionOperationTail = blocked.promise
  const queued = host.handle({ type: 'session.clearSelection', requestId: 5 })
  const rejected = assert.rejects(queued, /ownership/)
  host.beginReady(2)
  blocked.resolve()
  await rejected
  assert.equal(host.client, original.client)
  assert.equal(host.state, original.state)
  assert.equal(host.sockets, original.sockets)
  assert.notEqual(host.generation, generation)
  assert.equal(host.ownsContextCommand(capability), false)
  assert.equal(state.selection.targetCreatureId, 'id-alpha')
  const currentGeneration = host.generation
  host.beginReady(2)
  assert.equal(host.generation, currentGeneration)
  assert.equal(client.commandCalls.length, 0)
})

test('ready reconciliation is bounded even without selection and ignores late data', async () => {
  const { host, client, state } = harness()
  host.topologyTimeoutMs = 5
  const read = deferred()
  let signal
  client.listOpen = (options) => {
    signal = options.signal
    return read.promise
  }
  host.beginReady(1)
  await assert.rejects(host.reconcileReady(1), /timed out/)
  assert.equal(signal.aborted, true)
  read.resolve([])
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(state.selection, null)
  assert.equal(host.readyControllers.size, 0)
})

test('new ready immediately cancels old reconciliation and accepts the latest result', async () => {
  const { host, client } = harness()
  const stale = deferred()
  client.listOpen = () => stale.promise
  host.beginReady(1)
  const older = host.reconcileReady(1)
  const rejected = assert.rejects(older, /ownership/)
  await new Promise((resolve) => setImmediate(resolve))
  host.beginReady(2)
  await rejected
  client.listOpen = async () => []
  assert.equal((await host.reconcileReady(2)).selection, null)
  stale.resolve([])
})

test('old ready socket opens cannot attach to the refreshed runtime', async () => {
  const { host, state } = harness()
  state.selection = { session: 'graph-a', creature: 'alpha', targetCreatureId: 'id-alpha' }
  host.beginReady(2)
  await assert.rejects(host.handle({ type: 'ws.open', socketId: 1, readyId: 1 }), /ownership/)
  await assert.rejects(host.handle({ type: 'ws.open', socketId: 2 }), /ownership/)
})

test('selection intent supersedes ready reconciliation without treating the connection as failed', async () => {
  const { host, client } = harness()
  const read = deferred()
  client.listOpen = () => read.promise
  host.beginReady(1)
  const ready = host.reconcileReady(1)
  await new Promise((resolve) => setImmediate(resolve))
  const clear = host.clearSelection()
  read.resolve([])
  assert.equal((await ready).superseded, true)
  await clear
  assert.equal(host.disposed, false)
})

test('late admitted persistence cannot rotate new-ready transports after refresh', async () => {
  const { host, state } = harness()
  state.selection = { session: 'graph-a', creature: 'alpha', targetCreatureId: 'id-alpha' }
  host.beginReady(1)
  const persist = deferred()
  state.updateSelection = async () => {
    await persist.promise
    state.selection = null
  }
  const clearing = host.clearSelection()
  const rejected = assert.rejects(clearing, /ownership/)
  await new Promise((resolve) => setImmediate(resolve))
  host.beginReady(2)
  const generation = host.generation
  persist.resolve()
  await rejected
  assert.equal(host.generation, generation)
})

test('queued ready timeout never resets serialization or persists after queue release', async () => {
  const { host, client } = harness()
  host.topologyTimeoutMs = 5
  let reads = 0
  client.listOpen = async () => {
    reads++
    return []
  }
  const block = deferred()
  host.selectionOperationTail = block.promise
  host.beginReady(1)
  await assert.rejects(host.reconcileReady(1), /timed out/)
  block.resolve()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(reads, 0)
})
