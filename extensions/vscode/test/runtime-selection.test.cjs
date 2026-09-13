const assert = require('node:assert/strict')
const test = require('node:test')

const { deferred, harness } = require('./runtimeHarness.cjs')

test('stale selection reconciliation cannot overwrite a concurrent explicit selection', async () => {
  const { client, host, sockets, state, updates } = harness()
  state.selection = {
    session: 'graph-a',
    graph: 'graph-a',
    creature: 'alpha',
    targetCreatureId: 'creature-alpha',
  }
  const listed = deferred()
  client.listOpen = () => listed.promise
  const reconciling = host.reconcileSelection()
  client.active = async () => ({
    session_id: 'graph-b',
    creatures: [{ creature_id: 'creature-beta', name: 'beta' }],
  })

  const selecting = host.handle({
    type: 'session.select',
    requestId: 20,
    session: 'graph-b',
    creatureId: 'creature-beta',
  })
  listed.resolve([
    {
      runtimeId: 'graph-a-new',
      isLive: true,
      creatures: [{ id: 'creature-alpha', name: 'alpha-new' }],
    },
  ])
  const result = await reconciling
  await selecting

  assert.equal(result.selection.session, 'graph-a-new')
  assert.equal(updates.length, 2)
  assert.equal(state.selection.session, 'graph-b')
  assert.equal(host.generation, 3)
  assert.equal(sockets.beginCount, 3)
})

test('stale missing-selection reconciliation cannot clear a concurrent explicit selection', async () => {
  const { client, host, state, updates } = harness()
  state.selection = {
    session: 'graph-a',
    graph: 'graph-a',
    creature: 'alpha',
    targetCreatureId: 'creature-alpha',
  }
  const listed = deferred()
  client.listOpen = () => listed.promise
  const reconciling = host.reconcileSelection()
  client.active = async () => ({
    session_id: 'graph-b',
    creatures: [{ creature_id: 'creature-beta', name: 'beta' }],
  })

  const selecting = host.handle({
    type: 'session.select',
    requestId: 21,
    session: 'graph-b',
    creatureId: 'creature-beta',
  })
  listed.resolve([])
  const result = await reconciling
  await selecting

  assert.deepEqual(result, { selection: null, changed: true, selectionVersion: 1 })
  assert.equal(updates.length, 2)
  assert.equal(state.selection.session, 'graph-b')
})

test('selection serializes reconcile after a pending explicit selection', async () => {
  const { client, host, posts, sockets, state, updates } = harness()
  state.selection = {
    session: 'graph-a',
    graph: 'graph-a',
    creature: 'alpha',
    targetCreatureId: 'creature-alpha',
  }
  const activated = deferred()
  const listed = deferred()
  client.active = () => activated.promise
  client.listOpen = () => listed.promise

  const selecting = host.handle({
    type: 'session.select',
    requestId: 30,
    session: 'graph-b',
    creatureId: 'creature-beta',
  })
  const reconciling = host.handle({ type: 'session.reconcile', requestId: 31 })
  await Promise.resolve()
  assert.equal(updates.length, 0)

  activated.resolve({
    session_id: 'graph-b',
    creatures: [{ creature_id: 'creature-beta', name: 'beta' }],
  })
  await selecting
  assert.equal(posts.at(-1).requestId, 30)
  assert.equal(state.selection.session, 'graph-b')
  const selectedGeneration = host.generation
  listed.resolve([
    {
      runtimeId: 'graph-b',
      isLive: true,
      creatures: [{ id: 'creature-beta', name: 'beta' }],
    },
  ])
  await reconciling

  assert.equal(posts.at(-1).requestId, 31)
  assert.equal(state.selection.session, 'graph-b')
  assert.equal(host.generation, selectedGeneration)
  assert.equal(sockets.beginCount, selectedGeneration)
  assert.equal(updates.length, 1)
  assert.equal(posts.find((post) => post.requestId === 30).data.selectionVersion, 1)
  assert.equal(posts.find((post) => post.requestId === 31).data.selectionVersion, 1)
})

