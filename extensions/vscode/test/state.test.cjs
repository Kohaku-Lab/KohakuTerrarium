const assert = require('node:assert/strict')
const test = require('node:test')

const { ConnectionStateWriter } = require('../src/host/state.cjs')

test('connection state updates are serialized and the newest configuration wins', async () => {
  let value = { endpoint: 'old', selection: null }
  let releaseOld
  const workspaceState = {
    get: () => value,
    async update(_key, next) {
      if (next.endpoint === 'old' && next.selection) {
        await new Promise((resolve) => {
          releaseOld = resolve
        })
      }
      value = next
    },
  }
  const writer = new ConnectionStateWriter(workspaceState, 'connection')
  const old = writer.update(() => ({ endpoint: 'old', selection: { session: 'stale' } }))
  await new Promise((resolve) => setImmediate(resolve))
  const configured = writer.update(() => ({ endpoint: 'new', selection: null }))

  releaseOld()
  await Promise.all([old, configured])

  assert.deepEqual(value, { endpoint: 'new', selection: null })
})

test('conditional updates decide ownership at serialized execution time', async () => {
  let value = { endpoint: 'old', selection: null }
  let releaseOld
  let owned = true
  const workspaceState = {
    get: () => value,
    async update(_key, next) {
      if (next.endpoint === 'old' && next.selection?.session === 'blocker') {
        await new Promise((resolve) => (releaseOld = resolve))
      }
      value = next
    },
  }
  const writer = new ConnectionStateWriter(workspaceState, 'connection')
  const blocker = writer.update(() => ({ endpoint: 'old', selection: { session: 'blocker' } }))
  await new Promise((resolve) => setImmediate(resolve))
  const guarded = writer.updateIf(
    () => owned,
    (current) => ({ ...current, selection: { session: 'topology' } }),
  )
  owned = false
  releaseOld()

  const [_, result] = await Promise.all([blocker, guarded])

  assert.equal(result.applied, false)
  assert.deepEqual(value, { endpoint: 'old', selection: { session: 'blocker' } })
})

test('ordinary update reports whether the mutator persisted a value', async () => {
  let value = { endpoint: 'new', selection: null }
  const workspaceState = {
    get: () => value,
    async update(_key, next) {
      value = next
    },
  }
  const writer = new ConnectionStateWriter(workspaceState, 'connection')

  const skipped = await writer.update(() => undefined)
  const applied = await writer.update((current) => ({ ...current, selection: { session: 'live' } }))

  assert.equal(skipped.applied, false)
  assert.equal(applied.applied, true)
})

test('a guarded stale selection write can no-op at execution time', async () => {
  let value = { endpoint: 'new', selection: null }
  const workspaceState = {
    get: () => value,
    async update(_key, next) {
      value = next
    },
  }
  const writer = new ConnectionStateWriter(workspaceState, 'connection')

  await writer.update((current) => (current.endpoint === 'old' ? { endpoint: 'old', selection: { session: 'stale' } } : undefined))

  assert.deepEqual(value, { endpoint: 'new', selection: null })
})
