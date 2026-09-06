// Real loopback HTTP workflow: RuntimeHost + createClient + a real ArtifactReader
// fetching from fixture servers. No fetch mocks; only the routes are scripted.
const assert = require('node:assert/strict')
const test = require('node:test')
const http = require('node:http')

const { RuntimeHost } = require('../src/host/runtime.cjs')
const { createClient } = require('../src/host/client.cjs')

const TOKEN = 'host-secret'
const RUNTIME = 'graph-live'
// The saved session namespace served by the backend differs from the runtime id.
const NAMESPACE = 'graph_1'
const REF = `/api/sessions/${NAMESPACE}/artifacts/scene.png`
const FOREIGN = '/api/sessions/other_ns/artifacts/leak.png'
const UNSEEN = `/api/sessions/${NAMESPACE}/artifacts/unseen.png`
const PNG = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0])
const GIF = Uint8Array.from(Buffer.from('GIF89a' + 'x'.repeat(6), 'binary'))
const SVG = Uint8Array.from(Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"></svg>'))
const PNG_URL = `data:image/png;base64,${Buffer.from(PNG).toString('base64')}`

const flush = () => new Promise((resolve) => setImmediate(resolve))
const until = async (get) => {
  for (let attempt = 0; attempt < 200 && !get(); attempt++) await new Promise((resolve) => setTimeout(resolve, 10))
}
function deferred() {
  let resolve
  const promise = new Promise((onResolve) => {
    resolve = onResolve
  })
  return { promise, resolve }
}

function json(response, body) {
  response.writeHead(200, { 'Content-Type': 'application/json' })
  response.end(JSON.stringify(body))
}

function serveArtifact(response, box, secondaryOrigin) {
  const chunks = (...parts) => parts.forEach((part) => response.write(part))
  switch (box.mode) {
    case 'redirect':
      response.writeHead(302, { Location: `${secondaryOrigin}${REF}` })
      return response.end()
    case 'html':
      response.writeHead(200, { 'Content-Type': 'text/html' })
      return response.end(PNG)
    case 'svg':
      response.writeHead(200, { 'Content-Type': 'image/svg+xml' })
      return response.end(SVG)
    case 'mismatch':
      response.writeHead(200, { 'Content-Type': 'image/png' })
      return response.end(GIF)
    case 'header-oversize':
      response.writeHead(200, { 'Content-Type': 'image/png', 'Content-Length': '24' })
      return response.end(Buffer.concat([PNG, PNG]))
    case 'stream-oversize':
      response.writeHead(200, { 'Content-Type': 'image/png' })
      chunks(PNG.subarray(0, 6), PNG.subarray(6))
      return response.end()
    case 'stall':
      response.writeHead(200, { 'Content-Type': 'image/png' })
      response.write(PNG.subarray(0, 6))
      return // never ends; the reader timeout aborts the socket
    case 'gated':
      response.writeHead(200, { 'Content-Type': 'image/png' })
      response.write(PNG.subarray(0, 6))
      return box.gate.promise.then(() => {
        try {
          chunks(PNG.subarray(6))
          response.end()
        } catch {}
      })
    default:
      response.writeHead(200, { 'Content-Type': 'image/png' })
      return response.end(PNG)
  }
}

// Strict fixture server: every route demands the exact host token up front.
async function startServer(t, handler) {
  const hits = []
  const server = http.createServer((request, response) => {
    const hit = { url: request.url, token: request.headers['x-kt-host-token'] ?? null, closed: false, rejected: false }
    hits.push(hit)
    request.on('error', () => {})
    response.on('error', () => {})
    if (hit.token !== TOKEN) {
      hit.rejected = true
      response.writeHead(401, { 'Content-Type': 'application/json' })
      response.end('{"error":"unauthorized"}')
      return
    }
    response.on('close', () => {
      hit.closed = true
    })
    handler(request, response)
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  t.after(async () => {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
  })
  return {
    server,
    hits,
    origin: `http://127.0.0.1:${server.address().port}`,
    hitsOf: (url) => hits.filter((hit) => hit.url === url && !hit.rejected),
  }
}

// Scripted routes: /sessions/open rows, history admission, and artifact behaviors.
async function startFixture(t, box) {
  const secondary = await startServer(t, (request, response) => json(response, { leaked: true }))
  const fixture = await startServer(t, (request, response) => {
    const { url } = request
    if (url === '/api/sessions/open') {
      return json(response, [
        {
          conversation_id: 'conv-1',
          runtime_id: RUNTIME,
          saved_name: NAMESPACE,
          is_live: true,
          type: 'terrarium',
          title: 'beta',
          creatures: [{ creature_id: 'creature-beta', name: 'beta' }],
        },
      ])
    }
    if (url === `/api/sessions/active/${RUNTIME}`) {
      return json(response, {
        session_id: RUNTIME,
        type: 'terrarium',
        config_name: 'team',
        creatures: [{ creature_id: 'creature-beta', name: 'beta' }],
      })
    }
    if (url === `/api/sessions/${RUNTIME}/creatures/beta/history`) {
      return json(response, { events: [{ image_url: { url: REF } }, { image_url: { url: FOREIGN } }] })
    }
    if (url === REF) return serveArtifact(response, box, secondary.origin)
    response.writeHead(404, { 'Content-Type': 'application/json' })
    response.end('{"error":"missing"}')
  })
  return { fixture, secondary }
}

// Same runtime harness structure as the other Host tests; SocketOwners stay stubbed.
function harness(base, runtimeOptions = {}) {
  const posts = []
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
    begin: () => 1,
    open: (_generation, _socketId, factory) => factory(),
    send: async () => true,
    closeSocket: () => true,
    closeGeneration: () => {},
  }
  const host = new RuntimeHost({
    client: createClient({ endpoint: base, token: TOKEN }),
    state,
    sockets,
    post: (message) => posts.push(message),
    getDefaultCreature: () => '@kt-biome/creatures/swe',
    getWorkspacePath: () => 'C:/workspace',
    socketFactory: (url, protocols) => ({ url, protocols }),
    webSocketBase: `ws://${new URL(base).host}`,
    token: TOKEN,
    runtimeEpoch: 7,
    ...runtimeOptions,
  })
  return { host, posts }
}

const read = (requestId, path, selectionVersion, readyId = 7) => ({ type: 'artifact.read', requestId, path, readyId, selectionVersion })
const versionOf = (posts, requestId) =>
  posts.find((message) => message.type === 'session.select.result' && message.requestId === requestId).data.selectionVersion
async function selectAndObserve(host, requestId) {
  await host.handle({ type: 'session.select', requestId, session: RUNTIME, creatureId: 'creature-beta' })
  await host.handle({ type: 'http.history', requestId: requestId + 1, session: RUNTIME, creature: 'beta' })
}

test('history admission drives an authenticated artifact read through the saved-session namespace', async (t) => {
  const box = { mode: 'png', gate: Promise.resolve() }
  const { fixture, secondary } = await startFixture(t, box)
  const { host, posts } = harness(fixture.origin)
  const artifactHits = () => fixture.hitsOf(REF)

  // Every route refuses anonymous and mistokened callers before doing anything.
  for (const route of ['/api/sessions/open', `/api/sessions/${RUNTIME}/creatures/beta/history`, REF]) {
    const anonymous = await fetch(`${fixture.origin}${route}`)
    assert.equal(anonymous.status, 401, route)
    await anonymous.body?.cancel()
  }
  assert.ok(fixture.hits.every((hit) => hit.rejected))

  await host.handle({ type: 'session.select', requestId: 1, session: RUNTIME, creatureId: 'creature-beta' })
  const version = versionOf(posts, 1)
  await host.handle({ type: 'http.history', requestId: 2, session: RUNTIME, creature: 'beta' })
  assert.equal(posts.at(-1).type, 'http.history.result')

  // The admitted ref resolves through the listing row's saved namespace and streams a data URL.
  await host.handle(read(3, REF, version))
  assert.deepEqual(posts.at(-1), { type: 'artifact.read.result', requestId: 3, data: { dataUrl: PNG_URL } })
  assert.equal(fixture.hitsOf('/api/sessions/open').length, 1)
  assert.equal(artifactHits().length, 1)
  assert.equal(artifactHits()[0].token, TOKEN)

  // Wrong fences reject before any HTTP happens.
  await assert.rejects(host.handle(read(4, REF, version + 1)), /ownership changed/)
  await assert.rejects(host.handle(read(5, REF, version, 6)), /ownership changed/)
  assert.equal(artifactHits().length, 1)

  // An unobserved ref and an observed foreign-namespace ref never reach the network.
  await assert.rejects(host.handle(read(6, UNSEEN, version)), /ownership changed|Unknown artifact reference/)
  assert.equal(artifactHits().length, 1)
  await assert.rejects(host.handle(read(7, FOREIGN, version)), /Unknown artifact reference/)
  assert.equal(fixture.hitsOf('/api/sessions/open').length, 2)
  assert.equal(artifactHits().length, 1)

  // A redirect to a second server is refused and that server never receives the token.
  box.mode = 'redirect'
  await assert.rejects(host.handle(read(8, REF, version)), /Artifact request failed/)
  assert.equal(secondary.hits.length, 0)

  assert.deepEqual(
    posts.filter((message) => message.type === 'artifact.read.result'),
    [{ type: 'artifact.read.result', requestId: 3, data: { dataUrl: PNG_URL } }],
  )
  assert.ok(fixture.hits.every((hit) => hit.token === TOKEN || hit.rejected))
  host.dispose()
})

test('stream limits, stalled bodies, and cancelled reads never deliver artifact bytes', async (t) => {
  const box = { mode: 'png', gate: Promise.resolve() }
  const { fixture } = await startFixture(t, box)
  const a = harness(fixture.origin)
  const b = harness(fixture.origin, { artifactMaxBytes: 8, artifactTimeoutMs: 250 })
  await selectAndObserve(a.host, 1)
  await selectAndObserve(b.host, 10)
  const va = versionOf(a.posts, 1)
  const vb = versionOf(b.posts, 10)
  const artifactHits = () => fixture.hitsOf(REF)

  // MIME and signature must agree; HTML, SVG, and mismatches are rejected after fetch.
  const modes = [
    ['html', /type is not supported/],
    ['svg', /type is not supported/],
    ['mismatch', /content is not supported/],
  ]
  for (const [index, [mode, pattern]] of modes.entries()) {
    box.mode = mode
    await assert.rejects(a.host.handle(read(3 + index, REF, va)), pattern)
  }
  assert.equal(artifactHits().length, 3)

  // Small limits reject oversize from the declared length and from streamed bytes.
  box.mode = 'header-oversize'
  await assert.rejects(b.host.handle(read(12, REF, vb)), /too large/)
  box.mode = 'stream-oversize'
  await assert.rejects(b.host.handle(read(13, REF, vb)), /too large/)

  // A stalled body trips the shared timer and the aborted socket closes server-side.
  box.mode = 'stall'
  await assert.rejects(b.host.handle(read(14, REF, vb)), /timed out/)
  const stallHit = artifactHits().at(-1)
  await until(() => stallHit.closed)
  assert.equal(stallHit.closed, true)
  assert.equal(artifactHits().length, 6)

  // A queued selection intent aborts the in-flight read before delivery.
  box.mode = 'gated'
  box.gate = deferred()
  const pendingSelect = assert.rejects(a.host.handle(read(6, REF, va)), /ownership changed/)
  await until(() => artifactHits().length > 6)
  await a.host.handle({ type: 'session.select', requestId: 7, session: RUNTIME, creatureId: 'creature-beta' })
  box.gate.resolve()
  await pendingSelect

  // Disposal aborts the same way; no read in this test ever produced bytes.
  box.mode = 'gated'
  box.gate = deferred()
  const pendingDispose = assert.rejects(b.host.handle(read(15, REF, vb)), /ownership changed|aborted/)
  await flush()
  await until(() => artifactHits().length > 7)
  b.host.dispose()
  box.gate.resolve()
  await pendingDispose

  assert.deepEqual(
    [...a.posts, ...b.posts].filter((message) => message.type === 'artifact.read.result'),
    [],
  )
  assert.equal(artifactHits().length, 8)
  assert.ok(fixture.hits.every((hit) => hit.token === TOKEN))
  a.host.dispose()
  b.host.dispose()
})