test('selection serializes reconcile after a pending stop', async () => {
  const { client, host, sockets, state } = harness()
  state.selection = {
    session: 'graph-a',
    graph: 'graph-a',
    creature: 'alpha',
    targetCreatureId: 'creature-alpha',
  }
  const stopped = deferred()
  let listCalls = 0
  client.stop = () => stopped.promise
  client.listOpen = async () => {
    listCalls++
    return [{ runtimeId: 'graph-a', isLive: true, creatures: [{ id: 'creature-alpha', name: 'alpha' }] }]
  }

  const stopping = host.handle({
    type: 'session.stop',
    requestId: 32,
    session: 'graph-a',
    creatureId: 'creature-alpha',
  })
  const reconciling = host.handle({ type: 'session.reconcile', requestId: 33 })
  await Promise.resolve()
  assert.equal(listCalls, 0)
  stopped.resolve({ status: 'stopped' })
  await Promise.all([stopping, reconciling])

  assert.equal(state.selection, null)
  assert.equal(listCalls, 0)
  assert.equal(sockets.beginCount, 2)
})

test('a rejected selection operation does not poison the queue', async () => {
  const { client, host, posts, state } = harness()
  const activated = deferred()
  client.active = () => activated.promise

  const failed = host.handle({
    type: 'session.select',
    requestId: 34,
    session: 'graph-missing',
    creatureId: 'creature-missing',
  })
  const cleared = host.handle({ type: 'session.clearSelection', requestId: 35 })
  activated.reject(Error('active failed'))

  await assert.rejects(failed, /active failed/)
  await cleared
  assert.equal(state.selection, null)
  assert.deepEqual(posts.at(-1), {
    type: 'session.clearSelection.result',
    requestId: 35,
    data: { ok: true, selectionVersion: 0, readyId: 'ready-B' },
  })
})

test('selection reconciliation relocates by stable Creature id and rotates transport ownership', async () => {
  const { client, host, sockets, state } = harness()
  state.selection = {
    session: 'graph-old',
    graph: 'graph-old',
    creature: 'old-name',
    targetCreatureId: 'creature-beta',
  }
  client.listOpen = async () => [
    {
      runtimeId: 'graph-new',
      isLive: true,
      creatures: [{ id: 'creature-beta', name: 'new-name' }],
    },
  ]
  const before = sockets.beginCount

  const result = await host.reconcileSelection()

  assert.deepEqual(result.selection, {
    session: 'graph-new',
    graph: 'graph-new',
    creature: 'new-name',
    targetCreatureId: 'creature-beta',
  })
  assert.deepEqual(state.selection, result.selection)
  assert.equal(sockets.beginCount, before + 1)
})

test('selection reconciliation fails closed when the stable target disappears', async () => {
  const { client, host, sockets, state } = harness()
  state.selection = {
    session: 'graph-old',
    graph: 'graph-old',
    creature: 'beta',
    targetCreatureId: 'creature-beta',
  }
  client.listOpen = async () => [{ runtimeId: 'graph-other', isLive: true, creatures: [{ id: 'creature-alpha', name: 'alpha' }] }]
  const before = sockets.beginCount

  const result = await host.reconcileSelection()

  assert.equal(result.selection, null)
  assert.equal(state.selection, null)
  assert.equal(sockets.beginCount, before + 1)
})

test('selection reconciliation preserves stale ownership when the service is unavailable', async () => {
  const { client, host, sockets, state } = harness()
  state.selection = {
    session: 'graph-stale',
    graph: 'graph-stale',
    creature: 'beta',
    targetCreatureId: 'creature-beta',
  }
  client.listOpen = async () => {
    throw Error('service unavailable')
  }
  const before = sockets.beginCount

  await assert.rejects(host.reconcileSelection(), /service unavailable/)

  assert.deepEqual(state.selection, {
    session: 'graph-stale',
    graph: 'graph-stale',
    creature: 'beta',
    targetCreatureId: 'creature-beta',
  })
  assert.equal(sockets.beginCount, before)
})

test('selection reconciliation keeps ownership stable when the attachment is unchanged', async () => {
  const { client, host, sockets, state } = harness()
  state.selection = {
    session: 'graph-live',
    graph: 'graph-live',
    creature: 'beta',
    targetCreatureId: 'creature-beta',
  }
  client.listOpen = async () => [{ runtimeId: 'graph-live', isLive: true, creatures: [{ id: 'creature-beta', name: 'beta' }] }]
  const before = sockets.beginCount

  const result = await host.reconcileSelection()

  assert.deepEqual(result.selection, state.selection)
  assert.equal(sockets.beginCount, before)
})

