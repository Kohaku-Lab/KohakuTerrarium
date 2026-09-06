// End-to-end paged/detail history through the REAL VS Code webview shim:
// shim (data-URL import) -> installHistoryBridge global -> RuntimeHost ->
// fixed HTTP route. Also pins the live-only saved-history shim (explicit
// unsupported, never a silent no-op) and the async ownership delivery check
// for page/detail reads.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const test = require('node:test')

const { RuntimeHost } = require('../src/host/runtime.cjs')
const { createClient } = require('../src/host/client.cjs')

const root = path.resolve(__dirname, '..')
const TOKEN = 'host-secret'
const RUNTIME = 'graph-live'
const CREATURE = 'beta'
const PAGE = {
  messages: [],
  events: [{ event_id: 9, type: 'assistant' }],
  is_processing: false,
  live_job_ids: [],
  history_page: {
    version: 1,
    stream: 'events',
    history_id: 'hist-1',
    before: null,
    after: 'cursor-newest',
    has_older: true,
    has_newer: false,
    reset_required: false,
  },
}
const SNAPSHOT = {
  messages: [{ role: 'user', content: 'hi' }],
  events: [],
  is_processing: true,
  live_job_ids: ['job-1'],
  history_page: {
    version: 1,
    stream: 'snapshot',
    history_id: 'hist-1',
    before: null,
    after: 'snap-newest',
    has_older: true,
    has_newer: false,
    reset_required: true,
  },
}
const DETAIL = {
  history_page: { version: 1, stream: 'events', history_id: 'hist-1' },
  record: { _history_key: 'k1', content: 'full body' },
}

function json(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json' })
  response.end(JSON.stringify(body))
}

