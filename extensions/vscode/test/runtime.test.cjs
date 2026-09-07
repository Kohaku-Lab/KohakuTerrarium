const assert = require('node:assert/strict')
const test = require('node:test')

const { harness } = require('./runtimeHarness.cjs')

test('session.create uses Host-owned config and workspace then returns a normalized live session', async () => {
  const { client, host, posts } = harness()

  await host.handle({ type: 'session.create', requestId: 1 })

  assert.equal(posts[0].type, 'session.create.result')
  assert.deepEqual(client.createdPayload, {
    configPath: '@kt-biome/creatures/swe',
    pwd: 'C:/workspace',
    name: 'VS Code Session',
  })
  assert.equal(posts[0].data.runtimeId, 'graph-created')
  assert.deepEqual(posts[0].data.creatures, [{ id: 'creature-created', name: 'swe' }])
  assert.equal(posts[0].data.createdPayload, undefined)
})

test('session.select validates the active runtime, persists stable identity, and resets socket ownership', async () => {
  const { host, posts, sockets, state } = harness()

  await host.handle({ type: 'session.select', requestId: 2, session: 'graph-live', creatureId: 'creature-beta' })

  assert.equal(sockets.beginCount, 2)
  assert.deepEqual(state.selection, {
    session: 'graph-live',
    graph: 'graph-live',
    creature: 'beta',
    targetCreatureId: 'creature-beta',
  })
  assert.deepEqual(posts[0], {
    type: 'session.select.result',
    requestId: 2,
    data: { ...state.selection, selectionVersion: 1, readyId: 'ready-B' },
  })
})

test('session.select preserves the old socket generation when persistence fails', async () => {
  const { host, sockets, state } = harness()
  state.updateSelection = async () => {
    throw Error('persistence failed')
  }
  const before = sockets.beginCount

  await assert.rejects(
    host.handle({ type: 'session.select', requestId: 8, session: 'graph-live', creatureId: 'creature-beta' }),
    /persistence failed/,
  )

  assert.equal(sockets.beginCount, before)
})

test('ws.send emits a correlated result only after the socket write callback accepts the frame', async () => {
  const { host, posts, sockets } = harness()
  const calls = []
  let accept
  sockets.send = (...args) => {
    calls.push(args.slice(0, 3))
    return new Promise((resolve) => {
      accept = resolve
    })
  }

  const pending = host.handle({ type: 'ws.send', readyId: 'ready-B', socketId: 7, sendId: 42, data: 'frame' })
  await Promise.resolve()
  assert.equal(posts.length, 0)
  accept(true)
  await pending

  assert.deepEqual(calls, [[host.generation, 7, 'frame']])
  assert.deepEqual(posts.at(-1), {
    type: 'ws.send.result',
    socketId: 7,
    sendId: 42,
    readyId: 'ready-B',
  })
})

test('ws.send rejection throws without emitting a success result', async () => {
  const { host, posts, sockets } = harness()
  sockets.send = async () => false

  await assert.rejects(host.handle({ type: 'ws.send', readyId: 'ready-B', socketId: 7, sendId: 43, data: 'frame' }), /not open/)
  assert.equal(
    posts.some((post) => post.type === 'ws.send.result'),
    false,
  )
})

test('ws.open omits the token subprotocol for loopback-bypass mode', async () => {
  const { host, socketCalls, state } = harness()
  state.selection = {
    session: 'graph-live',
    graph: 'graph-live',
    creature: 'beta',
    targetCreatureId: 'creature-beta',
  }
  host.token = ''

  await host.handle({ type: 'ws.open', readyId: 'ready-B', socketId: 7 })

  assert.deepEqual(socketCalls[0].socket.protocols, [])
})

test('ws.open constructs the authenticated selected-creature route on the Host', async () => {
  const { host, socketCalls, state } = harness()
  state.selection = {
    session: 'graph live',
    graph: 'graph live',
    creature: 'beta/name',
    targetCreatureId: 'creature-beta',
  }
  host.generation = 4

  await host.handle({ type: 'ws.open', readyId: 'ready-B', socketId: 9 })

  assert.equal(socketCalls[0].generation, 4)
  assert.equal(socketCalls[0].socketId, 9)
  assert.equal(socketCalls[0].socket.url, 'ws://127.0.0.1:8000/ws/sessions/graph%20live/creatures/beta%2Fname/chat')
  assert.deepEqual(socketCalls[0].socket.protocols, ['kt-token.host-secret'])
})

test('clearing selection persists before rotating socket ownership', async () => {
  const { host, sockets, state } = harness()
  state.selection = {
    session: 'graph-old',
    graph: 'graph-old',
    creature: 'old',
    targetCreatureId: 'creature-old',
  }
  const before = sockets.beginCount

  await host.clearSelection()

  assert.equal(state.selection, null)
  assert.equal(sockets.beginCount, before + 1)
})

test('session.resume validates a dormant session and returns a normalized live session', async () => {
  const { client, host, posts } = harness()
  client.listOpen = async () => [{ savedName: 'saved-one', isLive: false }]

  await host.handle({ type: 'session.resume', requestId: 10, savedName: 'saved-one' })

  assert.deepEqual(posts[0].data, {
    conversationId: null,
    runtimeId: 'graph-resumed',
    savedName: 'saved-one',
    title: 'saved-one',
    isLive: true,
    kind: 'creature',
    creatures: [{ id: 'creature-resumed', name: 'resumed' }],
  })
})

test('session.stop clears ownership only after the Host stop succeeds', async () => {
  const { client, host, posts, sockets, state } = harness()
  state.selection = {
    session: 'graph-selected',
    graph: 'graph-selected',
    creature: 'beta',
    targetCreatureId: 'creature-beta',
  }
  host.generation = sockets.begin()
  const before = sockets.beginCount

  await host.handle({
    type: 'session.stop',
    requestId: 11,
    session: 'graph-selected',
    creatureId: 'creature-beta',
  })

  assert.equal(state.selection, null)
  assert.equal(sockets.beginCount, before + 1)
  assert.deepEqual(posts.at(-1), {
    type: 'session.stop.result',
    requestId: 11,
    data: { ok: true, selectionVersion: 1, readyId: 'ready-B' },
  })
  client.stop = async () => {
    throw Error('stop failed')
  }
  state.selection = {
    session: 'graph-next',
    graph: 'graph-next',
    creature: 'beta',
    targetCreatureId: 'creature-beta',
  }
  const failedBefore = sockets.beginCount
  await assert.rejects(
    host.handle({ type: 'session.stop', requestId: 12, session: 'graph-next', creatureId: 'creature-beta' }),
    /stop failed/,
  )
  assert.notEqual(state.selection, null)
  assert.equal(sockets.beginCount, failedBefore)
})
