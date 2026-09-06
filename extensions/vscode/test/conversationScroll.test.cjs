const assert = require('node:assert/strict')
const test = require('node:test')

const {
  createConversationMessageOrchestrator,
  createConversationScrollController,
  NEAR_BOTTOM_THRESHOLD,
} = require('../src/webview/conversationScroll.mjs')

function viewport({ scrollTop = 0, scrollHeight = 1000, clientHeight = 200 } = {}) {
  return { scrollTop, scrollHeight, clientHeight }
}

function harness() {
  const jobs = []
  const controller = createConversationScrollController({
    schedule: (job) => jobs.push(job),
  })
  return {
    controller,
    flush: () => {
      while (jobs.length) jobs.shift()()
    },
  }
}

test('uses Dashboard near-bottom threshold and follows only while near the bottom', () => {
  assert.equal(NEAR_BOTTOM_THRESHOLD, 80)
  const { controller, flush } = harness()
  const el = viewport({ scrollTop: 721 })
  controller.setIdentity('session-a:creature-a', { hasMessages: true })
  controller.onViewportReady(el)
  flush()
  el.scrollTop = 721
  controller.onScroll({ target: el })
  el.scrollHeight = 1100
  controller.onMessagesUpdated({ hasMessages: true })
  flush()
  assert.equal(el.scrollTop, 1100)

  el.scrollTop = 820
  controller.onScroll({ target: el })
  el.scrollHeight = 1200
  controller.onMessagesUpdated({ hasMessages: true })
  flush()
  assert.equal(el.scrollTop, 820)

  el.scrollTop = 700
  controller.onScroll({ target: el })
  el.scrollHeight = 1200
  controller.onMessagesUpdated({ hasMessages: true })
  flush()
  assert.equal(el.scrollTop, 700)
})

test('restores positions per Session and Creature and defaults a new identity to bottom', () => {
  const { controller, flush } = harness()
  const el = viewport({ scrollTop: 0 })
  controller.onViewportReady(el)
  controller.setIdentity('session-a:creature-a', { hasMessages: true })
  flush()
  assert.equal(el.scrollTop, 1000)
  el.scrollTop = 320
  controller.onScroll({ target: el })

  controller.setIdentity('session-b:creature-a', { hasMessages: true })
  flush()
  assert.equal(el.scrollTop, 1000)
  el.scrollTop = 460
  controller.onScroll({ target: el })

  controller.setIdentity('session-a:creature-a', { hasMessages: true })
  flush()
  assert.equal(el.scrollTop, 320)
})

test('waits for delayed initial history before bottoming or restoring', () => {
  const { controller, flush } = harness()
  const el = viewport({ scrollHeight: 0, clientHeight: 200 })
  controller.setIdentity('session-a:creature-a', { hasMessages: false })
  controller.onViewportReady(el)
  flush()
  assert.equal(el.scrollTop, 0)
  el.scrollHeight = 900
  controller.onMessagesUpdated({ hasMessages: true })
  flush()
  assert.equal(el.scrollTop, 900)
})

test('submission force-follows immediately and through the next stream update', () => {
  const { controller, flush } = harness()
  const el = viewport({ scrollTop: 300 })
  controller.setIdentity('session-a:creature-a', { hasMessages: true })
  controller.onViewportReady(el)
  flush()
  el.scrollTop = 250
  controller.onScroll({ target: el })
  controller.forceFollow()
  flush()
  assert.equal(el.scrollTop, 1000)
  el.scrollHeight = 1080
  controller.onMessagesUpdated({ hasMessages: true })
  flush()
  assert.equal(el.scrollTop, 1080)
})

test('preserves the reading anchor when earlier history is prepended', () => {
  const { controller, flush } = harness()
  const el = viewport({ scrollTop: 240, scrollHeight: 1000 })
  controller.setIdentity('session-a:creature-a', { hasMessages: true })
  controller.onViewportReady(el)
  flush()
  el.scrollTop = 240
  controller.onScroll({ target: el })
  const complete = controller.beforePrepend()
  el.scrollHeight = 1380
  complete()
  flush()
  assert.equal(el.scrollTop, 620)
})

test('message orchestration captures prepend before render and preserves append follow policy', () => {
  const { controller, flush } = harness()
  const orchestrator = createConversationMessageOrchestrator(controller)
  const el = viewport({ scrollTop: 240, scrollHeight: 1000 })
  const current = [{ id: 'b' }, { id: 'c' }]
  controller.setIdentity('same', { hasMessages: true })
  controller.onViewportReady(el)
  flush()
  el.scrollTop = 240
  controller.onScroll({ target: el })

  orchestrator.beforeMessagesChange('same', current, 'same', [{ id: 'a' }, ...current])
  el.scrollHeight = 1380 // Vue renders only after the synchronous watcher captured 1000.
  orchestrator.afterMessagesChange('same', [{ id: 'a' }, ...current])
  assert.equal(el.scrollTop, 240)
  flush()
  assert.equal(el.scrollTop, 620)

  el.scrollTop = 1200
  controller.onScroll({ target: el })
  orchestrator.beforeMessagesChange('same', current, 'same', [...current, { id: 'd' }])
  el.scrollHeight = 1500
  orchestrator.afterMessagesChange('same', [...current, { id: 'd' }])
  flush()
  assert.equal(el.scrollTop, 1500)

  el.scrollTop = 400
  controller.onScroll({ target: el })
  orchestrator.beforeMessagesChange('old', current, 'current', [{ id: 'a' }, ...current])
  el.scrollHeight = 1700
  orchestrator.afterMessagesChange('current', [{ id: 'a' }, ...current])
  flush()
  assert.equal(el.scrollTop, 400)
})

test('identity-bound viewport and scroll callbacks cannot affect a later identity', () => {
  const { controller, flush } = harness()
  const el = viewport({ scrollTop: 100 })
  controller.setIdentity('old', { hasMessages: true })
  controller.onViewportReady(el, 'old')
  flush()
  el.scrollTop = 100
  controller.onScroll({ target: el }, 'old')

  controller.setIdentity('current', { hasMessages: true })
  flush()
  assert.equal(el.scrollTop, 1000)
  el.scrollTop = 650
  controller.onViewportReady(el, 'old')
  controller.onScroll({ target: el }, 'old')

  assert.equal(controller.getSavedPosition('old'), 100)
  assert.equal(controller.getSavedPosition('current'), 1000)
})

test('cleanup and stale viewport, update, and prepend callbacks cannot move the current viewport', () => {
  const { controller, flush } = harness()
  const old = viewport({ scrollTop: 100 })
  const current = viewport({ scrollTop: 200 })
  controller.setIdentity('old', { hasMessages: true })
  controller.onViewportReady(old)
  flush()
  old.scrollTop = 100
  controller.onScroll({ target: old })
  const stalePrepend = controller.beforePrepend()
  controller.setIdentity('current', { hasMessages: true })
  controller.onViewportReady(current)
  old.scrollHeight = 1400
  stalePrepend()
  flush()
  assert.equal(old.scrollTop, 100)
  assert.equal(current.scrollTop, 1000)
  controller.onScroll({ target: old })
  assert.equal(controller.getSavedPosition('current'), 1000)
  controller.dispose()
  current.scrollHeight = 1300
  controller.forceFollow()
  controller.onMessagesUpdated({ hasMessages: true })
  flush()
  assert.equal(current.scrollTop, 1000)
})
