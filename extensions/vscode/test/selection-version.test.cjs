const assert = require('node:assert/strict')
const test = require('node:test')

function deferred() {
  let resolve
  const promise = new Promise((onResolve) => {
    resolve = onResolve
  })
  return { promise, resolve }
}

const selected = (id) => ({ session: id, targetCreatureId: `${id}-creature` })
const listed = (id) => [
  {
    isLive: true,
    runtimeId: id,
    title: id,
    kind: 'creature',
    creatures: [{ id: `${id}-creature`, name: id }],
  },
]

test('an explicit selection result invalidates a delayed older topology notification before list returns', async () => {
  const { createSelectionVersionOwner } = await import('../src/webview/selectionVersion.mjs')
  const { applyTopologySelection } = await import('../src/webview/topologySelection.mjs')
  const pendingList = deferred()
  const owner = createSelectionVersionOwner()
  const effects = { sessions: listed('B'), current: { marker: 'B' }, binds: 0, unbinds: 0 }
  const notification = owner.beginNotification('A', 1)
  assert.ok(notification)
  const applying = applyTopologySelection({
    selection: selected('A'),
    shell: {
      list: () => pendingList.promise,
      restore() {
        effects.binds++
        return { marker: 'A' }
      },
    },
    chat: {
      unbindFromInstance() {
        effects.unbinds++
      },
    },
    getCurrentSession: () => effects.current,
    setCurrentSession: (value) => {
      effects.current = value
    },
    setSessions: (value) => {
      effects.sessions = value
    },
    isCurrent: notification.isCurrent,
  })

  owner.acceptResult('A', 2)
  pendingList.resolve(listed('A'))
  await applying

  assert.deepEqual(effects, { sessions: listed('B'), current: { marker: 'B' }, binds: 0, unbinds: 0 })
})

test('notifications are monotonic, same-version idempotent, and a newer reconciliation applies', async () => {
  const { createSelectionVersionOwner } = await import('../src/webview/selectionVersion.mjs')
  const owner = createSelectionVersionOwner()
  owner.acceptBaseline('A', 0)
  assert.ok(owner.beginNotification('A', 1))
  assert.equal(owner.beginNotification('A', 1), null)
  owner.acceptResult('A', 2)
  assert.equal(owner.beginNotification('A', 1), null)
  const newer = owner.beginNotification('A', 3)
  assert.ok(newer)
  assert.equal(newer.isCurrent(), true)
})

test('unversioned startup is accepted only before a versioned baseline', async () => {
  const { createSelectionVersionOwner } = await import('../src/webview/selectionVersion.mjs')
  const owner = createSelectionVersionOwner()
  assert.ok(owner.beginNotification('A', undefined))
  owner.acceptBaseline('A', 0)
  assert.equal(owner.beginNotification('A', undefined), null)
  assert.equal(owner.highest(), 0)
})

test('a new ready epoch owns its lower baseline and rejects notifications from the old epoch', async () => {
  const { createSelectionVersionOwner } = await import('../src/webview/selectionVersion.mjs')
  const owner = createSelectionVersionOwner()

  assert.equal(owner.acceptBaseline('A', 0), true)
  assert.ok(owner.beginNotification('A', 2))
  assert.equal(owner.acceptBaseline('B', 0), true)
  assert.equal(owner.highest(), 0)
  assert.equal(owner.beginNotification('A', 3), null)
  assert.ok(owner.beginNotification('B', 1))
  assert.equal(owner.highest(), 1)
})

test('refresh reconciliation applies a lower selection baseline from the new ready epoch', async () => {
  const { createSelectionVersionOwner } = await import('../src/webview/selectionVersion.mjs')
  const { createReadyCoordinator } = await import('../src/webview/readyCoordinator.mjs')
  const owner = createSelectionVersionOwner()
  const ready = []
  const applied = []
  let activeReadyId
  const coordinator = createReadyCoordinator({
    requestReady() {
      const request = deferred()
      const id = ready.length + 1
      activeReadyId = id
      ready.push({ id, ...request })
      return request.promise
    },
    async applyReady(result, isCurrent) {
      if (!isCurrent()) return
      owner.acceptBaseline(activeReadyId, result.selectionVersion)
      applied.push({ selection: result.selection, version: owner.highest() })
    },
  })

  const first = coordinator.reconcile()
  ready[0].resolve({ selection: selected('A'), selectionVersion: 0 })
  await first
  owner.acceptResult(1, 2)

  const refreshed = coordinator.reconcile()
  ready[1].resolve({ selection: selected('B'), selectionVersion: 0 })
  await refreshed

  assert.deepEqual(applied.at(-1), { selection: selected('B'), version: 0 })
})

test('an in-flight replacement runtime result owns its epoch and rejects its delayed ready baseline', async () => {
  const { createSelectionVersionOwner } = await import('../src/webview/selectionVersion.mjs')
  const owner = createSelectionVersionOwner()
  owner.acceptBaseline('A', 3)

  owner.acceptResult('B', 1, true)
  assert.equal(owner.highest(), 1)
  assert.equal(owner.acceptBaseline('B', 0), true)
  assert.equal(owner.highest(), 1)
  assert.equal(owner.beginNotification('A', 4), null)
  assert.equal(owner.acceptResult('A', 5), 1)
  assert.ok(owner.beginNotification('B', 2))
})

test('a command result from an old ready epoch cannot advance the active epoch', async () => {
  const { createSelectionVersionOwner } = await import('../src/webview/selectionVersion.mjs')
  const owner = createSelectionVersionOwner()
  owner.acceptBaseline('A', 0)
  const result = deferred()
  const ownedEpoch = 'A'
  owner.acceptBaseline('B', 0)
  result.resolve({ selectionVersion: 4 })
  const response = await result.promise

  owner.acceptResult(ownedEpoch, response.selectionVersion)
  assert.equal(owner.highest(), 0)
})
