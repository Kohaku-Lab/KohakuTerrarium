const assert = require('node:assert/strict')
const test = require('node:test')

let createSubmitGate
let isComposerSubmitDisabled

test.before(async () => {
  ;({ createSubmitGate, isComposerSubmitDisabled } = await import('../src/webview/submitGate.mjs'))
})

const owner = (runtimeId = 'session-a') => ({ readyId: 1, runtimeId, creatureId: 'root' })

function deferred() {
  let resolve
  let reject
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

test('click or Enter while the first submit is pending performs no second conversion or send', async () => {
  const gate = createSubmitGate()
  const pending = deferred()
  let conversions = 0
  let sends = 0
  const submit = async () => {
    const token = gate.acquire(owner())
    if (!token) return
    try {
      conversions += 1
      await pending.promise
      sends += 1
    } finally {
      gate.release(token)
    }
  }

  const first = submit()
  await submit()
  assert.equal(conversions, 1)
  assert.equal(sends, 0)

  pending.resolve()
  await first
  assert.equal(sends, 1)
  await submit()
  assert.equal(conversions, 2)
})

test('pending submit disables Send but never disables processing Stop', () => {
  assert.equal(isComposerSubmitDisabled(true, false), true)
  assert.equal(isComposerSubmitDisabled(true, true), false)
  assert.equal(isComposerSubmitDisabled(false, false), false)
})

test('only one submit can own a conversation until accepted completion releases it', () => {
  const gate = createSubmitGate()
  const first = gate.acquire(owner())

  assert.ok(first)
  assert.equal(gate.busy(owner()), true)
  assert.equal(gate.acquire(owner()), null)

  gate.release(first)
  assert.equal(gate.busy(owner()), false)
  assert.ok(gate.acquire(owner()))
})

test('rejected and stale completions release only their own gate ownership', () => {
  const gate = createSubmitGate()
  const stale = gate.acquire(owner())
  const current = gate.acquire(owner('session-b'))

  assert.ok(current)
  assert.equal(gate.busy(owner()), true)
  assert.equal(gate.busy(owner('session-b')), true)

  gate.release(stale)
  assert.equal(gate.busy(owner('session-b')), true)
  gate.release(current)
  assert.equal(gate.busy(owner('session-b')), false)
})