async function startFixture(t) {
  const server = http.createServer((request, response) => {
    if (request.headers['x-kt-host-token'] !== TOKEN) return json(response, 401, { error: 'unauthorized' })
    const url = new URL(request.url, 'http://127.0.0.1')
    const { pathname, searchParams } = url
    if (pathname === '/api/sessions/open') {
      return json(response, 200, [
        {
          conversation_id: 'conv-1',
          runtime_id: RUNTIME,
          saved_name: 'graph_1',
          is_live: true,
          type: 'terrarium',
          title: CREATURE,
          creatures: [{ creature_id: 'creature-beta', name: CREATURE }],
        },
      ])
    }
    if (pathname === `/api/sessions/active/${RUNTIME}`) {
      return json(response, 200, {
        session_id: RUNTIME,
        type: 'terrarium',
        config_name: 'team',
        creatures: [{ creature_id: 'creature-beta', name: CREATURE }],
      })
    }
    if (pathname === `/api/sessions/${RUNTIME}/creatures/${CREATURE}/history` && searchParams.get('paged') === 'true') {
      return json(response, 200, searchParams.get('stream') === 'snapshot' ? SNAPSHOT : PAGE)
    }
    if (pathname === `/api/sessions/${RUNTIME}/creatures/${CREATURE}/history/detail`) {
      return json(response, 200, DETAIL)
    }
    return json(response, 404, { error: 'missing' })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  t.after(async () => {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
  })
  return { origin: `http://127.0.0.1:${server.address().port}` }
}

function makeHost(origin, posts) {
  const listeners = new Map()
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
  const sockets = { begin: () => 1, open: () => {}, send: async () => true, closeSocket: () => true, closeGeneration: () => {} }
  const host = new RuntimeHost({
    client: createClient({ endpoint: origin, token: TOKEN }),
    state,
    sockets,
    post: (message) => {
      posts.push(message)
      const settle = listeners.get(message.requestId)
      if (settle) {
        listeners.delete(message.requestId)
        settle(message)
      }
    },
    getDefaultCreature: () => '@kt-biome/creatures/swe',
    getWorkspacePath: () => 'C:/workspace',
    socketFactory: (url, protocols) => ({ url, protocols }),
    webSocketBase: `ws://${new URL(origin).host}`,
    token: TOKEN,
    runtimeEpoch: 7,
  })
  let nextId = 100
  const request = (type, data = {}) =>
    new Promise((resolve, reject) => {
      const requestId = nextId++
      listeners.set(requestId, (message) => (message.type === 'error' ? reject(Error(message.error)) : resolve(message.data)))
      host.handle({ type, requestId, ...data }).catch(reject)
    })
  return { host, request }
}

async function loadShim() {
  const source = fs.readFileSync(path.join(root, 'src', 'webview', 'shims', 'api.js'), 'utf8')
  const shim = await import(`data:text/javascript,${encodeURIComponent(source)}`)
  const { installHistoryBridge } = await import(pathToFileURL(path.join(root, 'src', 'webview', 'historyBridge.mjs')))
  return { shim, installHistoryBridge }
}

test('real webview shim reaches the host paged/detail routes and keeps responses raw', async (t) => {
  const fixture = await startFixture(t)
  const posts = []
  const { host, request } = makeHost(fixture.origin, posts)
  await host.handle({ type: 'session.select', requestId: 1, session: RUNTIME, creatureId: 'creature-beta' })

  const { shim, installHistoryBridge } = await loadShim()
  const uninstall = installHistoryBridge({ request })
  try {
    const page = await shim.terrariumAPI.getHistoryPage(RUNTIME, CREATURE, { limit: 50, before: 'c1' })
    assert.deepEqual(page, PAGE)
    assert.equal(page.history_page.reset_required, false)

    const snapshot = await shim.terrariumAPI.getHistoryPage(RUNTIME, CREATURE, { stream: 'snapshot' })
    assert.deepEqual(snapshot, SNAPSHOT)
    assert.equal(snapshot.history_page.reset_required, true)
    assert.equal(snapshot.history_page.stream, 'snapshot')
    assert.equal(snapshot.is_processing, true)

    const detail = await shim.terrariumAPI.getHistoryDetail(RUNTIME, CREATURE, {
      stream: 'events',
      ref: 'tok-ok',
      history_id: 'hist-1',
    })
    assert.deepEqual(detail, DETAIL)
    assert.equal(detail.record._history_key, 'k1')
  } finally {
    uninstall()
  }
  host.dispose()
})

test('saved-session shim history fails loudly on the live-only webview', async () => {
  const { shim } = await loadShim()
  assert.throws(() => shim.sessionAPI.getHistoryPage('saved', 'root', { limit: 10 }), /unavailable/)
  assert.throws(() => shim.sessionAPI.getHistoryDetail('saved', 'root', { stream: 'events', ref: 't', history_id: 'h' }), /unavailable/)
})

test('paged/detail delivery is suppressed when readiness or target changes after dispatch', async (t) => {
  const fixture = await startFixture(t)
  const posts = []
  const { host, request } = makeHost(fixture.origin, posts)
  await host.handle({ type: 'session.select', requestId: 1, session: RUNTIME, creatureId: 'creature-beta' })
  const observed = []
  host.artifacts.observe = (data) => observed.push(data)

  // An old-ready switch after dispatch keeps the names but loses the runtime
  // epoch, so the in-flight page must not be delivered.
  let releasePage
  host.client.historyPage = () => new Promise((resolve) => (releasePage = resolve))
  const paged = request('http.historyPage', { session: RUNTIME, creature: CREATURE, options: { limit: 50 } })
  host.runtimeEpoch = 8
  releasePage(PAGE)
  await assert.rejects(paged, /ownership changed/)
  assert.equal(
    posts.some((post) => post.requestId === 100),
    false,
    'no stale page post',
  )
  host.runtimeEpoch = 7

  // A target switch with unchanged names (fresh selection object) must also
  // suppress the in-flight detail and admit no refs.
  await host.handle({ type: 'session.select', requestId: 2, session: RUNTIME, creatureId: 'creature-beta' })
  let releaseDetail
  host.client.historyDetail = () => new Promise((resolve) => (releaseDetail = resolve))
  const detail = request('http.historyDetail', {
    session: RUNTIME,
    creature: CREATURE,
    params: { stream: 'events', ref: 'tok-ok', history_id: 'hist-1' },
  })
  host.state.selection = { ...host.state.selection }
  releaseDetail(DETAIL)
  await assert.rejects(detail, /ownership changed/)
  assert.equal(
    posts.some((post) => post.requestId === 101),
    false,
    'no stale detail post',
  )
  assert.deepEqual(observed, [], 'page/detail refs are not admitted')
  host.dispose()
})
