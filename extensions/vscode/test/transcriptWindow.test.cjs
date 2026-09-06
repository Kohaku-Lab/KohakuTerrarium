const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')
const { computed, reactive } = require('vue')

const root = path.resolve(__dirname, '..')

async function helpers() {
  return import(pathToFileURL(path.join(root, 'src', 'webview', 'transcriptWindow.mjs')))
}

function messages(count) {
  return Array.from({ length: count }, (_, index) => ({ id: `m-${index}` }))
}

test('transcript window defaults to the latest bounded tail', async () => {
  const { createTranscriptWindow } = await helpers()
  const source = messages(1000)
  const window = createTranscriptWindow()

  const view = window.view(source, 'session-a:root')

  assert.equal(view.messages.length, 400)
  assert.equal(view.messages[0], source[600])
  assert.equal(view.messages.at(-1), source[999])
  assert.equal(view.messageOffset, 600)
  assert.equal(view.earlierCount, 600)
  assert.equal(view.totalCount, 1000)
  assert.equal(view.previousMessage, source[599])
  assert.equal(source.length, 1000)
})

test('transcript window keeps short and boundary-sized transcripts intact', async () => {
  const { createTranscriptWindow } = await helpers()
  const window = createTranscriptWindow()

  for (const count of [0, 399, 400]) {
    const source = messages(count)
    const view = window.view(source, 'session-a:root')
    assert.deepEqual(view.messages, source)
    assert.notEqual(view.messages, source)
    assert.equal(view.messageOffset, 0)
    assert.equal(view.earlierCount, 0)
    assert.equal(view.previousMessage, null)
  }
})

test('the default window stays bounded as live tail messages append', async () => {
  const { createTranscriptWindow } = await helpers()
  const window = createTranscriptWindow()
  const source = messages(400)
  window.view(source, 'session-a:root')
  source.push({ id: 'm-400' }, { id: 'm-401' })

  const view = window.view(source, 'session-a:root')

  assert.equal(view.messages.length, 400)
  assert.equal(view.messageOffset, 2)
  assert.equal(view.messages[0], source[2])
  assert.equal(view.messages.at(-1), source[401])
})

test('load earlier expands one page and preserves the live tail', async () => {
  const { createTranscriptWindow } = await helpers()
  const source = messages(1000)
  const window = createTranscriptWindow()
  window.view(source, 'session-a:root')

  assert.equal(window.expandEarlier(source, 'session-a:root'), true)
  let view = window.view(source, 'session-a:root')
  assert.equal(view.messageOffset, 200)
  assert.equal(view.messages.length, 800)
  assert.equal(view.messages.at(-1), source.at(-1))
  source.push(...messages(450).map((message) => ({ id: `live-${message.id}` })))
  view = window.view(source, 'session-a:root')
  assert.equal(view.messages.length, 800)
  assert.equal(view.messageOffset, 650)
  assert.equal(view.messages.at(-1), source.at(-1))

  assert.equal(window.expandEarlier(source, 'session-a:root'), true)
  assert.equal(window.expandEarlier(source, 'session-a:root'), true)
  assert.equal(window.expandEarlier(source, 'session-a:root'), false)
  view = window.view(source, 'session-a:root')
  assert.equal(view.messageOffset, 0)
  assert.equal(view.messages.length, 1450)
})

test('same-length sequence replacements reset pagination expansion', async () => {
  const { createMessageSequence, createTranscriptWindow } = await helpers()
  const window = createTranscriptWindow()
  const source = messages(1000)
  let sequence = createMessageSequence(source)
  window.view(source, 'session-a:root', sequence)
  window.expandEarlier(source, 'session-a:root', sequence)
  assert.equal(window.view(source, 'session-a:root', sequence).messageOffset, 200)

  const replacement = messages(1000).map((message) => ({ id: `branch-${message.id}` }))
  sequence = createMessageSequence(replacement)
  const view = window.view(replacement, 'session-a:root', sequence)

  assert.equal(view.messageOffset, 600)
  assert.equal(view.messages.length, 400)
})

test('identity changes and transcript shrinkage reset invalid expansion', async () => {
  const { createTranscriptWindow } = await helpers()
  const window = createTranscriptWindow()
  const source = messages(1000)
  window.view(source, 'session-a:root')
  window.expandEarlier(source, 'session-a:root')

  let view = window.view(messages(450), 'session-a:root')
  assert.equal(view.messageOffset, 50)
  assert.equal(view.messages.length, 400)

  view = window.view(messages(900), 'session-b:root')
  assert.equal(view.messageOffset, 500)
  assert.equal(view.messages.length, 400)
})

