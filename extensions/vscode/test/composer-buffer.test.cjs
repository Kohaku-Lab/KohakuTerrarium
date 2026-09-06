const assert = require('node:assert/strict')
const test = require('node:test')
const { reactive } = require('vue')

test('composer buffer tracks edits, selection changes and explicit invalidation', async () => {
  const { bindComposerBuffer } = await import('../src/webview/composerBuffer.mjs')
  const { createConversationDrafts } = await import('../src/webview/conversationOwnership.mjs')
  const owner = reactive({ readyId: 1, runtimeId: 'a', creatureId: 'one' })
  const buckets = createConversationDrafts(() => owner)
  const { model, revision } = bindComposerBuffer(buckets)
  model.value = 'draft a'
  assert.equal(buckets.get(), 'draft a')
  owner.creatureId = 'two'
  assert.equal(model.value, '')
  model.value = 'draft b'
  owner.creatureId = 'one'
  assert.equal(model.value, 'draft a')
  owner.readyId++
  assert.equal(model.value, 'draft a')
  buckets.clearAll()
  revision.value++
  assert.equal(model.value, '')
})