test('session.reconcile returns the authoritative selection for explicit recovery', async () => {
  const { client, host, posts, state } = harness()
  state.selection = {
    session: 'graph-old',
    graph: 'graph-old',
    creature: 'old-name',
    targetCreatureId: 'creature-beta',
  }
  client.listOpen = async () => [{ runtimeId: 'graph-new', isLive: true, creatures: [{ id: 'creature-beta', name: 'beta' }] }]

  await host.handle({ type: 'session.reconcile', requestId: 13 })

  assert.deepEqual(posts.at(-1), {
    type: 'session.reconcile.result',
    requestId: 13,
    data: {
      selection: {
        session: 'graph-new',
        graph: 'graph-new',
        creature: 'beta',
        targetCreatureId: 'creature-beta',
      },
      changed: true,
      selectionVersion: 1,
      readyId: 'ready-B',
    },
  })
})

test('history and interrupt always use the persisted selected target', async () => {
  const { client, host, posts, state } = harness()
  const calls = []
  state.selection = {
    session: 'graph-selected',
    graph: 'graph-selected',
    creature: 'beta',
    targetCreatureId: 'creature-beta',
  }
  client.history = async (...args) => {
    calls.push(['history', ...args])
    return { events: [] }
  }
  client.interrupt = async (...args) => {
    calls.push(['interrupt', ...args])
    return { ok: true }
  }

  await host.handle({ type: 'http.history', requestId: 3, session: 'graph-selected', creature: 'beta' })
  await host.handle({ type: 'http.interrupt', requestId: 4, session: 'graph-selected', creature: 'beta' })

  assert.deepEqual(calls, [
    ['history', 'graph-selected', 'beta'],
    ['interrupt', 'graph-selected', 'beta'],
  ])
  assert.equal(posts[0].type, 'http.history.result')
  assert.equal(posts[1].type, 'http.interrupt.result')
})

test('context actions bind fixed commands to the current Host selection', async () => {
  const { client, host, posts, state } = harness()
  state.selection = { session: 'graph-live', creature: 'beta', targetCreatureId: 'creature-beta' }

  await host.handle({ type: 'context.compact', requestId: 50 })
  await host.handle({ type: 'context.clear', requestId: 51 })

  assert.deepEqual(client.commandCalls, [
    { session: 'graph-live', creature: 'beta', command: 'compact', args: '' },
    { session: 'graph-live', creature: 'beta', command: 'clear', args: '--force' },
  ])
  assert.deepEqual(
    posts.map(({ type, requestId }) => ({ type, requestId })),
    [
      { type: 'context.compact.result', requestId: 50 },
      { type: 'context.clear.result', requestId: 51 },
    ],
  )
})

test('context actions preserve the backend response payload exactly', async () => {
  const { client, host, posts, state } = harness()
  const payload = {
    error: 'logical failure',
    notify: { message: 'notice' },
    data: { message: 'detail' },
    output: 'text',
  }
  state.selection = { session: 'graph-live', creature: 'beta', targetCreatureId: 'creature-beta' }
  client.creatureCommand = async () => payload

  await host.handle({ type: 'context.compact', requestId: 53 })

  assert.strictEqual(posts[0].data, payload)
})

test('disposed runtime rejects a queued context command before backend execution', async () => {
  const { client, host, state } = harness()
  state.selection = {
    session: 'graph-a',
    graph: 'graph-a',
    creature: 'alpha',
    targetCreatureId: 'creature-alpha',
  }
  const activated = deferred()
  client.active = () => activated.promise
  const selecting = host.handle({
    type: 'session.select',
    requestId: 59,
    session: 'graph-b',
    creatureId: 'creature-beta',
  })
  const context = host.handle({ type: 'context.compact', requestId: 60 })

  host.dispose()
  activated.resolve({ session_id: 'graph-b', creatures: [{ creature_id: 'creature-beta', name: 'beta' }] })
  await assert.rejects(selecting, /ownership changed/)
  await assert.rejects(context, /ownership changed/)
  assert.equal(client.commandCalls.length, 0)
})

test('context action fails closed when selection ownership changes during request', async () => {
  const { client, host, posts, state } = harness()
  const command = deferred()
  state.selection = { session: 'graph-live', creature: 'beta', targetCreatureId: 'creature-beta' }
  client.creatureCommand = () => command.promise

  const pending = host.handle({ type: 'context.compact', requestId: 52 })
  await Promise.resolve()
  state.selection = { session: 'other', creature: 'other', targetCreatureId: 'other' }
  command.resolve({ ok: true })

  await assert.rejects(pending, /ownership changed/)
  assert.deepEqual(posts, [])
})
