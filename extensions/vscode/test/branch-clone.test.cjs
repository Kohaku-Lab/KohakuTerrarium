const assert = require('node:assert/strict')
const { once } = require('node:events')
const { MessageChannel } = require('node:worker_threads')
const test = require('node:test')

const { reactive } = require('vue')

const { allowedMessage } = require('../src/host/protocol.cjs')

for (const operation of ['Regenerate', 'EditMessage']) {
  for (const field of ['locator', 'branchView']) {
    test(`${operation} snapshots a reactive ${field} before the real MessagePort boundary`, async () => {
      const { installBranchBridge } = await import('../src/webview/branchBridge.mjs')
      const { createRequestLifecycle } = await import('../src/webview/requestLifecycle.mjs')
      const { port1, port2 } = new MessageChannel()
      const lifecycle = createRequestLifecycle({ postMessage: (message) => port1.postMessage(message) })
      const uninstall = installBranchBridge({ request: lifecycle.request, getOwner: () => ({ admittedReadyId: 5 }) })
      const target = { turnIndex: 3, requestId: 'correlation', locator: { eventId: 9, turnIndex: 3, branchId: 2 }, branchView: { 1: 2 } }
      target[field] = reactive(target[field])
      try {
        let failure
        const pending =
          operation === 'Regenerate'
            ? globalThis.__ktVsCodeRegenerate('g', 'alice', target)
            : globalThis.__ktVsCodeEditMessage('g', 'alice', 2, [{ type: 'text', text: 'edited' }], target)
        const observed = pending.catch((error) => {
          failure = error
        })
        await Promise.resolve()
        await Promise.resolve()
        assert.equal(failure, undefined, 'the branch DTO must be structured-cloneable')
        const [received] = await once(port2, 'message')
        assert.equal(allowedMessage(received), true)
        assert.deepEqual(received.locator, { eventId: 9, turnIndex: 3, branchId: 2 })
        assert.deepEqual(received.branchView, { 1: 2 })
        target.locator.eventId = 99
        target.branchView[1] = 3
        assert.equal(received.locator.eventId, 9)
        assert.equal(received.branchView[1], 2)
        const completed = { status: 'completed', branch_id: 4, turn_index: 3 }
        lifecycle.settle({ type: `${received.type}.result`, requestId: received.requestId, data: completed })
        assert.deepEqual(await pending, completed)
        await observed
        assert.equal(lifecycle.size(), 0)
      } finally {
        uninstall()
        lifecycle.rejectAll(Error('test closed'))
        port1.close()
        port2.close()
      }
    })
  }
}
