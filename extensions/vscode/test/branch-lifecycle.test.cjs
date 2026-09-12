const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const shimSource = fs.readFileSync(path.join(__dirname, '../src/webview/shims/api.js'), 'utf8')
const loadShim = () => import('data:text/javascript,' + encodeURIComponent(shimSource))

async function bridgeFixture() {
  const { installBranchBridge } = await import('../src/webview/branchBridge.mjs')
  const { createRequestLifecycle } = await import('../src/webview/requestLifecycle.mjs')
  const sent = []
  const owner = { admittedReadyId: 100, runtimeId: 'g', creatureId: 'c1' }
  const lifecycle = createRequestLifecycle({ postMessage: (message) => sent.push(message) })
  const uninstall = installBranchBridge({ request: lifecycle.request, getOwner: () => owner })
  return { sent, owner, lifecycle, uninstall, regenerate: globalThis.__ktVsCodeRegenerate }
}

for (const change of ['ready', 'target', 'dispose']) {
  for (const status of [200, 409]) {
    test(`the branch bridge rejects a delayed ${status} response after ${change} supersession`, async () => {
      const app = await bridgeFixture()
      try {
        const pending = app.regenerate('g', 'root')
        const request = app.sent[0]
        if (change === 'ready') app.owner.admittedReadyId = null
        if (change === 'target') app.owner.creatureId = 'c2'
        if (change === 'dispose') app.uninstall()
        const message =
          status === 200
            ? { type: 'http.regenerate.result', requestId: request.requestId, data: { status: 'completed', branch_id: 2 } }
            : { type: 'error', requestId: request.requestId, error: 'conflict', status: 409, mayHaveRun: true }
        app.lifecycle.settle(message)
        await assert.rejects(pending, (error) => {
          assert.equal(error.superseded, true)
          assert.equal(error.mayHaveRun, true)
          assert.equal(error.status, status === 409 ? 409 : undefined)
          return true
        })
        assert.equal(app.sent.length, 1, 'a stale response is never retried')
        assert.equal(app.lifecycle.size(), 0)
      } finally {
        app.uninstall()
      }
    })
  }
}

for (const reason of ['ready', 'disposed', 'uninstalled']) {
  test(`a ${reason} branch call is explicitly rejected before dispatch`, async () => {
    const app = await bridgeFixture()
    const shim = await loadShim()
    try {
      if (reason === 'ready') app.owner.admittedReadyId = null
      else app.uninstall()
      const call = reason === 'uninstalled' ? () => shim.agentAPI.regenerate('g', 'root') : () => app.regenerate('g', 'root')
      await assert.rejects(call(), (error) => {
        assert.equal(error.mayHaveRun, false)
        assert.equal(error.status, undefined)
        assert.equal(error.response, undefined)
        return true
      })
      assert.equal(app.sent.length, 0)
    } finally {
      app.uninstall()
    }
  })
}

for (const timeoutMs of [0, 30000]) {
  test(`a synchronous post failure releases its pending entry and timer (${timeoutMs})`, async () => {
    const { createRequestLifecycle } = await import('../src/webview/requestLifecycle.mjs')
    const timers = new Map()
    let next = 0
    const lifecycle = createRequestLifecycle({
      postMessage: () => {
        throw Error('webview closed')
      },
      setTimer: (callback) => {
        const id = next++
        timers.set(id, callback)
        return id
      },
      clearTimer: (id) => timers.delete(id),
    })
    await assert.rejects(
      lifecycle.request('http.editMessage', {}, () => {}, { timeoutMs }),
      (error) => {
        assert.match(error.message, /webview closed/)
        return true
      },
    )
    assert.equal(lifecycle.size(), 0, 'a rejected post must not retain the submitted content')
    assert.equal(timers.size, 0)
  })
}

test('request completion clears the same timer provider used at creation', async () => {
  const { createRequestLifecycle } = await import('../src/webview/requestLifecycle.mjs')
  const timers = new Map()
  let sent
  const lifecycle = createRequestLifecycle({
    postMessage: (message) => {
      sent = message
    },
    setTimer: (callback) => {
      timers.set(0, callback)
      return 0
    },
    clearTimer: (id) => timers.delete(id),
  })
  const response = lifecycle.request('session.list')
  lifecycle.settle({ type: 'session.list.result', requestId: sent.requestId, data: ['ok'] })
  assert.deepEqual(await response, ['ok'])
  assert.equal(timers.size, 0)
  assert.equal(lifecycle.size(), 0)
})

test('rejectAll marks only branch waits as superseded with an uncertain dispatch outcome', async () => {
  const { createRequestLifecycle } = await import('../src/webview/requestLifecycle.mjs')
  const lifecycle = createRequestLifecycle({ postMessage: () => {} })
  const branch = lifecycle.request('http.editMessage', {}, () => {}, { timeoutMs: 0 }).catch((error) => error)
  const ordinary = lifecycle.request('session.list').catch((error) => error)
  const cause = Error('Connection changed')
  lifecycle.rejectAll(cause)
  const error = await branch
  assert.equal(error.superseded, true)
  assert.equal(error.mayHaveRun, true)
  assert.equal(error.response, undefined, 'local invalidation is not an HTTP response')
  assert.equal(await ordinary, cause)
  assert.equal(cause.mayHaveRun, undefined, 'a shared error must not be mutated for unrelated requests')
  assert.equal(lifecycle.size(), 0)
})
