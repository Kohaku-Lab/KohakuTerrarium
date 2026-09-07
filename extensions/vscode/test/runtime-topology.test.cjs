const assert = require('node:assert/strict')
const test = require('node:test')

const { deferred, harness } = require('./runtimeHarness.cjs')

test('topology reconciliations start independently and ignore late older results', async () => {
  const { client, host, state, updates } = harness()
  state.selection = {
    session: 'graph-a',
    graph: 'graph-a',
    creature: 'alpha',
    targetCreatureId: 'creature-alpha',
  }
  const older = deferred()
  let calls = 0
  client.listOpen = () => {
    calls++
    return calls === 1
      ? older.promise
      : Promise.resolve([{ runtimeId: 'graph-new', isLive: true, creatures: [{ id: 'creature-alpha', name: 'alpha-new' }] }])
  }

  const first = host.reconcileTopologySelection()
  const second = host.reconcileTopologySelection()
  assert.equal(calls, 2)
  const result = await second
  older.resolve([{ runtimeId: 'graph-old', isLive: true, creatures: [{ id: 'creature-alpha', name: 'alpha-old' }] }])
  const stale = await first

  assert.equal(result.selection.session, 'graph-new')
  assert.equal(stale.superseded, true)
  assert.equal(state.selection.session, 'graph-new')
  assert.equal(updates.length, 1)
})

test('an admitted topology write completes consistently when a newer read starts', async () => {
  const { client, host, state, updates } = harness()
  state.selection = {
    session: 'graph-a',
    graph: 'graph-a',
    creature: 'alpha',
    targetCreatureId: 'creature-alpha',
  }
  const admitted = deferred()
  const release = deferred()
  let stateTail = Promise.resolve()
  state.updateSelectionIf = (selection, owns) => {
    const operation = stateTail.then(async () => {
      if (!owns()) return false
      if (selection.session === 'graph-old') {
        admitted.resolve()
        await release.promise
      }
      state.selection = selection
      updates.push(selection)
      return true
    })
    stateTail = operation.catch(() => {})
    return operation
  }
  let calls = 0
  client.listOpen = async () => {
    calls++
    const session = calls === 1 ? 'graph-old' : 'graph-new'
    const name = calls === 1 ? 'alpha-old' : 'alpha-new'
    return [{ runtimeId: session, isLive: true, creatures: [{ id: 'creature-alpha', name }] }]
  }

  const older = host.reconcileTopologySelection()
  await admitted.promise
  const newer = host.reconcileTopologySelection()
  release.resolve()
  await Promise.all([older, newer])

  assert.deepEqual(
    updates.map((selection) => selection.session),
    ['graph-old'],
  )
  assert.equal(state.selection.session, 'graph-old')
})

test('topology observes through hung explicit I/O while the explicit intent remains authoritative', async () => {
  const { client, host, state, updates } = harness()
  state.selection = {
    session: 'graph-a',
    graph: 'graph-a',
    creature: 'alpha',
    targetCreatureId: 'creature-alpha',
  }
  const activated = deferred()
  let listCalls = 0
  client.active = () => activated.promise
  client.listOpen = async () => {
    listCalls++
    return [{ runtimeId: 'graph-a-new', isLive: true, creatures: [{ id: 'creature-alpha', name: 'alpha-new' }] }]
  }

  const selecting = host.handle({
    type: 'session.select',
    requestId: 19,
    session: 'graph-b',
    creatureId: 'creature-beta',
  })
  const topology = await host.reconcileTopologySelection()

  assert.equal(topology.superseded, true)
  assert.equal(listCalls, 1)
  assert.equal(updates.length, 0)
  activated.resolve({ session_id: 'graph-b', creatures: [{ creature_id: 'creature-beta', name: 'beta' }] })
  await selecting
  assert.equal(state.selection.session, 'graph-b')
  assert.equal(updates.length, 1)
})

test('explicit selection intent invalidates a topology result before persistence', async () => {
  const { client, host, state, updates } = harness()
  state.selection = {
    session: 'graph-a',
    graph: 'graph-a',
    creature: 'alpha',
    targetCreatureId: 'creature-alpha',
  }
  const listed = deferred()
  const activated = deferred()
  client.listOpen = () => listed.promise
  client.active = () => activated.promise

  const topology = host.reconcileTopologySelection()
  const selecting = host.handle({
    type: 'session.select',
    requestId: 18,
    session: 'graph-b',
    creatureId: 'creature-beta',
  })
  listed.resolve([{ runtimeId: 'graph-a-new', isLive: true, creatures: [{ id: 'creature-alpha', name: 'alpha-new' }] }])
  const stale = await topology

  assert.equal(stale.superseded, true)
  assert.equal(updates.length, 0)
  activated.resolve({ session_id: 'graph-b', creatures: [{ creature_id: 'creature-beta', name: 'beta' }] })
  await selecting
  assert.equal(state.selection.session, 'graph-b')
})

test('unchanged topology result is superseded by a newer explicit intent', async () => {
  const { client, host, state, updates } = harness()
  state.selection = {
    session: 'graph-a',
    graph: 'graph-a',
    creature: 'alpha',
    targetCreatureId: 'creature-alpha',
  }
  const listed = deferred()
  const activated = deferred()
  client.listOpen = () => listed.promise
  client.active = () => activated.promise

  const topology = host.reconcileTopologySelection()
  const selecting = host.handle({
    type: 'session.select',
    requestId: 17,
    session: 'graph-b',
    creatureId: 'creature-beta',
  })
  listed.resolve([{ runtimeId: 'graph-a', isLive: true, creatures: [{ id: 'creature-alpha', name: 'alpha' }] }])
  const stale = await topology

  assert.equal(stale.superseded, true)
  assert.equal(updates.length, 0)
  activated.resolve({ session_id: 'graph-b', creatures: [{ creature_id: 'creature-beta', name: 'beta' }] })
  await selecting
})

test('unchanged topology reconciliation advances the notification version', async () => {
  const { client, host, state } = harness()
  state.selection = {
    session: 'graph-a',
    graph: 'graph-a',
    creature: 'alpha',
    targetCreatureId: 'creature-alpha',
  }
  client.listOpen = async () => [{ runtimeId: 'graph-a', isLive: true, creatures: [{ id: 'creature-alpha', name: 'alpha' }] }]

  const result = await host.reconcileTopologySelection()

  assert.equal(result.changed, false)
  assert.equal(result.selectionVersion, 1)
  assert.equal(host.selectionVersion, 1)
})

test('topology reconciliation times out, aborts, and ignores a late result', async () => {
  const { client, host, state } = harness({ topologyTimeoutMs: 5 })
  state.selection = {
    session: 'graph-a',
    graph: 'graph-a',
    creature: 'alpha',
    targetCreatureId: 'creature-alpha',
  }
  const listed = deferred()
  let signal
  client.listOpen = (options) => {
    signal = options.signal
    return listed.promise
  }

  await assert.rejects(host.reconcileTopologySelection(), /timed out/)
  assert.equal(signal.aborted, true)
  listed.resolve([])
  await Promise.resolve()

  assert.equal(state.selection.session, 'graph-a')
})
