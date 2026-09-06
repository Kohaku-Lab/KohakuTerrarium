const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const root = path.resolve(__dirname, '..')

async function helpers() {
  return import(pathToFileURL(path.join(root, 'src', 'webview', 'hostAcceptedChat.mjs')))
}

test('Host-rejected input restores only its Extension-owned optimistic state', async () => {
  const { createHostAcceptedChat } = await helpers()
  const message = { role: 'user', eventId: 'event-1', content: 'hello' }
  const chat = {
    activeTab: 'worker',
    messagesByTab: { worker: [] },
    queuedMessagesByTab: { worker: [] },
    processingByTab: { worker: false },
    send() {
      this.messagesByTab.worker.push(message)
      this.processingByTab.worker = true
    },
  }
  const BridgeWebSocket = {
    captureSend(callback) {
      callback()
      return {
        frame: JSON.stringify({
          type: 'input',
          target: 'worker',
          event_id: 'event-1',
        }),
        confirmation: Promise.reject(Error('Host rejected send')),
      }
    },
  }
  const accepted = createHostAcceptedChat({ BridgeWebSocket, chat })

  await assert.rejects(accepted.send('hello'), /Host rejected send/)
  assert.deepEqual(chat.messagesByTab.worker, [])
  assert.equal(chat.processingByTab.worker, false)
})

test('non-WebSocket command completion is returned without Host confirmation', async () => {
  const { createHostAcceptedChat } = await helpers()
  const chat = {
    activeTab: 'worker',
    messagesByTab: { worker: [] },
    queuedMessagesByTab: { worker: [] },
    processingByTab: { worker: false },
    send: async () => ({ handled: 'command', result: { id: 'goal-1' } }),
  }
  const BridgeWebSocket = {
    captureSend(callback) {
      return { value: callback(), frame: undefined, confirmation: null }
    },
  }
  const accepted = createHostAcceptedChat({ BridgeWebSocket, chat })

  assert.deepEqual(await accepted.send('/goal set X'), {
    handled: 'command',
    result: { id: 'goal-1' },
  })
})

test('Host-rejected input follows the event if the store moved it into the queue', async () => {
  const { createHostAcceptedChat } = await helpers()
  const message = { role: 'user', eventId: 'event-1', content: 'hello' }
  const chat = {
    activeTab: 'worker',
    messagesByTab: { worker: [] },
    queuedMessagesByTab: { worker: [] },
    processingByTab: { worker: false },
    send() {
      this.messagesByTab.worker.push(message)
      this.processingByTab.worker = true
      this.queuedMessagesByTab.worker.push(this.messagesByTab.worker.pop())
    },
  }
  const BridgeWebSocket = {
    captureSend(callback) {
      callback()
      return {
        frame: JSON.stringify({
          type: 'input',
          target: 'worker',
          event_id: 'event-1',
        }),
        confirmation: Promise.reject(Error('Host rejected send')),
      }
    },
  }
  const accepted = createHostAcceptedChat({ BridgeWebSocket, chat })

  await assert.rejects(accepted.send('hello'), /Host rejected send/)
  assert.deepEqual(chat.messagesByTab.worker, [])
  assert.deepEqual(chat.queuedMessagesByTab.worker, [])
  assert.equal(chat.processingByTab.worker, false)
})

test('Host-rejected input does not remove a live echo after backend acceptance', async () => {
  const { createHostAcceptedChat } = await helpers()
  const message = { role: 'user', eventId: 'event-1', content: 'hello' }
  const chat = {
    activeTab: 'worker',
    messagesByTab: { worker: [] },
    queuedMessagesByTab: { worker: [] },
    processingByTab: { worker: false },
    send() {
      this.messagesByTab.worker.push(message)
      this.processingByTab.worker = true
    },
  }
  const BridgeWebSocket = {
    captureSend(callback) {
      callback()
      return {
        frame: JSON.stringify({
          type: 'input',
          target: 'worker',
          event_id: 'event-1',
        }),
        confirmation: Promise.reject(Error('Host rejected send')),
      }
    },
  }
  const accepted = createHostAcceptedChat({ BridgeWebSocket, chat })
  const pending = accepted.send('hello')
  accepted.observe({
    type: 'user_input',
    source: 'worker',
    event_id: 'event-1',
  })

  await assert.rejects(pending, /Host rejected send/)
  assert.deepEqual(chat.messagesByTab.worker, [message])
  assert.equal(chat.processingByTab.worker, false)
})

