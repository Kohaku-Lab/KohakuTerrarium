const assert = require('node:assert/strict')
const test = require('node:test')

const { deferred, harness } = require('./runtimeHarness.cjs')

const flush = () => new Promise((resolve) => setImmediate(resolve))
const selected = { session: 'graph-live', graph: 'graph-live', creature: 'beta', targetCreatureId: 'creature-beta' }
const unchangedListing = () => [
  { runtimeId: 'graph-live', savedName: 'graph_1', isLive: true, creatures: [{ id: 'creature-beta', name: 'beta' }] },
]

// Context management is an explicit user command: admission is the stable target
// identity + ready epoch + explicit-intent fence, not the notification-ordering
// selectionVersion. An unchanged-target topology refresh advances selectionVersion yet
// must not reject a command captured before the clear confirmation or while compacting.

test('an unchanged-target topology refresh does not reject an in-flight context compact', async () => {
  const commandGate = deferred()
  const commandCalls = []
  const { client, host, state, posts } = harness()
  host.runtimeEpoch = 7
  state.selection = selected
  client.listOpen = async () => unchangedListing()
  client.creatureCommand = async (session, creature, command, args) => {
    commandCalls.push({ session, creature, command, args })
    await commandGate.promise
    return { data: { message: 'Context compacted' } }
  }

  const compacting = host.handle({ type: 'context.compact', requestId: 4 })
  await flush()
  const topology = await host.reconcileTopologySelection()
  assert.equal(topology.changed, false)
  assert.equal(host.selectionVersion, 1)

  commandGate.resolve()
  await compacting
  assert.deepEqual(commandCalls, [{ session: 'graph-live', creature: 'beta', command: 'compact', args: '' }])
  assert.deepEqual(posts.at(-1), {
    type: 'context.compact.result',
    requestId: 4,
    data: { data: { message: 'Context compacted' } },
  })
})

test('an unchanged-target topology refresh during clear confirmation still executes clear once', async () => {
  const { client, host, state, posts } = harness()
  host.runtimeEpoch = 7
  state.selection = selected
  client.listOpen = async () => unchangedListing()

  // Captured exactly where the extension dispatches, before awaiting the modal.
  const capability = host.acquireContextCommand()
  assert.equal(host.ownsContextCommand(capability), true)
  const topology = await host.reconcileTopologySelection()
  assert.equal(topology.changed, false)
  assert.equal(host.ownsContextCommand(capability), true)

  await host.handle({ type: 'context.clear', requestId: 5, contextCapability: capability })
  assert.deepEqual(client.commandCalls, [{ session: 'graph-live', creature: 'beta', command: 'clear', args: '--force' }])
  assert.equal(posts.at(-1).type, 'context.clear.result')
})

test('an actual target switch rejects a context command captured before confirmation', async () => {
  const { client, host, state } = harness()
  host.runtimeEpoch = 7
  state.selection = selected
  const capability = host.acquireContextCommand()
  state.selection = { session: 'graph-other', graph: 'graph-other', creature: 'gamma', targetCreatureId: 'creature-gamma' }

  assert.equal(host.ownsContextCommand(capability), false)
  await assert.rejects(host.handle({ type: 'context.compact', requestId: 6, contextCapability: capability }), /ownership changed/)
  assert.equal(client.commandCalls.length, 0)
})

test('an explicit reselect of the same target suppresses an earlier context command', async () => {
  const { client, host, state } = harness()
  host.runtimeEpoch = 7
  state.selection = selected
  const capability = host.acquireContextCommand()
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })

  assert.equal(host.ownsContextCommand(capability), false)
  await assert.rejects(host.handle({ type: 'context.clear', requestId: 7, contextCapability: capability }), /ownership changed/)
  assert.equal(client.commandCalls.length, 0)
})

test('a ready change rejects a context command captured before confirmation', async () => {
  const { client, host, state } = harness()
  host.runtimeEpoch = 7
  state.selection = selected
  const capability = host.acquireContextCommand()
  host.beginReady(8)

  assert.equal(host.ownsContextCommand(capability), false)
  await assert.rejects(host.handle({ type: 'context.compact', requestId: 8, contextCapability: capability }), /ownership changed/)
  assert.equal(client.commandCalls.length, 0)
})
