const assert = require('node:assert/strict')
const test = require('node:test')

const { RuntimeHost } = require('../src/host/runtime.cjs')
const { ArtifactReader } = require('../src/host/artifactRead.cjs')

const IMG = '/api/sessions/graph_1/artifacts/img.png'

const PNG = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0])
const GIF = Uint8Array.from(Buffer.from('GIF89a' + 'x'.repeat(6), 'binary'))
const JPEG = Uint8Array.from([0xff, 0xd8, 0xff, 0xe0, 0, 0, 0, 0, 0, 0, 0, 0])
const WEBP = Uint8Array.from(Buffer.from('RIFF' + '\x00\x00\x00\x00' + 'WEBP' + 'VP8 ', 'binary'))
const SVG = Uint8Array.from(Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"></svg>'))

function deferred() {
  let resolve
  let reject
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

// Lets a read finish its namespace listing stage so the artifact fetch is in flight.
const flush = () => new Promise((resolve) => setImmediate(resolve))

function streamOf(chunks, gate = null) {
  let index = 0
  return {
    getReader() {
      return {
        async read() {
          if (gate) await gate.promise
          if (index >= chunks.length) return { done: true, value: undefined }
          return { done: false, value: chunks[index++] }
        },
      }
    },
  }
}

function response({ contentType, bytes = [], contentLength = null, ok = true, status = 200, gate = null, onSignal = null }) {
  return {
    ok,
    status,
    headers: {
      get: (name) => (name.toLowerCase() === 'content-type' ? contentType : name.toLowerCase() === 'content-length' ? contentLength : null),
    },
    body: streamOf(Array.isArray(bytes) ? bytes : [bytes], gate),
    ...(onSignal ? { signal: onSignal } : {}),
  }
}

function harness({ fetchImpl, runtimeOptions = {} } = {}) {
  const posts = []
  const fetchCalls = []
  const fetchStub =
    fetchImpl ||
    (async (url, options) => {
      fetchCalls.push({ url, options })
      return response({ contentType: 'image/png', bytes: [PNG] })
    })
  const client = {
    listOpen: async () => [
      {
        conversationId: 'conv-1',
        runtimeId: 'graph-live',
        savedName: 'graph_1',
        isLive: true,
        title: 'beta',
        kind: 'terrarium',
        creatures: [{ id: 'creature-beta', name: 'beta' }],
      },
    ],
    history: async () => ({ events: [] }),
    interrupt: async () => ({ ok: true }),
    active: async () => ({
      session_id: 'graph-live',
      type: 'terrarium',
      config_name: 'team',
      creatures: [{ creature_id: 'creature-beta', name: 'beta' }],
    }),
    creatureCommand: async () => ({ ok: true }),
    stop: async () => ({ status: 'stopped' }),
  }
  const listCalls = []
  const listOpenImpl = client.listOpen
  client.listOpen = async (options = {}) => {
    listCalls.push(options)
    return listOpenImpl(options)
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
    count: 0,
    begin() {
      return ++this.count
    },
    open(generation, socketId, factory, view) {
      this.views = this.views || new Map()
      this.views.set(socketId, view)
      return factory()
    },
    send: async () => true,
    closeSocket() {
      return true
    },
    closeGeneration() {},
  }
  const host = new RuntimeHost({
    client,
    state,
    sockets,
    post: (message) => posts.push(message),
    getDefaultCreature: () => '@kt-biome/creatures/swe',
    getWorkspacePath: () => 'C:/workspace',
    socketFactory: (url, protocols) => ({ url, protocols }),
    webSocketBase: 'ws://127.0.0.1:8000',
    token: 'host-secret',
    runtimeEpoch: 7,
    fetchImpl: fetchStub,
    ...runtimeOptions,
  })
  return { client, host, posts, sockets, state, fetchCalls, listCalls }
}

const readMessage = (path, overrides = {}) => ({ type: 'artifact.read', requestId: 2, path, readyId: 7, selectionVersion: 0, ...overrides })

test('a read for a ref observed in the selected history returns a data URL', async () => {
  const { client, host, posts, state } = harness()
  client.history = async () => ({ events: [{ image_url: { url: IMG } }] })
  state.selection = { session: 'graph-live', graph: 'graph-live', creature: 'beta', targetCreatureId: 'creature-beta' }
  await host.handle({ type: 'http.history', requestId: 1, session: 'graph-live', creature: 'beta' })
  assert.equal(posts[0].type, 'http.history.result')
  await host.handle(readMessage(IMG))
  assert.deepEqual(posts[1], {
    type: 'artifact.read.result',
    requestId: 2,
    data: { dataUrl: `data:image/png;base64,${Buffer.from(PNG).toString('base64')}` },
  })
})

test('artifact reads reject version zero bypass and mixed fields before fetching', async () => {
  const { host, fetchCalls } = harness()
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host.artifacts.admit(IMG)
  await assert.rejects(host.handle(readMessage(IMG, { selectionVersion: 0 })))
  await assert.rejects(host.handle(readMessage(IMG, { selectionVersion: 1, target: 'foreign' })))
  assert.equal(fetchCalls.length, 0)
})

test('unknown refs, foreign URLs, and traversal paths never reach the backend', async () => {
  const { host, state, fetchCalls } = harness()
  state.selection = { session: 'graph-live', graph: 'graph-live', creature: 'beta', targetCreatureId: 'creature-beta' }
  for (const path of [
    '/api/sessions/graph_1/artifacts/unknown.png',
    'https://127.0.0.1:8000/api/sessions/graph_1/artifacts/img.png',
    '/api/sessions/graph_1/artifacts/%252e%252e%252fsecret.png',
    '/api/sessions/graph_1/artifacts/img.png?x=1',
  ]) {
    await assert.rejects(() => host.handle(readMessage(path)))
    assert.equal(fetchCalls.length, 0, path)
  }
})

test('reads are authenticated, redirect-hostile, and stream with hard limits', async () => {
  const calls = []
  const fetchImpl = async (url, options) => {
    calls.push({ url, options })
    if (calls.length === 1) throw Error('fake redirect refused')
    return response({ contentType: 'image/webp', bytes: [WEBP] })
  }
  const { host } = harness({ fetchImpl })
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host.artifacts.admit(IMG)
  await assert.rejects(() => host.handle(readMessage(IMG, { selectionVersion: 1 })), /Artifact request failed/)
  await host.handle(readMessage(IMG, { selectionVersion: 1 }))
  assert.equal(calls[0].options.redirect, 'error')
  assert.equal(calls[1].options.headers['X-KT-Host-Token'], 'host-secret')
  assert.equal(calls[1].options.headers.authorization, undefined)
  assert.equal(calls[0].url, `http://127.0.0.1:8000${IMG}`)
  assert.match(calls[1].url, /artifacts\/img\.png$/)
})

test('MIME and signature must agree; SVG, HTML, and mismatches are rejected', async () => {
  const cases = [
    { contentType: 'image/svg+xml', bytes: [SVG] },
    { contentType: 'text/html', bytes: [SVG] },
    { contentType: 'image/png', bytes: [GIF] },
    { contentType: 'image/gif', bytes: [PNG] },
  ]
  for (const item of cases) {
    const { host } = harness({ fetchImpl: async () => response(item) })
    await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
    host.artifacts.admit(IMG)
    await assert.rejects(() => host.handle(readMessage(IMG, { selectionVersion: 1 })), /not supported/)
  }
  for (const item of [
    { contentType: 'image/gif', bytes: [GIF] },
    { contentType: 'image/jpeg', bytes: [JPEG] },
    { contentType: 'image/webp', bytes: [WEBP] },
  ]) {
    const { host, posts } = harness({ fetchImpl: async () => response(item) })
    await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
    host.artifacts.admit(IMG)
    await host.handle(readMessage(IMG, { selectionVersion: 1 }))
    assert.match(posts.at(-1).data.dataUrl, /^data:image\/(gif|jpeg|webp);base64,/)
  }
})

test('oversize artifacts are rejected from content-length or streamed bytes', async () => {
  const { host } = harness({
    fetchImpl: async () => response({ contentType: 'image/png', bytes: [PNG], contentLength: String(9 * 1024 * 1024) }),
  })
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host.artifacts.admit(IMG)
  await assert.rejects(() => host.handle(readMessage(IMG, { selectionVersion: 1 })), /too large/)

  const small = new ArtifactReader({
    base: 'http://127.0.0.1:8000',
    token: 'host-secret',
    maxBytes: 8,
    fetchImpl: async () => response({ contentType: 'image/png', bytes: [PNG] }),
  })
  const host2 = harness({ runtimeOptions: { artifactReader: small } }).host
  await host2.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host2.artifacts.admit(IMG)
  await assert.rejects(() => host2.handle(readMessage(IMG, { selectionVersion: 1 })), /too large/)
})

test('a read aborts on timeout, transport failure, and hung bodies', async () => {
  const { host } = harness({ runtimeOptions: { artifactTimeoutMs: 20 }, fetchImpl: () => new Promise(() => {}) })
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host.artifacts.admit(IMG)
  await assert.rejects(() => host.handle(readMessage(IMG, { selectionVersion: 1 })), /timed out/)

  const gate = deferred()
  const { host: host2 } = harness({ fetchImpl: async () => response({ contentType: 'image/png', bytes: [PNG], gate }) })
  await host2.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host2.artifacts.admit(IMG)
  const attempt = host2.handle(readMessage(IMG, { selectionVersion: 1 }))
  gate.reject(Error('socket hung up'))
  await assert.rejects(() => attempt, /failed/)
})

test('reads are concurrency-limited with fail-fast overflow', async () => {
  const gates = [deferred(), deferred(), deferred(), deferred()]
  let calls = 0
  const fetchImpl = async () => response({ contentType: 'image/png', bytes: [PNG], gate: gates[calls++] })
  const { host } = harness({ fetchImpl })
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host.artifacts.admit(IMG)
  const running = [1, 2, 3, 4].map((id) => host.handle(readMessage(IMG, { requestId: 10 + id, selectionVersion: 1 })))
  await Promise.resolve()
  const overflow = host.handle(readMessage(IMG, { selectionVersion: 1 }))
  await assert.rejects(() => overflow, /limit reached/)
  gates.forEach((gate) => gate.resolve())
  await Promise.all(running)
  assert.equal(calls, 4)
})

test('a selection change or generation rotation invalidates refs and rejects late reads', async () => {
  const { host, state, fetchCalls } = harness()
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  state.selection = { ...state.selection }
  await assert.rejects(() => host.handle(readMessage(IMG, { selectionVersion: 1 })))
  assert.equal(fetchCalls.length, 0)

  await host.handle({ type: 'session.select', requestId: 3, session: 'graph-live', creatureId: 'creature-beta' })
  host.artifacts.admit(IMG)
  await host.handle({ type: 'session.select', requestId: 4, session: 'graph-live', creatureId: 'creature-beta' })
  assert.equal(host.artifacts.allowed(IMG), false)
  await assert.rejects(() => host.handle(readMessage(IMG, { selectionVersion: 1 })))
})

test('a read admitted before a change fails delivery instead of returning stale bytes', async () => {
  const gate = deferred()
  const { host, state } = harness({ fetchImpl: async () => response({ contentType: 'image/png', bytes: [PNG], gate }) })
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host.artifacts.admit(IMG)
  const attempt = host.handle(readMessage(IMG, { selectionVersion: 1 }))
  await Promise.resolve()
  state.selection = { ...state.selection, creature: 'gamma' }
  gate.resolve()
  await assert.rejects(() => attempt, /ownership changed/)
})

test('dispose aborts in-flight reads and blocks new ones without leaking the token', async () => {
  const captured = []
  const gate = deferred()
  const { host } = harness({
    fetchImpl: async (url, options) => {
      captured.push(options.signal)
      return response({ contentType: 'image/png', bytes: [PNG], gate })
    },
  })
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host.artifacts.admit(IMG)
  const attempt = host.handle(readMessage(IMG, { selectionVersion: 1 }))
  await flush()
  host.dispose()
  assert.equal(captured[0].aborted, true)
  gate.reject(Error('aborted by dispose'))
  await assert.rejects(() => attempt)
  await assert.rejects(() => host.handle(readMessage(IMG, { selectionVersion: 1 })))
})

test('an explicit selection intent aborts the in-flight read before delivery', async () => {
  const captured = []
  const gate = deferred()
  const { host } = harness({
    fetchImpl: async (url, options) => {
      captured.push(options.signal)
      return response({ contentType: 'image/png', bytes: [PNG], gate })
    },
  })
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host.artifacts.admit(IMG)
  const attempt = host.handle(readMessage(IMG, { selectionVersion: 1 }))
  await flush()
  await host.handle({ type: 'session.select', requestId: 2, session: 'graph-live', creatureId: 'creature-beta' })
  assert.equal(captured[0].aborted, true)
  gate.resolve()
  await assert.rejects(() => attempt, /ownership changed/)
})

test('the reader rejects preaborted signals and non-canonical paths before fetching', async () => {
  let fetched = false
  const reader = new ArtifactReader({
    base: 'http://127.0.0.1:8000',
    fetchImpl: async () => {
      fetched = true
      throw Error('must not fetch')
    },
  })
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(() => reader.read(IMG, { signal: controller.signal }), /cancelled/)
  await assert.rejects(() => reader.read('/api/sessions/graph_1/artifacts/../secret.png'), /Unknown artifact reference/)
  assert.equal(fetched, false)
})

test('frames register refs on the Host post path before the frame reaches the webview', async () => {
  const { host, posts } = harness()
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  const frame = { type: 'ws.frame', socketId: 1, data: JSON.stringify({ parts: [{ image_url: { url: IMG } }] }) }
  await host.handle({ type: 'ws.open', socketId: 1 })
  const view = host.sockets.views.get(1)
  view.postMessage(frame)
  assert.equal(host.artifacts.allowed(IMG), true)
  assert.deepEqual(
    posts.find((message) => message.type === 'ws.frame'),
    frame,
  )
})

test('history observed without an intent change still admits refs', async () => {
  const { client, host } = harness()
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  client.history = async () => ({ events: [{ image_url: { url: IMG } }] })
  await host.handle({ type: 'http.history', requestId: 2, session: 'graph-live', creature: 'beta' })
  assert.equal(host.artifacts.allowed(IMG), true)
})

test('history observed during an explicit selection intent is not admitted even if the pointer is unchanged', async () => {
  const gate = deferred()
  const { client, host, state } = harness()
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  const selected = state.selection
  client.history = () => gate.promise
  const history = host.handle({ type: 'http.history', requestId: 2, session: 'graph-live', creature: 'beta' })
  await Promise.resolve()
  // Reconcile keeps the same selection pointer and version, but explicit intent still supersedes.
  await host.handle({ type: 'session.reconcile', requestId: 3 })
  assert.equal(state.selection, selected)
  gate.resolve({ events: [{ image_url: { url: IMG } }] })
  await history
  assert.equal(host.artifacts.allowed(IMG), false)
  await assert.rejects(() => host.handle(readMessage(IMG, { selectionVersion: 1 })))
})

test('a read for an observed ref in another namespace is rejected before any artifact fetch', async () => {
  const { host, state, fetchCalls, listCalls } = harness()
  state.selection = { session: 'graph-live', graph: 'graph-live', creature: 'beta', targetCreatureId: 'creature-beta' }
  const foreign = '/api/sessions/other_ns/artifacts/leak.png'
  host.artifacts.admit(foreign)
  await assert.rejects(() => host.handle(readMessage(foreign)), /Unknown artifact reference/)
  assert.equal(fetchCalls.length, 0)
  assert.equal(listCalls.length, 1)
})

test('reads require a current live listing row whose trusted saved name matches the namespace', async () => {
  const { client, host, state, fetchCalls } = harness()
  state.selection = { session: 'graph-live', graph: 'graph-live', creature: 'beta', targetCreatureId: 'creature-beta' }
  host.artifacts.admit(IMG)
  const scenarios = [
    [],
    [{ runtimeId: 'other-live', savedName: 'graph_1', isLive: true, creatures: [] }],
    [{ runtimeId: 'graph-live', savedName: 'graph_1', isLive: false, creatures: [] }],
    [{ runtimeId: 'graph-live', savedName: null, isLive: true, creatures: [] }],
    [{ runtimeId: 'graph-live', savedName: 'other_ns', isLive: true, creatures: [] }],
  ]
  for (const sessions of scenarios) {
    client.listOpen = async () => sessions
    await assert.rejects(() => host.handle(readMessage(IMG)), /Unknown artifact reference/, JSON.stringify(sessions))
    assert.equal(fetchCalls.length, 0, JSON.stringify(sessions))
  }
})

test('a pending explicit selection intent rejects new reads before any HTTP', async () => {
  const gate = deferred()
  const { client, host, fetchCalls, listCalls } = harness()
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host.artifacts.admit(IMG)
  client.active = () => gate.promise
  const parked = host.handle({ type: 'session.select', requestId: 2, session: 'graph-live', creatureId: 'creature-beta' })
  await Promise.resolve()
  await assert.rejects(() => host.handle(readMessage(IMG, { selectionVersion: 1 })), /ownership changed/)
  assert.equal(fetchCalls.length, 0)
  assert.equal(listCalls.length, 0)
  gate.resolve({ session_id: 'graph-live', type: 'terrarium', creatures: [{ creature_id: 'creature-beta', name: 'beta' }] })
  await parked
  host.artifacts.admit(IMG)
  await host.handle(readMessage(IMG, { requestId: 3, selectionVersion: 1 }))
  assert.equal(fetchCalls.length, 1)
})

test('a namespace lookup that hangs aborts the whole read via the shared timer', async () => {
  const { client, host, fetchCalls } = harness({ runtimeOptions: { artifactTimeoutMs: 25 } })
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host.artifacts.admit(IMG)
  const signals = []
  client.listOpen = async (options = {}) => {
    signals.push(options.signal)
    return new Promise(() => {})
  }
  await assert.rejects(() => host.handle(readMessage(IMG, { selectionVersion: 1 })), /timed out/)
  assert.equal(signals[0]?.aborted, true)
  assert.equal(fetchCalls.length, 0)
})

test('the namespace listing stage counts toward the artifact concurrency cap', async () => {
  const gates = [deferred(), deferred(), deferred(), deferred()]
  const { client, host, fetchCalls } = harness()
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host.artifacts.admit(IMG)
  const trustedRow = await client.listOpen()
  client.listOpen = async () => gates[0].promise
  const running = [1, 2, 3, 4].map((id) => host.handle(readMessage(IMG, { requestId: 20 + id, selectionVersion: 1 })))
  await Promise.resolve()
  await assert.rejects(() => host.handle(readMessage(IMG, { selectionVersion: 1 })), /limit reached/)
  assert.equal(fetchCalls.length, 0)
  gates[0].resolve(trustedRow)
  await Promise.all(running)
  assert.equal(fetchCalls.length, 4)
})

test('dispose aborts reads parked in the namespace listing stage', async () => {
  const gate = deferred()
  const signals = []
  const { client, host } = harness()
  await host.handle({ type: 'session.select', requestId: 1, session: 'graph-live', creatureId: 'creature-beta' })
  host.artifacts.admit(IMG)
  client.listOpen = async (options = {}) => {
    signals.push(options.signal)
    return gate.promise
  }
  const attempt = host.handle(readMessage(IMG, { selectionVersion: 1 }))
  await Promise.resolve()
  host.dispose()
  gate.reject(Error('aborted by dispose'))
  await assert.rejects(() => attempt, /ownership changed|aborted/)
  assert.equal(signals[0]?.aborted, true)
})
