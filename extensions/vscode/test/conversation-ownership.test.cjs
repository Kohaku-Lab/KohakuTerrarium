const assert = require('node:assert/strict')
const test = require('node:test')

let createConversationAttachments
let createConversationDrafts
let createConversationOwnership
let isConversationSuperseded

test.before(async () => {
  ;({ createConversationAttachments, createConversationDrafts, createConversationOwnership, isConversationSuperseded } =
    await import('../src/webview/conversationOwnership.mjs'))
})

function deferred() {
  let resolve
  const promise = new Promise((done) => (resolve = done))
  return { promise, resolve }
}

function fixture() {
  let state = { readyId: 1, runtimeId: 'session-a', creatureId: 'root', name: 'Root' }
  const ownership = createConversationOwnership(() => state)
  return { ownership, setState: (value) => (state = value) }
}

test('accepted attachments are scoped by Session and Creature and restored on return', () => {
  let state = { readyId: 1, runtimeId: 'session-a', creatureId: 'root' }
  const buckets = createConversationAttachments(() => state)
  const fromA = [{ name: 'a.txt' }]
  buckets.set(fromA)

  state = { readyId: 1, runtimeId: 'session-b', creatureId: 'root' }
  assert.deepEqual(buckets.get(), [])
  buckets.set([{ name: 'b.txt' }])

  state = { readyId: 1, runtimeId: 'session-a', creatureId: 'other' }
  assert.deepEqual(buckets.get(), [])
  state = { readyId: 1, runtimeId: 'session-a', creatureId: 'root' }
  assert.equal(buckets.get(), fromA)
})

test('runtime refresh preserves stable conversation attachments and drafts', () => {
  let state = { readyId: 1, runtimeId: 'session-a', creatureId: 'root' }
  const buckets = createConversationAttachments(() => state)
  const accepted = [{ name: 'a.txt' }]
  const drafts = createConversationDrafts(() => state)
  drafts.set('mid-typing')
  buckets.set(accepted)
  assert.equal(buckets.get(), accepted)

  state = { ...state }
  assert.equal(buckets.get(), accepted)
  state = { ...state, readyId: 2 }
  assert.equal(buckets.get(), accepted)
  assert.equal(drafts.get(), 'mid-typing')
})

test('buckets evict the least recently used conversation and release all state on disposal', () => {
  let state = { readyId: 1, runtimeId: 'a', creatureId: 'root' }
  const attachments = createConversationAttachments(() => state, { maxBuckets: 2 })
  const drafts = createConversationDrafts(() => state, { maxBuckets: 2 })
  const ownerA = attachments.capture()
  attachments.set([{ name: 'a' }])
  drafts.set('a')
  state = { ...state, runtimeId: 'b' }
  const ownerB = attachments.capture()
  attachments.set([{ name: 'b' }])
  drafts.set('b')
  attachments.get(ownerA)
  drafts.get(ownerA)
  state = { ...state, runtimeId: 'c' }
  attachments.set([{ name: 'c' }])
  drafts.set('c')
  assert.deepEqual(attachments.get(ownerB), [])
  assert.equal(drafts.get(ownerB), '')
  assert.deepEqual(attachments.get(ownerA), [{ name: 'a' }])
  attachments.clearAll()
  drafts.clearAll()
  assert.deepEqual(attachments.get(ownerA), [])
  assert.equal(drafts.get(ownerA), '')
  attachments.set([{ name: 'new' }])
  drafts.set('new')
  attachments.dispose()
  drafts.dispose()
  attachments.set([{ name: 'late' }], ownerA)
  drafts.set('late', ownerA)
  assert.deepEqual(attachments.get(ownerA), [])
  assert.equal(drafts.get(ownerA), '')
})

test('late attachment confirmation does not promote an inactive bucket for eviction', () => {
  let state = { readyId: 1, runtimeId: 'a', creatureId: 'root' }
  const buckets = createConversationAttachments(() => state, { maxBuckets: 2 })
  const submitted = { name: 'submitted' }
  buckets.set([submitted, { name: 'a-later' }])
  const a = buckets.capture()
  state = { ...state, runtimeId: 'b' }
  buckets.set([{ name: 'b' }])
  const b = buckets.capture()
  buckets.removeSubmitted([submitted], a)
  state = { ...state, runtimeId: 'c' }
  buckets.set([{ name: 'c' }])
  assert.deepEqual(buckets.get(a), [])
  assert.deepEqual(buckets.get(b), [{ name: 'b' }])
})

test('dispose fences asynchronous conversion before dispatch even for the same owner', async () => {
  const { ownership } = fixture()
  const conversion = deferred()
  let sends = 0
  const pending = ownership.dispatch(async (assertCurrent) => {
    await conversion.promise
    assertCurrent()
    sends++
  })
  ownership.dispose()
  conversion.resolve()
  await assert.rejects(pending, isConversationSuperseded)
  assert.equal(sends, 0)
})

test('late send confirmation after refresh cannot clear a newly edited identical draft', () => {
  let state = { readyId: 1, runtimeId: 'a', creatureId: 'root' }
  const drafts = createConversationDrafts(() => state)
  drafts.set('same text')
  const submitted = drafts.capture()
  state = { ...state, readyId: 2 }
  drafts.set('different')
  drafts.set('same text')
  assert.equal(drafts.clearSubmitted('same text', submitted), false)
  assert.equal(drafts.get(), 'same text')
  const latest = drafts.capture()
  assert.equal(drafts.clearSubmitted('same text', latest), true)
  assert.equal(drafts.get(), '')
})

