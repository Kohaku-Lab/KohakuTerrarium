// Real loopback HTTP workflow: RuntimeHost + createClient against a fixture
// server. No fetch mocks; only the routes are scripted. Verifies #305
// paged/detail options are forwarded and response/error metadata survives.
const assert = require('node:assert/strict')
const test = require('node:test')
const http = require('node:http')

const { RuntimeHost } = require('../src/host/runtime.cjs')
const { createClient } = require('../src/host/client.cjs')

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
const DETAIL = {
  history_page: { version: 1, stream: 'events', history_id: 'hist-1' },
  record: { _history_key: 'k1', content: 'full body' },
}

function json(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json' })
  response.end(JSON.stringify(body))
}

async function startFixture(t) {
  const hits = []
  const server = http.createServer((request, response) => {
    hits.push({ url: request.url, token: request.headers['x-kt-host-token'] ?? null })
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
    if (pathname === `/api/sessions/${RUNTIME}/creatures/${CREATURE}/history`) {
      if (searchParams.get('after') === 'gone') return json(response, 404, { detail: 'cursor expired' })
      return json(response, 200, searchParams.get('paged') === 'true' ? PAGE : { events: [] })
    }
    if (pathname === `/api/sessions/${RUNTIME}/creatures/${CREATURE}/history/detail`) {
      const ref = searchParams.get('ref')
      if (ref === 'conflict') return json(response, 409, { detail: 'history changed' })
      if (ref === 'missing') return json(response, 404, { detail: 'record gone' })
      return json(response, 200, DETAIL)
    }
    return json(response, 404, { error: 'missing' })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  t.after(async () => {
    server.closeAllConnections()
    await new Promise((resolve) => server.close(resolve))
  })
  return { hits, origin: `http://127.0.0.1:${server.address().port}` }
}

function harness(base) {
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
    open: () => {},
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
  })
  return { host, posts, state }
}

async function select(host, requestId = 1) {
  await host.handle({ type: 'session.select', requestId, session: RUNTIME, creatureId: 'creature-beta' })
}

test('paged history forwards bounded options and preserves cursor/reset metadata', async (t) => {
  const fixture = await startFixture(t)
  const { host, posts } = harness(fixture.origin)
  await select(host)

  await host.handle({
    type: 'http.historyPage',
    requestId: 2,
    session: RUNTIME,
    creature: CREATURE,
    options: { limit: 50, before: 'c1', history_id: 'hist-1', stream: 'events' },
  })

  const call = fixture.hits.find((hit) => hit.url.includes('/history?'))
  assert.match(call.url, /paged=true/)
  assert.match(call.url, /limit=50/)
  assert.match(call.url, /before=c1/)
  assert.match(call.url, /history_id=hist-1/)
  assert.match(call.url, /stream=events/)
  assert.equal(call.token, TOKEN)
  assert.deepEqual(posts.at(-1), { type: 'http.historyPage.result', requestId: 2, data: PAGE })
  host.dispose()
})

test('detail fetches the opaque token triple and stays on the fixed detail route', async (t) => {
  const fixture = await startFixture(t)
  const { host, posts } = harness(fixture.origin)
  await select(host)

  await host.handle({
    type: 'http.historyDetail',
    requestId: 3,
    session: RUNTIME,
    creature: CREATURE,
    params: { stream: 'events', ref: 'tok-ok', history_id: 'hist-1' },
  })

  const call = fixture.hits.find((hit) => hit.url.includes('/history/detail'))
  assert.match(call.url, /stream=events/)
  assert.match(call.url, /ref=tok-ok/)
  assert.match(call.url, /history_id=hist-1/)
  assert.deepEqual(posts.at(-1), { type: 'http.historyDetail.result', requestId: 3, data: DETAIL })
  host.dispose()
})

test('history transport rejects malformed options before any HTTP or delivery', async (t) => {
  const fixture = await startFixture(t)
  const { host, posts, state } = harness(fixture.origin)
  await select(host)
  const before = fixture.hits.length

  for (const options of [{ limit: 0 }, { limit: '400' }, { url: '/etc' }, { before: 'a', after: 'b' }, { stream: 'secret' }]) {
    await assert.rejects(
      host.handle({ type: 'http.historyPage', requestId: 4, session: RUNTIME, creature: CREATURE, options }),
      /Invalid history page request/,
    )
  }
  await assert.rejects(
    host.handle({ type: 'http.historyDetail', requestId: 5, session: RUNTIME, creature: CREATURE, params: { stream: 'events', ref: 't' } }),
    /Invalid history detail request/,
  )
  assert.equal(fixture.hits.length, before, 'no malformed request reached the network')
  assert.equal(
    posts.some((post) => post.type.endsWith('.result') && post.requestId >= 4),
    false,
  )
  assert.notEqual(state.selection, null)
  host.dispose()
})

test('history transport refuses a stale selection and surfaces 409/404 status', async (t) => {
  const fixture = await startFixture(t)
  const { host } = harness(fixture.origin)
  await select(host)

  // A stale selection (cleared ownership) is rejected before the fetch.
  host.state.selection = null
  await assert.rejects(
    host.handle({ type: 'http.historyPage', requestId: 6, session: RUNTIME, creature: CREATURE, options: {} }),
    /ownership changed/,
  )
  await select(host, 7)

  await assert.rejects(
    host.handle({
      type: 'http.historyDetail',
      requestId: 8,
      session: RUNTIME,
      creature: CREATURE,
      params: { stream: 'events', ref: 'conflict', history_id: 'hist-1' },
    }),
    (error) => {
      assert.equal(error.status, 409)
      return true
    },
  )
  await assert.rejects(
    host.handle({
      type: 'http.historyDetail',
      requestId: 9,
      session: RUNTIME,
      creature: CREATURE,
      params: { stream: 'events', ref: 'missing', history_id: 'hist-1' },
    }),
    (error) => {
      assert.equal(error.status, 404)
      return true
    },
  )
  await assert.rejects(
    host.handle({ type: 'http.historyPage', requestId: 10, session: RUNTIME, creature: CREATURE, options: { after: 'gone' } }),
    (error) => {
      assert.equal(error.status, 404)
      return true
    },
  )
  host.dispose()
})
