const assert = require('node:assert/strict')
const test = require('node:test')

function deferred() {
  let resolve
  let reject
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

const selected = (id) => ({ session: id, targetCreatureId: `${id}-creature` })
const listed = (id) => [
  {
    isLive: true,
    runtimeId: id,
    title: id,
    creatures: [{ id: `${id}-creature`, name: id }],
  },
]

async function harness(listPromise, isCurrent) {
  const { applyTopologySelection } = await import('../src/webview/topologySelection.mjs')
  const effects = { sessions: ['B-session'], current: { marker: 'B' }, binds: 0, unbinds: 0 }
  const shell = {
    list: () => listPromise,
    restore(session, selection) {
      effects.binds++
      return { session, targetCreatureId: selection.targetCreatureId }
    },
  }
  const chat = {
    unbindFromInstance() {
      effects.unbinds++
    },
  }
  return {
    effects,
    apply: applyTopologySelection({
      selection: selected('A'),
      shell,
      chat,
      getCurrentSession: () => effects.current,
      setCurrentSession: (value) => {
        effects.current = value
      },
      setSessions: (value) => {
        effects.sessions = value
      },
      isCurrent,
    }),
  }
}

test('queued selection cannot mutate topology after a newer ready generation becomes authoritative', async () => {
  const pendingList = deferred()
  let current = true
  const run = await harness(pendingList.promise, () => current)
  current = false
  pendingList.resolve(listed('A'))
  await run.apply

  assert.deepEqual(run.effects, {
    sessions: ['B-session'],
    current: { marker: 'B' },
    binds: 0,
    unbinds: 0,
  })
})

test('stale queued selection quietly discards a list failure', async () => {
  const pendingList = deferred()
  let current = true
  const run = await harness(pendingList.promise, () => current)
  current = false
  pendingList.reject(Error('stale list failure'))
  await run.apply
  assert.deepEqual(run.effects, {
    sessions: ['B-session'],
    current: { marker: 'B' },
    binds: 0,
    unbinds: 0,
  })
})

test('current queued selection surfaces a list failure', async () => {
  const pendingList = deferred()
  const run = await harness(pendingList.promise, () => true)
  pendingList.reject(Error('current list failure'))
  await assert.rejects(run.apply, /current list failure/)
})

test('queued selection still applies while its ready generation remains authoritative', async () => {
  const pendingList = deferred()
  const run = await harness(pendingList.promise, () => true)
  pendingList.resolve(listed('A'))
  await run.apply

  assert.deepEqual(run.effects.sessions, listed('A'))
  assert.equal(run.effects.current.targetCreatureId, 'A-creature')
  assert.equal(run.effects.binds, 1)
})
