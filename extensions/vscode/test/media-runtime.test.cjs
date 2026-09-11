const assert = require('node:assert/strict')
const test = require('node:test')

const { RuntimeHost } = require('../src/host/runtime.cjs')
const { MediaHost } = require('../src/host/mediaHost.cjs')
const { allowedMessage } = require('../src/host/protocol.cjs')

const REF = '/api/sessions/graph_1/artifacts/generated_videos/clip.mp4'
const flush = () => new Promise((resolve) => setImmediate(resolve))

function spoolStub(overrides = {}) {
  return {
    async start() {
      return '/spool-root'
    },
    async prepare(canonical, { name } = {}) {
      return {
        resourceId: 'r1',
        uri: 'vscode-webview://spool/r1',
        bytes: 3,
        mime: 'video/mp4',
        sha256: 'a'.repeat(64),
        name: name || 'r1',
        state: 'exposed',
      }
    },
    resource() {
      return null
    },
    acquire() {
      return true
    },
    release() {
      return true
    },
    discard() {
      return true
    },
    releaseAll() {},
    async dispose() {},
    ...overrides,
  }
}

function harness({ mediaHost = new MediaHost({ spool: spoolStub() }) } = {}) {
  const posts = []
  const client = {
    listOpen: async () => [],
    history: async () => ({ events: [] }),
    interrupt: async () => ({ ok: true }),
    active: async () => ({ session_id: 'graph-live', type: 'terrarium', creatures: [{ creature_id: 'creature-beta', name: 'beta' }] }),
    creatureCommand: async () => ({ ok: true }),
    stop: async () => ({ status: 'stopped' }),
  }
  const state = {
    selection: null,
    async updateSelection(selection) {
      this.selection = selection
    },
    async updateSelectionIf(selection, owns) {
      if (!owns()) return false
      this.selection = selection
      return true
    },
  }
  const sockets = {
    begin() {
      return 1
    },
    open() {},
    send: async () => true,
    closeSocket() {},
    closeGeneration() {},
  }
  const host = new RuntimeHost({
    client,
    state,
    sockets,
    post: (message) => posts.push(message),
    getDefaultCreature: () => '',
    getWorkspacePath: () => 'C:/workspace',
    socketFactory: () => ({}),
    webSocketBase: 'ws://127.0.0.1:8000',
    token: 'host-secret',
    runtimeEpoch: 7,
    mediaHost,
  })
  return { host, posts, state }
}

const prepare = (overrides = {}) => ({ type: 'media.prepare', requestId: 1, path: REF, readyId: 7, selectionVersion: 0, ...overrides })

test('media.prepare is routed through the coordinator and its result is posted', async () => {
  const mediaHost = new MediaHost({ spool: spoolStub() })
  const { host, posts, state } = harness({ mediaHost })
  state.selection = { session: 'graph-live', creature: 'beta', targetCreatureId: 'c' }
  await host.handle(prepare({ name: 'clip.mp4' }))
  assert.deepEqual(posts[0], {
    type: 'media.prepare.result',
    requestId: 1,
    data: {
      resourceId: 'r1',
      uri: 'vscode-webview://spool/r1',
      bytes: 3,
      mime: 'video/mp4',
      sha256: 'a'.repeat(64),
      name: 'clip.mp4',
      state: 'exposed',
    },
  })
})

test('a stale ready epoch or missing selection rejects media.prepare before the spool', async () => {
  let prepared = 0
  const mediaHost = new MediaHost({ spool: spoolStub({ prepare: async () => (prepared++, {}) }) })
  const { host, state } = harness({ mediaHost })
  await assert.rejects(() => host.handle(prepare()), /Select a Creature/)
  state.selection = { session: 'graph-live', creature: 'beta', targetCreatureId: 'c' }
  await assert.rejects(() => host.handle(prepare({ readyId: 999 })), /ownership changed/)
  assert.equal(prepared, 0)
})

test('an explicit selection intent aborts an in-flight media prepare', async () => {
  const captured = []
  const mediaHost = new MediaHost({
    spool: spoolStub({
      prepare: (canonical, { signal }) =>
        new Promise((_, reject) => {
          captured.push(signal)
          signal.addEventListener('abort', () => reject(Error('Media request cancelled')), { once: true })
        }),
    }),
  })
  const { host, state } = harness({ mediaHost })
  state.selection = { session: 'graph-live', creature: 'beta', targetCreatureId: 'c' }
  const attempt = host.handle(prepare())
  await flush()
  await host.handle({ type: 'session.clearSelection', requestId: 2 })
  assert.equal(captured[0].aborted, true)
  await assert.rejects(() => attempt, /cancelled/)
})

test('media messages are rejected when no coordinator is wired', async () => {
  const { host, state } = harness({ mediaHost: null })
  state.selection = { session: 'graph-live', creature: 'beta', targetCreatureId: 'c' }
  await assert.rejects(() => host.handle(prepare()), /Media is unavailable/)
})

test('the media protocol envelope is a closed, fixed-route surface', () => {
  assert.equal(allowedMessage(prepare({ name: 'clip.mp4' })), true)
  assert.equal(allowedMessage({ type: 'media.release', requestId: 2, resourceId: 'r1', lease: 'editor' }), true)
  assert.equal(allowedMessage({ type: 'media.cancel', requestId: 3, prepareRequestId: 1 }), true)
  assert.equal(allowedMessage({ type: 'media.open', requestId: 4, resourceId: 'r1' }), true)
  assert.equal(allowedMessage({ type: 'media.save', requestId: 5, resourceId: 'r1' }), true)
  // Raw file paths are permitted at the envelope level: only the Host's canonical
  // route resolution may block a raw path, so a compromised webview cannot name an
  // absolute URL as its own fetch target.
  assert.equal(allowedMessage(prepare({ path: '/abs/raw/clip.mp4' })), true)
  assert.equal(allowedMessage(prepare({ path: 'C:/raw/clip.mp4' })), true)
  // A path whose segment merely contains ``://`` is not an absolute URL; anchoring
  // the scheme (rather than scanning for ``://``) avoids misclassifying it.
  assert.equal(allowedMessage(prepare({ path: '/workspace/notes://draft/clip.mp4' })), true)
  assert.equal(allowedMessage(prepare({ path: '/api/sessions/graph_1/artifacts/a://b.mp4' })), true)
  for (const message of [
    { type: 'media.prepare', requestId: 1, path: REF, readyId: 7, selectionVersion: 0, token: 'secret' },
    { type: 'media.prepare', requestId: 1, path: 'https://evil.example/x.mp4', readyId: 7, selectionVersion: 0 },
    { type: 'media.prepare', requestId: 1, path: 'file:///etc/passwd', readyId: 7, selectionVersion: 0 },
    { type: 'media.prepare', requestId: 1, path: 'ws://evil.example/x', readyId: 7, selectionVersion: 0 },
    { type: 'media.prepare', requestId: 1, path: REF, readyId: 0, selectionVersion: 0 },
    { type: 'media.release', requestId: 2, resourceId: 'r1', lease: 'admin' },
    { type: 'media.cancel', requestId: 3 },
    { type: 'media.open', requestId: 4, resourceId: 'r1', endpoint: 'http://127.0.0.1:8000' },
  ])
    assert.equal(allowedMessage(message), false, JSON.stringify(message))
})