test('callback failure still honors backend acceptance before deciding rollback', async () => {
  const { createHostAcceptedChat } = await helpers()
  const message = { role: 'user', eventId: 'event-1', content: 'hello' }
  let rejectConfirmation
  const chat = {
    activeTab: 'worker',
    messagesByTab: { worker: [message] },
    queuedMessagesByTab: { worker: [] },
    processingByTab: { worker: true },
    send: () => undefined,
  }
  const BridgeWebSocket = {
    captureSend() {
      return {
        error: Error('store failed after send'),
        frame: JSON.stringify({
          type: 'input',
          target: 'worker',
          event_id: 'event-1',
        }),
        confirmation: new Promise((resolve, reject) => (rejectConfirmation = reject)),
      }
    },
  }
  const accepted = createHostAcceptedChat({ BridgeWebSocket, chat })
  const pending = accepted.send('hello')
  accepted.observe({
    type: 'user_input',
    source: 'worker',
    event_id: 'event-1',
  })
  rejectConfirmation(Error('late Host rejection'))

  await assert.rejects(pending, /store failed after send/)
  assert.deepEqual(chat.messagesByTab.worker, [message])
})

test('Host-rejected UI reply restores its prompt without changing the shared store API', async () => {
  const { createHostAcceptedChat } = await helpers()
  const prompt = { role: 'ui_event', eventId: 'event-1', replied: false }
  const chat = {
    messagesByTab: { worker: [prompt] },
    submitUIReply(tab, eventId, actionId, values) {
      Object.assign(prompt, {
        replied: true,
        repliedActionId: actionId,
        repliedValues: values,
      })
      return true
    },
  }
  const BridgeWebSocket = {
    captureSend(callback) {
      callback()
      return {
        frame: JSON.stringify({
          type: 'ui_reply',
          target: 'worker',
          event_id: 'event-1',
        }),
        confirmation: Promise.reject(Error('Host rejected reply')),
      }
    },
  }
  const accepted = createHostAcceptedChat({ BridgeWebSocket, chat })

  await assert.rejects(accepted.submitUIReply('worker', 'event-1', 'submit', { text: 'hi' }), /Host rejected reply/)
  assert.deepEqual(prompt, {
    role: 'ui_event',
    eventId: 'event-1',
    replied: false,
  })
})

test('Host-rejected UI reply does not undo an accepted backend acknowledgement', async () => {
  const { createHostAcceptedChat } = await helpers()
  const values = { text: 'hi' }
  const prompt = { role: 'ui_event', eventId: 'event-1', replied: false }
  const chat = {
    messagesByTab: { worker: [prompt] },
    submitUIReply() {
      Object.assign(prompt, {
        replied: true,
        repliedActionId: 'submit',
        repliedValues: values,
      })
      return true
    },
  }
  const BridgeWebSocket = {
    captureSend(callback) {
      callback()
      return {
        frame: JSON.stringify({
          type: 'ui_reply',
          target: 'worker',
          event_id: 'event-1',
        }),
        confirmation: Promise.reject(Error('Host rejected reply')),
      }
    },
  }
  const accepted = createHostAcceptedChat({ BridgeWebSocket, chat })
  const pending = accepted.submitUIReply('worker', 'event-1', 'submit', values)
  accepted.observe({
    type: 'ui_reply_ack',
    source: 'worker',
    event_id: 'event-1',
    status: 'accepted',
  })

  await assert.rejects(pending, /Host rejected reply/)
  assert.equal(prompt.replied, true)
})

test('Host-rejected UI reply preserves an authoritative superseded acknowledgement', async () => {
  const { createHostAcceptedChat } = await helpers()
  const prompt = {
    role: 'ui_event',
    eventId: 'event-1',
    replied: true,
    superseded: true,
  }
  const chat = {
    messagesByTab: { worker: [prompt] },
    submitUIReply() {
      Object.assign(prompt, {
        replied: true,
        repliedActionId: 'submit',
        repliedValues: {},
      })
      return true
    },
  }
  const BridgeWebSocket = {
    captureSend(callback) {
      callback()
      return {
        frame: JSON.stringify({
          type: 'ui_reply',
          target: 'worker',
          event_id: 'event-1',
        }),
        confirmation: Promise.reject(Error('Host rejected reply')),
      }
    },
  }
  const accepted = createHostAcceptedChat({ BridgeWebSocket, chat })
  const pending = accepted.submitUIReply('worker', 'event-1', 'submit', {})
  accepted.observe({
    type: 'ui_reply_ack',
    source: 'worker',
    event_id: 'event-1',
    status: 'superseded',
  })

  await assert.rejects(pending, /Host rejected reply/)
  assert.equal(prompt.superseded, true)
})

