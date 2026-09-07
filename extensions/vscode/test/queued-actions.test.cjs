const assert = require('node:assert/strict')
const test = require('node:test')

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
async function fixture() {
  const { createQueuedActions } = await import('../src/webview/queuedActions.mjs')
  const confirmation = deferred()
  const item = { backendQueued: true, eventId: 'event-a', content: 'before', contentParts: [{ type: 'text', text: 'before' }] }
  const queue = [item]
  const chat = {
    activeTab: 'alpha',
    wsStatus: 'open',
    _ws: { readyState: 1 },
    queuedMessagesByTab: { alpha: queue },
    editQueuedMessage(tab, id, content) {
      item.content = content[0].text
      item.contentParts = content
    },
    cancelQueuedMessage() {
      item.cancelling = true
    },
  }
  const timers = new Set()
  const actions = createQueuedActions({
    chat,
    BridgeWebSocket: {
      OPEN: 1,
      captureSend(fn) {
        return { value: fn(), frame: 'sent', confirmation: confirmation.promise }
      },
    },
    setTimer: (fn) => {
      timers.add(fn)
      return fn
    },
    clearTimer: (fn) => timers.delete(fn),
  })
  return { actions, confirmation, item, chat, queue, timers }
}

test('uncertain Host failures block retry until a late matching backend acknowledgement', async () => {
  const { actions, confirmation, item, timers } = await fixture()
  const pending = actions.edit('alpha', item, [{ type: 'text', text: 'after' }])
  await assert.rejects(actions.cancel('alpha', item), /pending/)
  assert.equal(item.content, 'after')
  confirmation.reject(Error('Host rejected write'))
  await assert.rejects(pending, /Host rejected/)
  assert.equal(item.content, 'after')
  assert.equal(item.queueActionUncertain, true)
  await assert.rejects(actions.edit('alpha', item, [{ type: 'text', text: 'retry' }]), /unknown/)
  assert.equal(timers.size, 0)
  actions.observe({ type: 'input_edit_ack', source: 'alpha', event_id: item.eventId, status: 'edited' })
  assert.equal(Boolean(item.queueActionUncertain), false)
})

test('backend ack wins over late Host failure and queue drain never resurrects an entry', async () => {
  const { actions, confirmation, item, chat, queue } = await fixture()
  const pending = actions.cancel('alpha', item)
  actions.observe({ type: 'input_cancel_ack', source: 'alpha', event_id: item.eventId, status: 'cancelled' })
  queue.splice(0, 1)
  confirmation.reject(Error('closed after backend ack'))
  await pending
  assert.deepEqual(chat.queuedMessagesByTab.alpha, [])
})

test('queue actions wait for backend ack and time out with unknown outcome', async () => {
  const { actions, confirmation, item, timers } = await fixture()
  const pending = actions.cancel('alpha', item)
  let settled = false
  pending.then(
    () => {
      settled = true
    },
    () => {
      settled = true
    },
  )
  confirmation.resolve()
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(settled, false)
  for (const fn of [...timers]) fn()
  await assert.rejects(pending, /may have|unknown/)
  assert.equal(Boolean(item.cancelling), false)
  assert.equal(timers.size, 0)
})

test('unconfirmed initial sends cannot be changed and unrelated acknowledgement kinds cannot settle an operation', async () => {
  const { actions, item, confirmation, timers } = await fixture()
  item.backendQueued = false
  await assert.rejects(actions.cancel('alpha', item), /acknowledgement/)
  assert.equal(item.cancelling, undefined)
  item.backendQueued = true
  const pending = actions.cancel('alpha', item)
  confirmation.resolve()
  actions.observe({ type: 'input_edit_ack', source: 'alpha', event_id: item.eventId, status: 'edited' })
  actions.observe({ type: 'input_cancel_ack', source: 'alpha', event_id: item.eventId, status: 'edited' })
  let settled = false
  pending.then(
    () => {
      settled = true
    },
    () => {
      settled = true
    },
  )
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(settled, false)
  actions.observe({ type: 'input_cancel_ack', source: 'alpha', event_id: item.eventId, status: 'already_sent' })
  assert.equal(await pending, 'already_sent')
  assert.equal(timers.size, 0)
})

test('queue drain and replacement while Host failure is pending never restore the old item', async () => {
  const { actions, item, chat, confirmation, queue } = await fixture()
  const pending = actions.edit('alpha', item, [{ type: 'text', text: 'after' }])
  queue.splice(0, 1)
  const replacement = { eventId: item.eventId, content: 'other conversation' }
  chat.queuedMessagesByTab.alpha = [replacement]
  confirmation.reject(Error('transport closed'))
  await assert.rejects(pending)
  assert.deepEqual(chat.queuedMessagesByTab.alpha, [replacement])
  assert.equal(item.content, 'after')
})

test('missing initial queue acknowledgement becomes unknown instead of waiting forever', async () => {
  const { actions, item, timers } = await fixture()
  item.backendQueued = false
  actions.trackInput('alpha', item)
  assert.equal(timers.size, 1)
  for (const fn of [...timers]) fn()
  assert.equal(item.queueActionUncertain, true)
  assert.equal(timers.size, 0)
  actions.observe({ type: 'input_queued', source: 'alpha', event_id: item.eventId }, {})
  assert.equal(item.queueActionUncertain, true, 'stale socket cannot release uncertain input')
  actions.observe({ type: 'input_queued', source: 'alpha', event_id: item.eventId })
  assert.equal(Boolean(item.queueActionUncertain), false)
})

test('disconnect, stale item, different tab and disposal cannot mutate queue state', async () => {
  const { actions, item, chat, confirmation, timers } = await fixture()
  chat.wsStatus = 'closed'
  await assert.rejects(actions.cancel('alpha', item), /disconnected/)
  assert.equal(item.cancelling, undefined)
  chat.wsStatus = 'open'
  await assert.rejects(actions.cancel('beta', item))
  await assert.rejects(actions.edit('alpha', { ...item }, 'replacement'))
  const pending = actions.cancel('alpha', item)
  const rejected = assert.rejects(pending, /disposed/)
  actions.dispose()
  await rejected
  confirmation.resolve()
  assert.equal(timers.size, 0)
  await assert.rejects(actions.cancel('alpha', item), /disposed/)
})
