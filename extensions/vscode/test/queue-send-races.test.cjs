const assert = require('node:assert/strict')
const test = require('node:test')

for (const bucket of ['messagesByTab', 'queuedMessagesByTab']) {
  test(`no-frame failure removes only its synchronous optimistic ${bucket} entry`, async () => {
    const { createHostAcceptedChat } = await import('../src/webview/hostAcceptedChat.mjs')
    const original = { eventId: 'existing' }
    const newItem = { role: 'user', eventId: 'new', content: 'new' }
    const chat = {
      activeTab: 'a',
      messagesByTab: { a: [] },
      queuedMessagesByTab: { a: [] },
      processingByTab: {},
      send() {
        this[bucket].a.push(newItem)
        return Promise.reject(Error('socket closed before send'))
      },
    }
    chat[bucket].a.push(original)
    const adapter = createHostAcceptedChat({ chat, BridgeWebSocket: { captureSend: (fn) => ({ value: fn() }) } })
    await assert.rejects(adapter.send('new'), /socket closed/)
    assert.deepEqual(chat[bucket].a, [original])
  })
}

test('no-frame failure also cleans up a newly created queue bucket', async () => {
  const { createHostAcceptedChat } = await import('../src/webview/hostAcceptedChat.mjs')
  const chat = {
    activeTab: 'a',
    messagesByTab: { a: [] },
    queuedMessagesByTab: {},
    processingByTab: {},
    send() {
      this.queuedMessagesByTab.a = [{ role: 'user', eventId: 'new' }]
      return Promise.reject(Error('socket closed'))
    },
  }
  const adapter = createHostAcceptedChat({ chat, BridgeWebSocket: { captureSend: (fn) => ({ value: fn() }) } })
  await assert.rejects(adapter.send('new'), /socket closed/)
  assert.deepEqual(chat.queuedMessagesByTab.a, [])
})

test('backend queue acknowledgement moves locally sent transcript input into the queue once', async () => {
  const { createHostAcceptedChat } = await import('../src/webview/hostAcceptedChat.mjs')
  const item = { role: 'user', eventId: 'new', content: 'new' }
  const socket = { readyState: 1 }
  const chat = {
    activeTab: 'a',
    _ws: socket,
    messagesByTab: { a: [] },
    queuedMessagesByTab: { a: [] },
    processingByTab: {},
    send() {
      this.messagesByTab.a.push(item)
    },
  }
  const adapter = createHostAcceptedChat({
    chat,
    BridgeWebSocket: { captureSend: (fn) => ({ value: fn(), frame: JSON.stringify({ type: 'input', event_id: 'new' }) }) },
  })
  await adapter.send('new')
  const ack = { type: 'input_queued', source: 'a', event_id: 'new' }
  adapter.observe(ack, {})
  assert.equal(chat.messagesByTab.a.length, 1, 'old socket cannot relocate a current item')
  adapter.observe(ack, socket)
  adapter.observe(ack, socket)
  assert.deepEqual(chat.messagesByTab.a, [])
  assert.deepEqual(chat.queuedMessagesByTab.a, [item])
  assert.equal(item.backendQueued, true)
  adapter.queued.dispose()
})
