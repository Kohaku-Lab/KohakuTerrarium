const assert = require('node:assert/strict')
const test = require('node:test')

const { deferred, harness } = require('./runtimeHarness.cjs')
const { MediaHost } = require('../src/host/mediaHost.cjs')

// A spool stand-in: only the surface the media coordinator drives for a prepare.
function mediaSpool(overrides = {}) {
  return {
    async prepare(route, { name } = {}) {
      return {
        resourceId: 'r1',
        uri: 'vscode-webview://spool/r1',
        bytes: 3,
        mime: 'video/mp4',
        sha256: 'a'.repeat(64),
        name: name || 'r1',
        state: 'exposed',
      }
    },
    discard: () => true,
    release: () => true,
    acquire: () => true,
    releaseAll() {},
    async start() {
      return '/spool-root'
    },
    async dispose() {},
    ...overrides,
  }
}

const mediaPrepare = (overrides = {}) => ({ type: 'media.prepare', requestId: 2, path: IMG, readyId: 7, selectionVersion: 0, ...overrides })

const flush = () => new Promise((resolve) => setImmediate(resolve))
const IMG = '/api/sessions/graph_1/artifacts/img.png'
const selected = { session: 'graph-live', graph: 'graph-live', creature: 'beta', targetCreatureId: 'creature-beta' }
const unchangedListing = () => [
  { runtimeId: 'graph-live', savedName: 'graph_1', isLive: true, creatures: [{ id: 'creature-beta', name: 'beta' }] },
]
const goalEnvelope = (overrides = {}) => ({
  type: 'goal.execute',
  requestId: 11,
  readyId: 10,
  selectionVersion: 0,
  args: 'list',
  ...overrides,
})

// B3: topology notification ordering and selected-target operation validity are distinct.
// An unchanged-target topology refresh must still deliver the refreshed list, yet must not
// reject a goal/media/history read captured under the previous ordering version. Actual
// ready/selected identity changes still invalidate those operations.

test('an unchanged-target topology refresh is delivered and does not reject a queued goal', async () => {
  const { client, host, state } = harness()
  host.runtimeEpoch = 10
  state.selection = { session: 'graph-a', graph: 'graph-a', creature: 'alpha', targetCreatureId: 'id-alpha' }
  client.listOpen = async () => [{ runtimeId: 'graph-a', isLive: true, title: 'alpha', creatures: [{ id: 'id-alpha', name: 'alpha' }] }]
  const blocked = deferred()
  host.selectionOperationTail = blocked.promise

  const goal = host.handle(goalEnvelope())
  await flush()
  const topology = await host.reconcileTopologySelection()
  // Delivered, not superseded, and advances the ordering version the Webview needs.
  assert.equal(topology.changed, false)
  assert.equal(topology.superseded, undefined)
  assert.equal(topology.selectionVersion, 1)
  assert.equal(host.selectionVersion, 1)

  blocked.resolve()
  await goal
  assert.deepEqual(client.commandCalls, [{ session: 'graph-a', creature: 'id-alpha', command: 'goal', args: 'list' }])
})

test('an unchanged-target topology refresh does not reject an in-flight media prepare', async () => {
  const gate = deferred()
  const mediaHost = new MediaHost({
    spool: mediaSpool({
      prepare: async () => {
        await gate.promise
        return { resourceId: 'r1', uri: 'vscode-webview://spool/r1', state: 'exposed' }
      },
    }),
  })
  const { client, host, state, posts } = harness({ mediaHost })
  host.runtimeEpoch = 7
  state.selection = selected
  client.listOpen = async () => unchangedListing()

  const preparing = host.handle(mediaPrepare())
  await flush()
  const topology = await host.reconcileTopologySelection()
  assert.equal(topology.changed, false)

  gate.resolve()
  await preparing
  assert.equal(posts.at(-1).type, 'media.prepare.result')
  assert.equal(posts.at(-1).data.resourceId, 'r1')
})

test('an unchanged-target topology refresh does not reject an in-flight history read', async () => {
  const readGate = deferred()
  const { client, host, state, posts } = harness()
  host.runtimeEpoch = 7
  state.selection = selected
  client.listOpen = async () => unchangedListing()
  client.historyPage = async () => {
    await readGate.promise
    return { events: [] }
  }

  const reading = host.handle({ type: 'http.historyPage', requestId: 3, session: 'graph-live', creature: 'beta', options: {} })
  await flush()
  const topology = await host.reconcileTopologySelection()
  assert.equal(topology.changed, false)

  readGate.resolve()
  await reading
  assert.equal(posts.at(-1).type, 'http.historyPage.result')
})

test('an actual target change rejects a queued goal', async () => {
  const { client, host, state } = harness()
  host.runtimeEpoch = 10
  state.selection = { session: 'graph-a', graph: 'graph-a', creature: 'alpha', targetCreatureId: 'id-alpha' }
  const blocked = deferred()
  host.selectionOperationTail = blocked.promise

  const goal = host.handle(goalEnvelope())
  const rejected = assert.rejects(goal, /ownership/)
  state.selection = { session: 'graph-b', graph: 'graph-b', creature: 'beta', targetCreatureId: 'id-beta' }
  blocked.resolve()

  await rejected
  assert.equal(client.commandCalls.length, 0)
})

test('a ready reset rejects a queued goal', async () => {
  const { client, host, state } = harness()
  host.runtimeEpoch = 10
  state.selection = { session: 'graph-a', graph: 'graph-a', creature: 'alpha', targetCreatureId: 'id-alpha' }
  const blocked = deferred()
  host.selectionOperationTail = blocked.promise

  const goal = host.handle(goalEnvelope())
  const rejected = assert.rejects(goal, /ownership/)
  host.beginReady('ready-C')
  blocked.resolve()

  await rejected
  assert.equal(client.commandCalls.length, 0)
})

test('an explicit target change aborts an in-flight media prepare without posting a result', async () => {
  const mediaHost = new MediaHost({
    spool: mediaSpool({
      prepare: (route, { signal }) =>
        new Promise((_, reject) => {
          signal.addEventListener('abort', () => reject(Error('Media request cancelled')), { once: true })
        }),
    }),
  })
  const { client, host, state, posts } = harness({ mediaHost })
  host.runtimeEpoch = 7
  state.selection = selected
  client.listOpen = async () => unchangedListing()

  const preparing = host.handle(mediaPrepare())
  await flush()
  await host.handle({ type: 'session.clearSelection', requestId: 3 })
  await assert.rejects(() => preparing, /cancelled/)
  assert.equal(
    posts.some((post) => post.type === 'media.prepare.result'),
    false,
  )
})
