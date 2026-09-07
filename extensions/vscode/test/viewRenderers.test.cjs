const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const root = path.resolve(__dirname, '..')

function ref(value) {
  return { value }
}

async function renderers(overrides = {}) {
  const { createViewRenderers } = await import(pathToFileURL(path.join(root, 'src', 'webview', 'viewRenderers.mjs')))
  return createViewRenderers({
    ConversationMessage: { name: 'ConversationMessage' },
    MarkdownRenderer: { name: 'MarkdownRenderer' },
    available: ref(true),
    busy: ref(false),
    currentSession: ref(null),
    openSession: () => {},
    resumeSession: () => {},
    ...overrides,
  })
}

test('icon renders the requested shared action path', async () => {
  const { icon } = await renderers()

  const chevron = icon('chevron')
  assert.equal(chevron.type, 'svg')
  assert.equal(chevron.props.class, 'action-icon action-icon--chevron')
  assert.match(chevron.children[0].props.d, /^M16 22/)
})

test('renderSession resumes dormant sessions through the provided callback', async () => {
  const resumed = []
  const { renderSession } = await renderers({
    resumeSession: (session) => resumed.push(session),
  })
  const session = {
    conversationId: 'saved-1',
    savedName: 'saved.kohakutr',
    title: 'Saved',
    isLive: false,
    creatures: [],
  }

  const row = renderSession(session)
  assert.equal(row.type, 'button')
  assert.equal(row.props['aria-label'], 'Resume Session Saved')
  row.props.onClick()
  assert.deepEqual(resumed, [session])
})

test('renderSession opens a single live creature', async () => {
  const opened = []
  const { renderSession } = await renderers({
    openSession: (session, creatureId) => opened.push([session, creatureId]),
  })
  const session = {
    conversationId: 'live-1',
    runtimeId: 'runtime-1',
    title: 'Live',
    isLive: true,
    creatures: [{ id: 'creature-1', name: 'Kohaku' }],
  }

  const row = renderSession(session)
  assert.equal(row.type, 'button')
  assert.equal(row.props['aria-label'], 'Open Session Live')
  row.props.onClick()
  assert.deepEqual(opened, [[session, 'creature-1']])
})

test('renderSession renders a multi-creature group with independently openable rows', async () => {
  const opened = []
  const { renderSession } = await renderers({
    currentSession: ref({
      session: { runtimeId: 'runtime-1' },
      targetCreatureId: 'creature-2',
    }),
    openSession: (session, creatureId) => opened.push([session, creatureId]),
  })
  const session = {
    conversationId: 'live-1',
    runtimeId: 'runtime-1',
    title: 'Team',
    isLive: true,
    creatures: [
      { id: 'creature-1', name: 'Kohaku' },
      { id: 'creature-2', name: 'Reviewer' },
    ],
  }

  const group = renderSession(session)
  assert.equal(group.type, 'div')
  assert.equal(group.children.length, 3)
  assert.ok(group.children[0].props.class.includes('is-active'))
  assert.ok(group.children[2].props.class.includes('is-active'))
  group.children[1].props.onClick()
  assert.deepEqual(opened, [[session, 'creature-1']])
})