test('processing end during rejection is not overwritten by stale pre-submit state', async () => {
  const { createHostAcceptedChat } = await helpers()
  const chat = {
    activeTab: 'worker',
    messagesByTab: { worker: [] },
    queuedMessagesByTab: { worker: [] },
    processingByTab: { worker: true },
    send() {
      this.queuedMessagesByTab.worker.push({ role: 'user', eventId: 'event-1' })
    },
  }
  const BridgeWebSocket = {
    captureSend(callback) {
      callback()
      return {
        frame: JSON.stringify({
          type: 'input',
          target: 'worker',
          event_id: 'event-1',
        }),
        confirmation: Promise.reject(Error('Host rejected send')),
      }
    },
  }
  const accepted = createHostAcceptedChat({ BridgeWebSocket, chat })
  const pending = accepted.send('hello')
  chat.processingByTab.worker = false

  await assert.rejects(pending, /Host rejected send/)
  assert.equal(chat.processingByTab.worker, false)
})

test('stale input rejection restores the original tab after selection rotation', async () => {
  const { createHostAcceptedChat } = await helpers()
  const message = { role: 'user', eventId: 'event-1', content: 'hello' }
  const chat = {
    activeTab: 'worker',
    messagesByTab: { worker: [], other: [] },
    queuedMessagesByTab: { worker: [], other: [] },
    processingByTab: { worker: false, other: false },
    send() {
      this.messagesByTab.worker.push(message)
      this.processingByTab.worker = true
    },
  }
  const BridgeWebSocket = {
    captureSend(callback) {
      callback()
      return {
        frame: JSON.stringify({
          type: 'input',
          target: 'worker',
          event_id: 'event-1',
        }),
        confirmation: Promise.reject(Error('Host rejected send')),
      }
    },
  }
  const accepted = createHostAcceptedChat({ BridgeWebSocket, chat })
  const pending = accepted.send('hello')
  chat.activeTab = 'other'

  await assert.rejects(pending, /Host rejected send/)
  assert.deepEqual(chat.messagesByTab.worker, [])
  assert.equal(chat.processingByTab.worker, false)
  assert.equal(chat.processingByTab.other, false)
})

test('pending UI reply rejects duplicate clicks until Host settles', async () => {
  const { createHostAcceptedChat } = await helpers()
  let resolveConfirmation
  const confirmation = new Promise((resolve) => (resolveConfirmation = resolve))
  let sends = 0
  const chat = {
    messagesByTab: {
      worker: [{ role: 'ui_event', eventId: 'event-1', replied: false }],
    },
    submitUIReply() {
      sends += 1
      return true
    },
  }
  const BridgeWebSocket = {
    captureSend(callback) {
      callback()
      return {
        frame: JSON.stringify({
          type: 'ui_reply',
          target: 'worker',
          event_id: 'event-1',
        }),
        confirmation,
      }
    },
  }
  const accepted = createHostAcceptedChat({ BridgeWebSocket, chat })

  const first = accepted.submitUIReply('worker', 'event-1', 'submit', {})
  await assert.rejects(accepted.submitUIReply('worker', 'event-1', 'submit', {}), /already pending/)
  resolveConfirmation()
  await first
  assert.equal(sends, 1)
})

test('failed UI reply can be retried after Host settlement', async () => {
  const { createHostAcceptedChat } = await helpers()
  const prompt = { role: 'ui_event', eventId: 'event-1', replied: false }
  let attempt = 0
  const chat = {
    messagesByTab: { worker: [prompt] },
    submitUIReply(tab, eventId, actionId, values) {
      Object.assign(prompt, {
        replied: true,
        repliedActionId: actionId,
        repliedValues: values,
      })
      return true
    },
  }
  const BridgeWebSocket = {
    captureSend(callback) {
      callback()
      attempt += 1
      return {
        frame: JSON.stringify({
          type: 'ui_reply',
          target: 'worker',
          event_id: 'event-1',
        }),
        confirmation: attempt === 1 ? Promise.reject(Error('Host rejected reply')) : Promise.resolve(),
      }
    },
  }
  const accepted = createHostAcceptedChat({ BridgeWebSocket, chat })

  await assert.rejects(accepted.submitUIReply('worker', 'event-1', 'submit', {}), /Host rejected reply/)
  await accepted.submitUIReply('worker', 'event-1', 'submit', {})
  assert.equal(attempt, 2)
})