test('clear only removes the captured conversation bucket', () => {
  let state = { readyId: 1, runtimeId: 'session-a', creatureId: 'root' }
  const buckets = createConversationAttachments(() => state)
  const owned = buckets.capture()
  buckets.set([{ name: 'a.txt' }], owned)
  state = { readyId: 1, runtimeId: 'session-b', creatureId: 'root' }
  buckets.set([{ name: 'b.txt' }])
  buckets.clear(owned)
  assert.deepEqual(buckets.get(), [{ name: 'b.txt' }])
})

test('confirmed submissions remove only the submitted attachment objects', () => {
  const state = { readyId: 1, runtimeId: 'session-a', creatureId: 'root' }
  const buckets = createConversationAttachments(() => state)
  const owned = buckets.capture()
  const submitted = { name: 'same.txt' }
  const laterSameName = { name: 'same.txt' }
  buckets.set([submitted, laterSameName], owned)

  buckets.removeSubmitted([submitted], owned)

  assert.deepEqual(buckets.get(owned), [laterSameName])
})

test('confirmed submissions preserve attachments added while confirmation is pending', () => {
  const state = { readyId: 1, runtimeId: 'session-a', creatureId: 'root' }
  const buckets = createConversationAttachments(() => state)
  const owned = buckets.capture()
  const submitted = { name: 'a.txt' }
  const later = { name: 'b.txt' }
  buckets.set([submitted, later], owned)

  buckets.removeSubmitted([submitted], owned)

  assert.deepEqual(buckets.get(owned), [later])
})

test('confirmed submissions leave a re-added identical object in the bucket', () => {
  const state = { readyId: 1, runtimeId: 'session-a', creatureId: 'root' }
  const buckets = createConversationAttachments(() => state)
  const owned = buckets.capture()
  const submitted = { name: 'a.txt' }
  buckets.set([submitted, submitted], owned)

  buckets.removeSubmitted([submitted], owned)

  assert.deepEqual(buckets.get(owned), [submitted])
})

test('drafts are bucketed and late cleanup only clears the submitted owner', () => {
  let state = { readyId: 1, runtimeId: 'session-a', creatureId: 'root' }
  const drafts = createConversationDrafts(() => state)
  const owned = drafts.capture()
  drafts.set('submitted A')

  state = { readyId: 1, runtimeId: 'session-b', creatureId: 'root' }
  drafts.set('current B')
  drafts.clear(owned)
  assert.equal(drafts.get(), 'current B')

  state = owned
  assert.equal(drafts.get(), '')
})

test('dispatch permits authoritative completion after ownership switches', async () => {
  const { ownership, setState } = fixture()
  const confirmation = deferred()
  let dispatched = false
  const operation = ownership.dispatch(async (assertCurrent) => {
    assertCurrent()
    dispatched = true
    await confirmation.promise
    return 'accepted'
  })

  setState({ readyId: 1, runtimeId: 'session-b', creatureId: 'root', name: 'Root' })
  confirmation.resolve()
  assert.equal(await operation, 'accepted')
  assert.equal(dispatched, true)
})

test('attachment transform from another Session with the same creature name is superseded', async () => {
  const { ownership, setState } = fixture()
  const conversion = deferred()
  const transformed = ownership.transform(() => conversion.promise)('file')

  setState({ readyId: 1, runtimeId: 'session-b', creatureId: 'root', name: 'Root' })
  conversion.resolve('a-bytes')

  await assert.rejects(transformed, isConversationSuperseded)
})

test('attachment transform applies while exact ownership remains current', async () => {
  const { ownership } = fixture()
  const conversion = deferred()
  const transformed = ownership.transform(() => conversion.promise)('file')
  conversion.resolve('a-bytes')
  assert.equal(await transformed, 'a-bytes')
})

test('supplemental name changes do not supersede the same stable ownership', async () => {
  const { ownership, setState } = fixture()
  const conversion = deferred()
  const transformed = ownership.transform(() => conversion.promise)('file')

  setState({ readyId: 1, runtimeId: 'session-a', creatureId: 'root', name: 'Renamed' })
  conversion.resolve('a-bytes')

  assert.equal(await transformed, 'a-bytes')
})

test('runtime refresh supersedes attachment transform for the same Session and Creature', async () => {
  const { ownership, setState } = fixture()
  const conversion = deferred()
  const transformed = ownership.transform(() => conversion.promise)('file')

  setState({ readyId: 2, runtimeId: 'session-a', creatureId: 'root', name: 'Root' })
  conversion.resolve('old-bytes')

  await assert.rejects(transformed, isConversationSuperseded)
})

test('send rechecks ownership after content conversion and before dispatch', async () => {
  const { ownership, setState } = fixture()
  const conversion = deferred()
  let sent = false
  const operation = ownership.run(async (assertCurrent) => {
    const content = await conversion.promise
    assertCurrent()
    sent = true
    return content
  })

  setState({ readyId: 1, runtimeId: 'session-b', creatureId: 'root', name: 'Root' })
  conversion.resolve('a-bytes')

  await assert.rejects(operation, isConversationSuperseded)
  assert.equal(sent, false)
})