test('a reactive expansion revision invalidates the transcript view', async () => {
  const { createTranscriptWindow } = await helpers()
  const window = createTranscriptWindow()
  const source = messages(1000)
  const revision = reactive({ value: 0 })
  const view = computed(() => {
    revision.value
    return window.view(source, 'session-a:root')
  })
  assert.equal(view.value.messageOffset, 600)

  assert.equal(window.expandEarlier(source, 'session-a:root'), true)
  revision.value += 1

  assert.equal(view.value.messageOffset, 200)
  assert.equal(view.value.messages.length, 800)
})

test('tail content updates do not recompute the structural sequence', async () => {
  const { createMessageSequence, createMessageTailSignature } = await helpers()
  const source = reactive([
    { id: 'm-1', role: 'user', content: 'hello' },
    { id: 'm-2', role: 'assistant', content: 'a' },
  ])
  let sequenceRuns = 0
  let tailRuns = 0
  const sequence = computed(() => {
    sequenceRuns += 1
    return createMessageSequence(source)
  })
  const tail = computed(() => {
    tailRuns += 1
    return createMessageTailSignature(source)
  })
  const firstSequence = sequence.value
  const firstTail = tail.value

  source[1].content = 'answer'

  assert.equal(sequence.value, firstSequence)
  assert.equal(sequenceRuns, 1)
  assert.notEqual(tail.value, firstTail)
  assert.equal(tailRuns, 2)
})

test('tail signatures detect equal-length, reasoning, result, and nested child changes', async () => {
  const { createMessageTailSignature } = await helpers()
  const message = {
    id: 'm-1',
    role: 'assistant',
    content: 'same',
    parts: [
      { type: 'reasoning', text: 'think', signature: 'sig-a', source: 'analysis' },
      {
        type: 'tool',
        id: 'tool-1',
        status: 'done',
        result: { value: 'first' },
        children: [{ id: 'child-1', type: 'tool', status: 'running', result: '' }],
      },
    ],
  }
  const initial = createMessageTailSignature([message])

  assert.notEqual(createMessageTailSignature([{ ...message, content: 'diff' }]), initial)
  assert.notEqual(createMessageTailSignature([{ ...message, parts: [{ type: 'reasoning', text: 'other' }, message.parts[1]] }]), initial)
  assert.notEqual(
    createMessageTailSignature([{ ...message, parts: [message.parts[0], { ...message.parts[1], result: { value: 'other' } }] }]),
    initial,
  )
  assert.notEqual(
    createMessageTailSignature([{ ...message, parts: [{ ...message.parts[0], signature: 'sig-b' }, message.parts[1]] }]),
    initial,
  )
  assert.notEqual(createMessageTailSignature([{ ...message, parts: [{ ...message.parts[0], source: 'tool' }, message.parts[1]] }]), initial)
  assert.notEqual(
    createMessageTailSignature([
      {
        ...message,
        parts: [message.parts[0], { ...message.parts[1], children: [{ ...message.parts[1].children[0], status: 'done' }] }],
      },
    ]),
    initial,
  )
})

test('transcript bindings stay stable per identity and retain stale identity tokens', async () => {
  const { createTranscriptBindings } = await helpers()
  const calls = []
  const bindings = createTranscriptBindings({
    onViewportReady: (viewport, identity) => calls.push(['viewport', viewport, identity]),
    onScroll: (event, identity) => calls.push(['scroll', event, identity]),
    onReply: (payload) => calls.push(['reply', payload]),
  })

  const first = bindings.forIdentity('session-a:root')
  assert.equal(bindings.forIdentity('session-a:root'), first)
  const second = bindings.forIdentity('session-b:root')
  assert.notEqual(second, first)

  first.onViewportReady('old-viewport')
  first.onScroll('old-scroll')
  first.onReply({ actionId: 'accept' })
  assert.deepEqual(calls, [
    ['viewport', 'old-viewport', 'session-a:root'],
    ['scroll', 'old-scroll', 'session-a:root'],
    ['reply', { actionId: 'accept' }],
  ])
})
